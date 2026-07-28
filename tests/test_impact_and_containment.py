"""Impact Agent tests, centred on selective containment.

The headline claim of this project is that it contains a blast radius instead of
quarantining a warehouse. These tests are what make that claim checkable: they
assert not only that the right assets are flagged, but that the billing branch
is explicitly examined and left in service.
"""

from __future__ import annotations

from pathlib import Path

from lineagemedic.agents.impact import ImpactAgent
from lineagemedic.agents.quality import QualityAgent
from lineagemedic.fixtures.graph import (
    URN_BILLING_SUMMARY,
    URN_PATIENT_FEATURES,
    URN_PRODUCTION_ENDPOINT,
    URN_RAW_BILLING,
    URN_RAW_PATIENTS,
    URN_READMISSION_MODEL,
    URN_STAGING_PATIENTS,
)
from lineagemedic.models import ImpactState
from lineagemedic.scenarios import CRITICAL, HEALTHY


def _run(db_path: Path, now, scenario, metadata):
    from lineagemedic.agents.context import ContextAgent

    checks = QualityAgent(db_path, now=now).run(scenario)
    graph, _ = ContextAgent(metadata).run(scenario)
    assessment, evidence = ImpactAgent().run(
        graph=graph, checks=checks, anchor_urn=scenario.anchor_urn
    )
    return assessment, evidence


def test_full_patient_chain_is_in_the_blast_radius(db_path, now, metadata) -> None:
    assessment, _ = _run(db_path, now, CRITICAL, metadata)
    in_radius = {
        a.urn for a in assessment.assets if a.state is not ImpactState.UNAFFECTED
    }

    for urn in (
        URN_RAW_PATIENTS,
        URN_STAGING_PATIENTS,
        URN_PATIENT_FEATURES,
        URN_READMISSION_MODEL,
        URN_PRODUCTION_ENDPOINT,
    ):
        assert urn in in_radius, f"{urn} should be affected"


def test_billing_branch_is_never_quarantined(db_path, now, metadata) -> None:
    """The core containment guarantee."""
    assessment, _ = _run(db_path, now, CRITICAL, metadata)
    cleared = {a.urn for a in assessment.unaffected}

    assert URN_RAW_BILLING in cleared
    assert URN_BILLING_SUMMARY in cleared
    assert assessment.unaffected_count == 2


def test_cleared_assets_state_a_reason(db_path, now, metadata) -> None:
    """Clearing an asset must be a recorded decision, not an omission."""
    assessment, _ = _run(db_path, now, CRITICAL, metadata)

    for asset in assessment.unaffected:
        assert asset.rationale
        assert "must not be quarantined" in asset.rationale


def test_production_endpoint_and_model_are_identified(db_path, now, metadata) -> None:
    assessment, _ = _run(db_path, now, CRITICAL, metadata)

    assert assessment.production_endpoints_affected == [URN_PRODUCTION_ENDPOINT]
    assert assessment.ml_models_affected == [URN_READMISSION_MODEL]


def test_hop_distances_follow_lineage(db_path, now, metadata) -> None:
    """Distance is measured over lineage edges from the *nearest* failing dataset.

    In the critical scenario three datasets fail checks directly -- raw_patients,
    staging_patients, and patient_features -- so each is its own origin at hop 0.
    The model and endpoint have no checks of their own and are reached purely by
    propagation, so their distance is measured from patient_features.
    """
    assessment, _ = _run(db_path, now, CRITICAL, metadata)
    hops = {a.urn: a.hops_from_source for a in assessment.assets}

    # Directly measured, therefore hop 0 rather than a downstream distance.
    assert hops[URN_RAW_PATIENTS] == 0
    assert hops[URN_STAGING_PATIENTS] == 0
    assert hops[URN_PATIENT_FEATURES] == 0

    # Reached only by propagation from the nearest failing dataset.
    assert hops[URN_READMISSION_MODEL] == 1
    assert hops[URN_PRODUCTION_ENDPOINT] == 2

    # Unreachable from any failing dataset: no distance exists.
    assert hops[URN_RAW_BILLING] is None
    assert hops[URN_BILLING_SUMMARY] is None


def test_healthy_scenario_affects_nothing(db_path, now, metadata) -> None:
    assessment, _ = _run(db_path, now, HEALTHY, metadata)

    assert assessment.affected_count == 0
    assert assessment.production_endpoints_affected == []
    assert assessment.ml_models_affected == []


def test_containment_is_reported_as_evidence(db_path, now, metadata) -> None:
    """A judge must be able to see containment, not infer it."""
    _, evidence = _run(db_path, now, CRITICAL, metadata)
    labels = {e.label for e in evidence}

    assert "Selective containment" in labels
    containment = next(e for e in evidence if e.label == "Selective containment")
    assert "raw_billing" in containment.detail
    assert URN_BILLING_SUMMARY in containment.references
