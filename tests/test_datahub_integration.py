"""Integration tests against a real, running DataHub instance.

Every test here is skipped unless a DataHub GMS actually answers. That is
deliberate and is the honest arrangement: a suite that silently passed without
DataHub would be asserting nothing while looking like coverage.

Run them by starting the quickstart and ingesting the graph::

    docker compose -p datahub -f ~/.datahub/quickstart/docker-compose.yml up -d
    python scripts/ingest_lineage.py --gms http://localhost:8080
    pytest tests/test_datahub_integration.py -v

Set ``DATAHUB_GMS_URL`` to point somewhere other than ``http://localhost:8080``.

What these tests are for
------------------------

The unit suite proves the workflow reasons correctly over *a* graph. These
prove the live adapters return a graph with the same shape as the fixture one,
so the reasoning that was verified against fixtures remains valid against
DataHub. The two most important cases:

*   :func:`test_live_lineage_reaches_the_production_endpoint` - the patient
    chain is traversable end to end. If DataHub returned a truncated graph, the
    critical scenario would silently downgrade to a warning.
*   :func:`test_billing_branch_is_not_connected_to_the_patient_branch` - the
    control branch is genuinely separate in the live catalog, which is what
    makes selective containment a real result rather than an artifact of the
    fixture file.
"""

from __future__ import annotations

import os

import httpx
import pytest

from lineagemedic.adapters.base import AdapterError
from lineagemedic.adapters.datahub_mcp import DataHubMetadataAdapter
from lineagemedic.adapters.datahub_sdk import DataHubWritebackAdapter
from lineagemedic.fixtures.graph import (
    URN_BILLING_SUMMARY,
    URN_PATIENT_FEATURES,
    URN_RAW_BILLING,
    URN_RAW_PATIENTS,
    URN_READMISSION_MODEL,
    URN_STAGING_PATIENTS,
)
from lineagemedic.models import AssetKind, DataSource, WritebackStatus

GMS_URL = os.environ.get("DATAHUB_GMS_URL", "http://localhost:8080").rstrip("/")
FRONTEND_URL = os.environ.get("DATAHUB_FRONTEND_URL", "http://localhost:9002").rstrip("/")
TOKEN = os.environ.get("DATAHUB_GMS_TOKEN", "")

#: Outputs of the two ML jobs, which is how the live catalog represents the
#: model and the endpoint in its lineage graph. DataHub v1.6.0 cannot return
#: ``mlModel`` or ``mlModelDeployment`` entities from a lineage traversal - see
#: ``_ml_bridge_mcps`` in ``scripts/ingest_lineage.py`` for the evidence - so
#: these datasets, not the ML entities, are what the blast radius must reach.
URN_MODEL_PREDICTIONS = (
    "urn:li:dataset:(urn:li:dataPlatform:mlflow,lineagemedic.model_predictions,PROD)"
)
URN_ENDPOINT_PREDICTIONS = (
    "urn:li:dataset:(urn:li:dataPlatform:sagemaker,"
    "lineagemedic.endpoint_predictions,PROD)"
)

#: The patient chain as it exists in the live catalog, in dependency order.
PATIENT_CHAIN = [
    URN_RAW_PATIENTS,
    URN_STAGING_PATIENTS,
    URN_PATIENT_FEATURES,
    URN_MODEL_PREDICTIONS,
    URN_ENDPOINT_PREDICTIONS,
]


def _datahub_is_up() -> bool:
    """True only if GMS answers its health endpoint. Never raises."""
    try:
        return httpx.get(f"{GMS_URL}/health", timeout=3.0).status_code < 400
    except httpx.HTTPError:
        return False


#: Applied to every test in this module. The reason string names the exact
#: command that makes them run, so a skip is actionable rather than mysterious.
requires_datahub = pytest.mark.skipif(
    not _datahub_is_up(),
    reason=(
        f"No DataHub GMS at {GMS_URL}. Start it with the quickstart compose file "
        "and run scripts/ingest_lineage.py to populate the graph."
    ),
)

pytestmark = requires_datahub


@pytest.fixture()
def adapter() -> DataHubMetadataAdapter:
    return DataHubMetadataAdapter(
        gms_url=GMS_URL, frontend_url=FRONTEND_URL, token=TOKEN
    )


def test_health_reports_a_real_connection(adapter: DataHubMetadataAdapter) -> None:
    reachable, detail = adapter.health()

    assert reachable is True
    assert GMS_URL in detail


def test_every_ingested_asset_is_retrievable(adapter: DataHubMetadataAdapter) -> None:
    """All seven assets exist in the live catalog with live provenance."""
    for urn in [*PATIENT_CHAIN, URN_RAW_BILLING, URN_BILLING_SUMMARY]:
        asset = adapter.get_asset(urn)

        assert asset.urn == urn
        assert asset.source is DataSource.LIVE_DATAHUB
        assert asset.name, f"{urn} came back without a name"


