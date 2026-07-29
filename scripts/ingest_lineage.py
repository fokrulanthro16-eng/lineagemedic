"""Ingest the LineageMedic asset graph into a live DataHub instance.

This is the bridge between the committed fixture graph and a real catalog. It
reads :mod:`lineagemedic.fixtures.graph` - the single source of truth for URNs,
schemas, ownership, and lineage edges - and emits the equivalent aspects to
DataHub over the REST sink.

Nothing here is invented. Every URN, column, owner, tag, and edge is taken from
the fixture module, so the graph a live adapter traverses is structurally
identical to the one the unit tests exercise. That is what makes the DataHub
phase an *integration* rather than a second, divergent definition of the world.

Two branches are ingested:

    raw_patients -> staging_patients -> patient_features
                 -> [train_readmission_model] -> [serve_readmission_endpoint]

    raw_billing  -> billing_summary

The two bracketed nodes are ``dataJob`` entities. The ``mlModel`` and
``mlModelDeployment`` entities are also ingested with their full metadata, but
DataHub v1.6.0 cannot return either type from a lineage traversal, so the jobs
that produce them carry the reachability. :func:`_ml_bridge_mcps` documents the
evidence for that constraint.

The billing branch is deliberately kept free of any lineage edge to the patient
branch. The Impact Agent must be able to examine it and clear it; if ingestion
accidentally connected the two, selective containment would be untestable.

Run against a running quickstart::

    python scripts/ingest_lineage.py --gms http://localhost:8080

The script is idempotent: DataHub aspects are upserts keyed by URN, so running
it twice converges on the same state rather than duplicating anything.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass

from datahub.emitter.mce_builder import make_tag_urn
from datahub.emitter.mcp import MetadataChangeProposalWrapper
from datahub.emitter.rest_emitter import DatahubRestEmitter
from datahub.metadata.schema_classes import (
    AuditStampClass,
    DataFlowInfoClass,
    DataJobInfoClass,
    DataJobInputOutputClass,
    DatasetLineageTypeClass,
    DatasetPropertiesClass,
    GlobalTagsClass,
    MLModelDeploymentPropertiesClass,
    MLModelPropertiesClass,
    OtherSchemaClass,
    OwnerClass,
    OwnershipClass,
    OwnershipTypeClass,
    SchemaFieldClass,
    SchemaFieldDataTypeClass,
    SchemaMetadataClass,
    StringTypeClass,
    SubTypesClass,
    TagAssociationClass,
    UpstreamClass,
    UpstreamLineageClass,
)

from lineagemedic.fixtures.graph import (
    PLATFORM_MLFLOW,
    PLATFORM_SAGEMAKER,
    URN_PATIENT_FEATURES,
    build_graph,
    dataset_urn,
)
from lineagemedic.models import Asset, AssetKind, DataSource, Owner

#: DataHub records who made a change. This is the ingestion actor, not a person.
_INGESTION_ACTOR = "urn:li:corpuser:datahub"

#: Output datasets of the two ML jobs. They exist in DataHub but not in the
#: fixture graph, because the fixture graph collapses each job into the artifact
#: it produces. See :func:`_ml_bridge_mcps` for why the live catalog cannot.
URN_MODEL_PREDICTIONS = dataset_urn("model_predictions", PLATFORM_MLFLOW)
URN_ENDPOINT_PREDICTIONS = dataset_urn("endpoint_predictions", PLATFORM_SAGEMAKER)

#: Maps the fixture's ownership vocabulary onto DataHub's ownership type enum.
_OWNERSHIP_TYPE = {
    "TECHNICAL_OWNER": OwnershipTypeClass.TECHNICAL_OWNER,
    "BUSINESS_OWNER": OwnershipTypeClass.BUSINESS_OWNER,
    "DATA_STEWARD": OwnershipTypeClass.DATA_STEWARD,
}

#: DataHub subtypes, so the UI renders each node as what it actually is rather
#: than as an undifferentiated "Dataset".
#:
#: :data:`AssetKind.ENDPOINT` is deliberately absent. DataHub's entity registry
#: does not define a ``subTypes`` aspect for ``mlModelDeployment`` and rejects
#: the emit with HTTP 422 ("Unknown aspect subTypes for entity
#: mlModelDeployment"). A deployment is already unambiguous in the UI by virtue
#: of its entity type, so there is nothing to disambiguate and no reason to
#: force the aspect through.
_SUBTYPES = {
    AssetKind.DATASET: ["Table"],
    AssetKind.FEATURE_TABLE: ["Feature Table"],
    AssetKind.ML_MODEL: ["ML Model"],
}


@dataclass
class IngestionResult:
    """What the run actually emitted, for truthful reporting."""

    entities: int = 0
    aspects: int = 0
    lineage_edges: int = 0
    #: Aspects emitted for the datajob bridge; see :func:`_ml_bridge_mcps`.
    bridge_aspects: int = 0
    failures: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.failures is None:
            self.failures = []


def _audit_stamp() -> AuditStampClass:
    """A DataHub audit stamp for the current emission."""
    return AuditStampClass(time=0, actor=_INGESTION_ACTOR)


def _ownership(owners: list[Owner]) -> OwnershipClass:
    """Translate fixture owners into a DataHub Ownership aspect."""
    return OwnershipClass(
        owners=[
            OwnerClass(
                owner=o.urn,
                type=_OWNERSHIP_TYPE.get(o.type, OwnershipTypeClass.TECHNICAL_OWNER),
            )
            for o in owners
        ],
        lastModified=_audit_stamp(),
    )


def _global_tags(tags: list[str]) -> GlobalTagsClass:
    """Translate plain tag strings into DataHub tag associations."""
    return GlobalTagsClass(
        tags=[TagAssociationClass(tag=make_tag_urn(t)) for t in tags]
    )


def _schema_metadata(asset: Asset) -> SchemaMetadataClass:
    """Build a SchemaMetadata aspect from the fixture's column definitions.

    Native types are carried through verbatim so the catalog reports the
    warehouse's own vocabulary (``TEXT``, ``INTEGER``) rather than a
    lossy normalisation. ``StringTypeClass`` is used as the logical type for
    every field because the demo warehouse is SQLite, whose dynamic typing makes
    a more specific logical claim unsupportable.
    """
    return SchemaMetadataClass(
        schemaName=asset.name,
        platform=f"urn:li:dataPlatform:{asset.platform}",
        version=0,
        hash="",
        platformSchema=OtherSchemaClass(rawSchema=""),
        fields=[
            SchemaFieldClass(
                fieldPath=field.name,
                type=SchemaFieldDataTypeClass(type=StringTypeClass()),
                nativeDataType=field.native_type,
                nullable=field.nullable,
                description=field.description,
            )
            for field in asset.schema_fields
        ],
        lastModified=_audit_stamp(),
    )


def _entity_type(asset: Asset) -> str:
    """The DataHub entity type backing one asset kind."""
    if asset.kind is AssetKind.ML_MODEL:
        return "mlModel"
    if asset.kind is AssetKind.ENDPOINT:
        return "mlModelDeployment"
    return "dataset"


def _properties_aspect(asset: Asset) -> object:
    """The type-appropriate properties aspect carrying name and description.

    The ML model's ``deployments`` field records the endpoint it serves. Note
    that this records the relationship but does *not* create a traversable
    lineage edge; see :func:`_ml_bridge_mcps` for why, and for what does.
    """
    if asset.kind is AssetKind.ML_MODEL:
        return MLModelPropertiesClass(
            name=asset.name,
            description=asset.description,
            customProperties={"managed_by": "lineagemedic"},
            deployments=list(asset.downstreams),
        )
    if asset.kind is AssetKind.ENDPOINT:
        return MLModelDeploymentPropertiesClass(
            description=asset.description,
            customProperties={"managed_by": "lineagemedic"},
        )
    return DatasetPropertiesClass(
        name=asset.name,
        description=asset.description,
        customProperties={"managed_by": "lineagemedic"},
    )


def _aspects_for(asset: Asset) -> list[object]:
    """Every aspect this asset should carry in DataHub.

    Two aspects are conditional. Schema metadata is emitted only for assets that
    actually declare columns, because an empty schema on an ML model would
    assert something false - that it has a known, empty column set. Subtypes are
    emitted only for entity types whose registry defines the aspect; see
    :data:`_SUBTYPES`.
    """
    aspects: list[object] = [
        _properties_aspect(asset),
        _ownership(asset.owners),
        _global_tags(asset.tags),
    ]
    subtypes = _SUBTYPES.get(asset.kind)
    if subtypes is not None:
        aspects.append(SubTypesClass(typeNames=subtypes))
    if asset.schema_fields:
        aspects.append(_schema_metadata(asset))
    return aspects


def _lineage_aspect(asset: Asset) -> UpstreamLineageClass | None:
    """Upstream lineage for one asset, or ``None`` if it is a source.

    Only emitted for dataset-like entities. Model and endpoint lineage in
    DataHub is expressed through their own properties aspects, which is handled
    separately by :func:`_ml_lineage_aspect`.
    """
    if not asset.upstreams:
        return None
    return UpstreamLineageClass(
        upstreams=[
            UpstreamClass(dataset=up, type=DatasetLineageTypeClass.TRANSFORMED)
            for up in asset.upstreams
        ]
    )


def _ml_bridge_mcps(graph: object) -> list[MetadataChangeProposalWrapper]:
    """Datajob nodes that make the ML half of the chain traversable.

    Why this exists
    ---------------
    LineageMedic's severity reasoning depends on a blast radius that reaches the
    deployed model and the production endpoint. In DataHub v1.6.0, ``mlModel``
    and ``mlModelDeployment`` cannot participate in a lineage traversal at all.
    That was established against the running instance, not assumed:

    * ``upstreamLineage`` on ``mlModel`` is rejected with HTTP 422, "Unknown
      aspect upstreamLineage for entity mlModel".
    * ``subTypes`` on ``mlModelDeployment`` is rejected with HTTP 422.
    * ``MLModelDeployment`` is not a type in the GraphQL schema, and
      ``MLMODEL_DEPLOYMENT`` is not a member of the ``EntityType`` enum, so the
      entity cannot be returned by ``searchAcrossLineage`` under any query.
    * ``MLModelProperties.deployments`` and the ``trainingData`` aspect are both
      accepted and stored, but neither yields a lineage edge:
      ``searchAcrossLineage`` from ``patient_features`` returned ``total: 0``
      after each was emitted.

    ``dataJob`` does traverse, so the two real process steps in this pipeline
    are modelled as the jobs they actually are, each with the dataset it
    produces:

        patient_features   -> [train_readmission_model]      -> model_predictions
        model_predictions  -> [serve_readmission_endpoint]   -> endpoint_predictions

    with the datasets additionally linked directly to each other::

        patient_features -> model_predictions -> endpoint_predictions

    Two further constraints, both established by probing rather than assumed.

    A ``dataJob`` is indexed into the lineage graph only when it declares
    **both** inputs and outputs. Emitting a job with an empty ``outputDatasets``
    was accepted by GMS and the entity was retrievable by URN, but it produced
    no edge. Hence each job below names a real output dataset.

    More importantly, ``searchAcrossLineage`` in the DOWNSTREAM direction does
    not follow dataset-to-job edges. A dataset consumed by a job is joined by a
    ``Consumes`` relationship, whereas dataset-to-dataset lineage uses
    ``DownstreamOf``; only the latter is traversed. Concretely, on this
    instance:

    * ``entity(patient_features).lineage(DOWNSTREAM)`` returns the training job
      via ``Consumes`` - the edge is genuinely in the graph.
    * ``searchAcrossLineage(patient_features, DOWNSTREAM)`` returns ``total:
      0``, even with ``types: [DATA_JOB, DATASET]`` and after the search index
      has caught up (all 11 entities are searchable).
    * ``searchAcrossLineage(train_job, DOWNSTREAM)`` works, reaching
      ``endpoint_predictions`` at degree 2.

    So the break is exactly one hop: dataset -> job. Because the adapter's blast
    radius is a DOWNSTREAM traversal, the jobs alone would leave the model and
    endpoint unreachable from an incident on a patient table. Each bridge output
    dataset therefore *also* carries an ``UpstreamLineage`` aspect naming the
    dataset upstream of its job, which creates the ``DownstreamOf`` edge that
    DOWNSTREAM traversal does follow. The job edges are kept as well: they are
    the accurate description of what produces what, and they make the pipeline
    render correctly in the DataHub UI.

    This is a faithful representation rather than a workaround. Training a model
    and serving an endpoint *are* jobs, and each does emit a table of scores;
    the fixture graph collapses each job into the artifact it produces, which is
    the right abstraction for the incident UI but not one DataHub's lineage
    graph shares.

    The ``mlModel`` and ``mlModelDeployment`` entities are still ingested, with
    their real descriptions, owners, and tags. They remain the catalog's record
    of the model and the endpoint; these jobs carry the reachability.
    """
    flow_urn = f"urn:li:dataFlow:({PLATFORM_MLFLOW},lineagemedic.readmission_pipeline,PROD)"
    train_urn = f"urn:li:dataJob:({flow_urn},train_readmission_model)"
    serve_urn = f"urn:li:dataJob:({flow_urn},serve_readmission_endpoint)"

    return [
        # The datasets each job emits. These are the scores the model and the
        # endpoint actually produce, and they are what makes each job
        # traversable.
        MetadataChangeProposalWrapper(
            entityType="dataset",
            entityUrn=URN_MODEL_PREDICTIONS,
            aspect=DatasetPropertiesClass(
                name="model_predictions",
                description=(
                    "Batch readmission risk scores written by "
                    "readmission_risk_model."
                ),
                customProperties={"managed_by": "lineagemedic"},
            ),
        ),
        MetadataChangeProposalWrapper(
            entityType="dataset",
            entityUrn=URN_ENDPOINT_PREDICTIONS,
            aspect=DatasetPropertiesClass(
                name="endpoint_predictions",
                description=(
                    "Live readmission risk scores served by "
                    "production_readmission_endpoint. Clinician-facing."
                ),
                customProperties={"managed_by": "lineagemedic"},
            ),
        ),
        MetadataChangeProposalWrapper(
            entityType="dataFlow",
            entityUrn=flow_urn,
            aspect=DataFlowInfoClass(
                name="readmission_pipeline",
                description=(
                    "Trains the 30-day readmission risk model from patient "
                    "features and serves it behind the production endpoint."
                ),
                customProperties={"managed_by": "lineagemedic"},
            ),
        ),
        MetadataChangeProposalWrapper(
            entityType="dataJob",
            entityUrn=train_urn,
            aspect=DataJobInfoClass(
                name="train_readmission_model",
                type="BATCH_SCHEDULED",
                description=(
                    "Trains readmission_risk_model on patient_features. "
                    "Corresponds to the mlModel entity of the same name."
                ),
            ),
        ),
        MetadataChangeProposalWrapper(
            entityType="dataJob",
            entityUrn=train_urn,
            aspect=DataJobInputOutputClass(
                inputDatasets=[URN_PATIENT_FEATURES],
                outputDatasets=[URN_MODEL_PREDICTIONS],
            ),
        ),
        MetadataChangeProposalWrapper(
            entityType="dataJob",
            entityUrn=serve_urn,
            aspect=DataJobInfoClass(
                name="serve_readmission_endpoint",
                type="BATCH_SCHEDULED",
                description=(
                    "Serves readmission_risk_model as "
                    "production_readmission_endpoint. Corresponds to the "
                    "mlModelDeployment entity of the same name."
                ),
            ),
        ),
        MetadataChangeProposalWrapper(
            entityType="dataJob",
            entityUrn=serve_urn,
            # Consumes the training job's output, so the endpoint sits strictly
            # downstream of the model rather than beside it.
            aspect=DataJobInputOutputClass(
                inputDatasets=[URN_MODEL_PREDICTIONS],
                inputDatajobs=[train_urn],
                outputDatasets=[URN_ENDPOINT_PREDICTIONS],
            ),
        ),
        # Dataset-to-dataset lineage mirroring each job, so that a DOWNSTREAM
        # traversal - which does not follow ``Consumes`` - still reaches the ML
        # half of the chain. See this function's docstring.
        MetadataChangeProposalWrapper(
            entityType="dataset",
            entityUrn=URN_MODEL_PREDICTIONS,
            aspect=UpstreamLineageClass(
                upstreams=[
                    UpstreamClass(
                        dataset=URN_PATIENT_FEATURES,
                        type=DatasetLineageTypeClass.TRANSFORMED,
                    )
                ]
            ),
        ),
        MetadataChangeProposalWrapper(
            entityType="dataset",
            entityUrn=URN_ENDPOINT_PREDICTIONS,
            aspect=UpstreamLineageClass(
                upstreams=[
                    UpstreamClass(
                        dataset=URN_MODEL_PREDICTIONS,
                        type=DatasetLineageTypeClass.TRANSFORMED,
                    )
                ]
            ),
        ),
    ]


def emit_graph(emitter: DatahubRestEmitter, *, dry_run: bool = False) -> IngestionResult:
    """Emit every asset and lineage edge, reporting exactly what succeeded."""
    graph = build_graph(source=DataSource.FIXTURE)
    result = IngestionResult()

    for asset in graph.assets:
        entity_type = _entity_type(asset)
        for aspect in _aspects_for(asset):
            mcp = MetadataChangeProposalWrapper(
                entityType=entity_type,
                entityUrn=asset.urn,
                aspect=aspect,  # type: ignore[arg-type]
            )
            if dry_run:
                result.aspects += 1
                continue
            try:
                emitter.emit(mcp)
                result.aspects += 1
            except Exception as exc:  # reported in the result, never swallowed
                result.failures.append(f"{asset.name} {type(aspect).__name__}: {exc}")
        result.entities += 1

    # Lineage is emitted after every entity exists, so no edge can point at an
    # entity DataHub has not seen yet.
    for asset in graph.assets:
        lineage = _lineage_aspect(asset)
        if lineage is None or _entity_type(asset) != "dataset":
            continue
        mcp = MetadataChangeProposalWrapper(
            entityType="dataset", entityUrn=asset.urn, aspect=lineage
        )
        if dry_run:
            result.lineage_edges += len(asset.upstreams)
            continue
        try:
            emitter.emit(mcp)
            result.lineage_edges += len(asset.upstreams)
        except Exception as exc:
            result.failures.append(f"{asset.name} UpstreamLineage: {exc}")

    # The datajob bridge that makes the ML half of the chain traversable. Last,
    # for the same reason lineage follows entities: it references the feature
    # table, which must already exist.
    for mcp in _ml_bridge_mcps(graph):
        if dry_run:
            result.bridge_aspects += 1
            continue
        try:
            emitter.emit(mcp)
            result.bridge_aspects += 1
        except Exception as exc:
            result.failures.append(f"ml-bridge {mcp.entityUrn}: {exc}")

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--gms",
        default="http://localhost:8080",
        help="DataHub GMS URL (default: http://localhost:8080)",
    )
    parser.add_argument("--token", default="", help="DataHub personal access token, if required.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count what would be emitted without contacting DataHub.",
    )
    args = parser.parse_args()

    emitter = DatahubRestEmitter(gms_server=args.gms, token=args.token or None)

    if not args.dry_run:
        try:
            emitter.test_connection()
        except Exception as exc:
            print(f"FAILED: cannot reach DataHub GMS at {args.gms}: {exc}", file=sys.stderr)
            return 2

    result = emit_graph(emitter, dry_run=args.dry_run)

    label = "WOULD EMIT" if args.dry_run else "EMITTED"
    print(f"{label}: {result.entities} entities, {result.aspects} aspects, "
          f"{result.lineage_edges} lineage edges, "
          f"{result.bridge_aspects} ml-bridge aspects")
    if result.failures:
        print(f"FAILURES ({len(result.failures)}):", file=sys.stderr)
        for failure in result.failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
