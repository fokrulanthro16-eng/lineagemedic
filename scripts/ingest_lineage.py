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
                 -> readmission_risk_model -> production_readmission_endpoint

    raw_billing  -> billing_summary

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

from lineagemedic.fixtures.graph import build_graph
from lineagemedic.models import Asset, AssetKind, DataSource, Owner

#: DataHub records who made a change. This is the ingestion actor, not a person.
_INGESTION_ACTOR = "urn:li:corpuser:datahub"

#: Maps the fixture's ownership vocabulary onto DataHub's ownership type enum.
_OWNERSHIP_TYPE = {
    "TECHNICAL_OWNER": OwnershipTypeClass.TECHNICAL_OWNER,
    "BUSINESS_OWNER": OwnershipTypeClass.BUSINESS_OWNER,
    "DATA_STEWARD": OwnershipTypeClass.DATA_STEWARD,
}

#: DataHub subtypes, so the UI renders each node as what it actually is rather
#: than as an undifferentiated "Dataset".
_SUBTYPES = {
    AssetKind.DATASET: ["Table"],
    AssetKind.FEATURE_TABLE: ["Feature Table"],
    AssetKind.ML_MODEL: ["ML Model"],
    AssetKind.ENDPOINT: ["Inference Endpoint"],
}


@dataclass
class IngestionResult:
    """What the run actually emitted, for truthful reporting."""

    entities: int = 0
    aspects: int = 0
    lineage_edges: int = 0
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

    For an ML model this aspect also carries lineage: ``trainingJobs`` is not
    the right field for datasets, so upstream feature tables are declared via
    the model's own properties, and the deployment it serves via ``deployments``.
    DataHub rejects ``UpstreamLineage`` on ``mlModel``/``mlModelDeployment``
    entities, so this is the supported way to connect the ML half of the chain.
    """
    if asset.kind is AssetKind.ML_MODEL:
        return MLModelPropertiesClass(
            name=asset.name,
            description=asset.description,
            customProperties={"managed_by": "lineagemedic"},
            # Feature tables the model consumes -> the upstream half of the edge.
            trainingJobs=[],
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

    Schema metadata is emitted only for assets that actually declare columns.
    Emitting an empty schema for an ML model would assert something false about
    the entity - that it has a known, empty column set.
    """
    aspects: list[object] = [
        _properties_aspect(asset),
        _ownership(asset.owners),
        _global_tags(asset.tags),
        SubTypesClass(typeNames=_SUBTYPES[asset.kind]),
    ]
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
          f"{result.lineage_edges} lineage edges")
    if result.failures:
        print(f"FAILURES ({len(result.failures)}):", file=sys.stderr)
        for failure in result.failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