def test_unknown_urn_raises_rather_than_returning_empty(
    adapter: DataHubMetadataAdapter,
) -> None:
    """A missing asset must be distinguishable from an unreachable catalog."""
    missing = (
        "urn:li:dataset:(urn:li:dataPlatform:sqlite,"
        "lineagemedic.no_such_table_exists,PROD)"
    )
    with pytest.raises(AdapterError):
        adapter.get_asset(missing)


def test_schemas_owners_and_tags_survive_the_round_trip(
    adapter: DataHubMetadataAdapter,
) -> None:
    """The metadata the ingestion script wrote is really in DataHub.

    Asserted on raw_patients because it carries all four kinds of metadata this
    project depends on: columns, ownership, tags, and a description.
    """
    asset = adapter.get_asset(URN_RAW_PATIENTS)

    field_names = {f.name for f in asset.schema_fields}
    assert {"patient_id", "age", "admission_date"} <= field_names
    assert asset.description and "EHR" in asset.description
    assert {"healthcare", "phi"} <= set(asset.tags)
    assert asset.owners, "raw_patients came back with no ownership"


def test_live_lineage_reaches_the_production_endpoint(
    adapter: DataHubMetadataAdapter,
) -> None:
    """The full four-hop patient chain is traversable from the anchor.

    This is the single most important integration assertion: the critical
    scenario's severity depends on the blast radius reaching the model's output
    and the production endpoint's output. A truncated graph would downgrade a
    critical incident to a warning without any error being raised.
    """
    graph = adapter.get_lineage(URN_RAW_PATIENTS)
    urns = {a.urn for a in graph.assets}

    assert graph.source is DataSource.LIVE_DATAHUB
    for expected in PATIENT_CHAIN:
        assert expected in urns, f"{expected} missing from live lineage"

    reachable = graph.downstream_closure(URN_RAW_PATIENTS)
    assert URN_ENDPOINT_PREDICTIONS in reachable
    assert URN_MODEL_PREDICTIONS in reachable


def test_billing_branch_is_not_connected_to_the_patient_branch(
    adapter: DataHubMetadataAdapter,
) -> None:
    """Selective containment must be a real property of the live graph.

    If ingestion accidentally linked the branches, the Impact Agent would
    quarantine billing during a patient incident and the demo's central claim
    would be false.
    """
    graph = adapter.get_lineage(URN_RAW_BILLING)
    reachable = set(graph.downstream_closure(URN_RAW_BILLING))

    assert URN_BILLING_SUMMARY in reachable
    for patient_urn in PATIENT_CHAIN:
        assert patient_urn not in reachable, (
            f"{patient_urn} is reachable from the billing branch; the two "
            "branches must stay independent"
        )


def test_entity_kinds_are_classified_from_datahub(
    adapter: DataHubMetadataAdapter,
) -> None:
    """Models and endpoints must not be flattened into plain datasets.

    The bridge entities are the ones that matter here. To DataHub they are an
    ordinary ``DATASET`` and ``DATA_JOB``; only their emitted subtype says one
    is a model's output and the other an endpoint's. Classifying them on entity
    type alone reported zero models and zero endpoints in the blast radius,
    which silently downgraded the critical scenario to a warning - a wrong
    answer with no error raised. See ``test_critical_blast_radius_is_severe``.
    """
    assert adapter.get_asset(URN_READMISSION_MODEL).kind is AssetKind.ML_MODEL
    assert adapter.get_asset(URN_RAW_PATIENTS).kind is AssetKind.DATASET
    assert adapter.get_asset(URN_MODEL_PREDICTIONS).kind is AssetKind.ML_MODEL
    assert adapter.get_asset(URN_ENDPOINT_PREDICTIONS).kind is AssetKind.ENDPOINT


def test_critical_blast_radius_reaches_a_model_and_an_endpoint(
    adapter: DataHubMetadataAdapter,
) -> None:
    """Severity is derived, so the kinds in the blast radius decide it.

    Reaching the right assets is not enough: the Impact Agent counts affected
    ML models and production endpoints, and a chain that traverses correctly
    but classifies every node as a dataset yields zero of each. That is what
    made the critical scenario report ``warning`` against a live instance while
    every traversal assertion still passed.
    """
    graph = adapter.get_lineage(URN_RAW_PATIENTS)
    reachable = set(graph.downstream_closure(URN_RAW_PATIENTS))
    kinds = {a.kind for a in graph.assets if a.urn in reachable}

    assert AssetKind.ML_MODEL in kinds, (
        "no ML model in the blast radius; a critical incident would be "
        "reported as a warning"
    )
    assert AssetKind.ENDPOINT in kinds, (
        "no production endpoint in the blast radius; a critical incident "
        "would be reported as a warning"
    )


def test_calls_are_recorded_with_live_provenance(
    adapter: DataHubMetadataAdapter,
) -> None:
    """Every read is auditable and labelled as live, not fixture."""
    adapter.get_asset(URN_RAW_PATIENTS)
    adapter.get_lineage(URN_RAW_PATIENTS)

    calls = adapter.drain_calls()

    assert len(calls) >= 2
    assert all(c.source is DataSource.LIVE_DATAHUB for c in calls)
    assert all(c.ok for c in calls)
    assert {"get_dataset", "get_lineage"} <= {c.tool for c in calls}
    # Draining is destructive, so a second drain must be empty.
    assert adapter.drain_calls() == []


def test_failed_calls_are_still_recorded(adapter: DataHubMetadataAdapter) -> None:
    """A failure must appear in the trace, not vanish from it."""
    with pytest.raises(AdapterError):
        adapter.get_asset(
            "urn:li:dataset:(urn:li:dataPlatform:sqlite,lineagemedic.absent,PROD)"
        )

    calls = adapter.drain_calls()

    assert len(calls) == 1
    assert calls[0].ok is False
    assert calls[0].error


# ---------------------------------------------------------------------------
# Writeback: the mutating half, gated on approval and verified by read-back
# ---------------------------------------------------------------------------


@pytest.fixture()
def writeback_adapter() -> DataHubWritebackAdapter:
    return DataHubWritebackAdapter(
        gms_url=GMS_URL, frontend_url=FRONTEND_URL, token=TOKEN
    )


def test_unapproved_writeback_mutates_nothing(
    writeback_adapter: DataHubWritebackAdapter,
    adapter: DataHubMetadataAdapter,
) -> None:
    """The approval gate holds against a live catalog, not just in unit tests.

    The assertion that matters is the second one: after a blocked call, the tag
    must be genuinely absent from DataHub. A receipt saying "blocked" while the
    write actually happened would be the worst possible outcome.
    """
    tag = "lineagemedic-unapproved-probe"

    receipt = writeback_adapter.write_incident_metadata(
        target_urns=[URN_STAGING_PATIENTS],
        tags=[tag],
        note="This must never be written.",
        incident_id="LM-GATE-TEST",
        approved=False,
    )

    assert receipt.status is WritebackStatus.BLOCKED_PENDING_APPROVAL
    assert receipt.tags_added == []

    present, missing = writeback_adapter.verify_tags(URN_STAGING_PATIENTS, [tag])
    assert present is False, "a blocked writeback still reached DataHub"
    assert missing == [tag]


def test_approved_writeback_is_applied_and_verified_by_reading_back(
    writeback_adapter: DataHubWritebackAdapter,
    adapter: DataHubMetadataAdapter,
) -> None:
    """An approved writeback really lands, and is confirmed from DataHub.

    ``APPLIED`` is only returned after the adapter's own read-back succeeds, so
    this test additionally re-reads through the *metadata* adapter - a separate
    code path - to confirm the tag is visible to ordinary catalog reads too.
    """
    incident_id = "LM-INTEGRATION"
    tag = "lineagemedic-incident"

    receipt = writeback_adapter.write_incident_metadata(
        target_urns=[URN_STAGING_PATIENTS],
        tags=[tag],
        note=f"Incident {incident_id}: staging refresh lag under investigation.",
        incident_id=incident_id,
        approved=True,
    )

    assert receipt.status is WritebackStatus.APPLIED, receipt.error
    assert receipt.source is DataSource.LIVE_DATAHUB
    assert tag in receipt.tags_added
    assert "globalTags" in receipt.aspects_written

    # Independent confirmation through the read adapter.
    asset = adapter.get_asset(URN_STAGING_PATIENTS)
    assert tag in asset.tags


def test_writeback_preserves_tags_it_did_not_add(
    writeback_adapter: DataHubWritebackAdapter,
    adapter: DataHubMetadataAdapter,
) -> None:
    """An incident write must not clobber tags the ingestion put there.

    staging_patients ships with healthcare/phi/silver. Those belong to other
    teams, and a writeback that replaced rather than merged would quietly
    destroy them.
    """
    writeback_adapter.write_incident_metadata(
        target_urns=[URN_STAGING_PATIENTS],
        tags=["lineagemedic-merge-probe"],
        note="Merge check.",
        incident_id="LM-MERGE",
        approved=True,
    )

    tags = set(adapter.get_asset(URN_STAGING_PATIENTS).tags)

    assert "lineagemedic-merge-probe" in tags
    assert {"healthcare", "phi", "silver"} <= tags, (
        "pre-existing tags were lost by the writeback"
    )
