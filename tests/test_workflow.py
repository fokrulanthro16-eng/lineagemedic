"""Workflow orchestration tests: severity derivation and agent composition."""

from __future__ import annotations

import pytest

from lineagemedic.models import AgentName, CheckStatus, DataSource, Severity
from lineagemedic.scenarios import ALL_SCENARIOS, CRITICAL, HEALTHY, WARNING, get_scenario


@pytest.mark.parametrize("scenario", list(ALL_SCENARIOS.values()), ids=lambda s: s.scenario_id)
def test_scenario_produces_its_documented_severity(workflow, scenario) -> None:
    """Severity is derived from data, and must match what the scenario documents.

    ``expected_severity`` is never read by the workflow, so this is a genuine
    oracle: it fails if either the data or the derivation logic drifts.
    """
    diagnosis = workflow.diagnose(scenario)
    assert diagnosis.severity is scenario.expected_severity


def test_critical_requires_corruption_and_a_production_consumer(workflow) -> None:
    diagnosis = workflow.diagnose(CRITICAL)

    assert diagnosis.severity is Severity.CRITICAL
    assert any(c.failing_rows > 0 for c in diagnosis.quality_checks)
    assert diagnosis.impact.production_endpoints_affected


def test_staleness_alone_is_a_warning_not_a_critical(workflow) -> None:
    """Late-but-valid data must not be escalated to the same tier as corruption."""
    diagnosis = workflow.diagnose(WARNING)

    assert diagnosis.severity is Severity.WARNING
    failed = [c for c in diagnosis.quality_checks if c.status is CheckStatus.FAIL]
    assert failed, "the warning scenario must fail at least one check"
    assert all(c.failing_rows == 0 for c in failed)
    # A production endpoint IS downstream here; only the absence of corrupted
    # rows keeps this out of CRITICAL.
    assert diagnosis.impact.production_endpoints_affected


def test_all_six_diagnostic_agents_run_in_order(workflow) -> None:
    diagnosis = workflow.diagnose(CRITICAL)

    assert [s.agent for s in diagnosis.steps] == [
        AgentName.QUALITY,
        AgentName.CONTEXT,
        AgentName.IMPACT,
        AgentName.ROOT_CAUSE,
        AgentName.REMEDIATION,
        AgentName.SAFETY,
    ]
    assert all(s.duration_ms >= 0 for s in diagnosis.steps)
    assert all(s.ok for s in diagnosis.steps)


def test_root_cause_prefers_the_lineage_origin(workflow) -> None:
    """raw_patients is upstream of staging, so it must outrank it."""
    from lineagemedic.fixtures.graph import URN_RAW_PATIENTS

    diagnosis = workflow.diagnose(CRITICAL)

    assert diagnosis.root_causes
    top = diagnosis.root_causes[0]
    assert top.suspected_urn == URN_RAW_PATIENTS
    assert "originates" in top.reasoning
    assert 0.0 < top.confidence <= 1.0


def test_downstream_symptom_is_ranked_below_the_origin(workflow) -> None:
    from lineagemedic.fixtures.graph import URN_RAW_PATIENTS, URN_STAGING_PATIENTS

    diagnosis = workflow.diagnose(CRITICAL)
    ranked = {h.suspected_urn: h for h in diagnosis.root_causes}

    assert URN_STAGING_PATIENTS in ranked
    assert (
        ranked[URN_STAGING_PATIENTS].confidence < ranked[URN_RAW_PATIENTS].confidence
    ), "an inherited defect must rank below its origin"
    assert "propagation" in ranked[URN_STAGING_PATIENTS].reasoning


def test_confidence_is_explained(workflow) -> None:
    """A bare number is not evidence; the diagnosis must say how it got there."""
    diagnosis = workflow.diagnose(CRITICAL)

    assert 0.0 <= diagnosis.confidence <= 1.0
    assert len(diagnosis.confidence_explanation) > 40
    assert "fixture" in diagnosis.confidence_explanation.lower()


def test_owner_is_resolved_from_datahub_context(workflow) -> None:
    diagnosis = workflow.diagnose(CRITICAL)
    owner = diagnosis.primary_owner

    assert owner is not None
    assert owner.display_name == "Data Platform Team"
    assert owner.type == "TECHNICAL_OWNER"


def test_evidence_is_attributed_to_agents(workflow) -> None:
    diagnosis = workflow.diagnose(CRITICAL)

    assert len(diagnosis.evidence) >= 8
    agents = {e.agent for e in diagnosis.evidence}
    assert AgentName.QUALITY in agents
    assert AgentName.CONTEXT in agents
    assert AgentName.IMPACT in agents
    assert all(e.detail for e in diagnosis.evidence)


def test_quality_evidence_reports_measured_numbers(workflow) -> None:
    """Evidence must contain the observation, not just a verdict."""
    diagnosis = workflow.diagnose(CRITICAL)
    age_evidence = next(
        e for e in diagnosis.evidence if e.label == "Quality check age-range"
    )

    assert "[FAIL]" in age_evidence.detail
    assert "37 of 500" in age_evidence.detail
    assert "Samples:" in age_evidence.detail


def test_healthy_run_proposes_nothing(workflow) -> None:
    diagnosis = workflow.diagnose(HEALTHY)

    assert diagnosis.root_causes == []
    assert diagnosis.remediation == []
    assert diagnosis.safety.requires_human_approval is False
    assert diagnosis.impact.affected_count == 0


def test_diagnosis_serialises_to_json(workflow) -> None:
    """The API contract: the whole diagnosis must round-trip."""
    from lineagemedic.models import Diagnosis

    diagnosis = workflow.diagnose(CRITICAL)
    payload = diagnosis.model_dump_json()
    restored = Diagnosis.model_validate_json(payload)

    assert restored.incident_id == diagnosis.incident_id
    assert restored.severity is diagnosis.severity
    assert restored.context_source is DataSource.FIXTURE
    assert len(restored.impact.assets) == len(diagnosis.impact.assets)


def test_incident_ids_are_unique(workflow) -> None:
    ids = {workflow.diagnose(HEALTHY).incident_id for _ in range(5)}
    assert len(ids) == 5


def test_unknown_scenario_id_is_rejected() -> None:
    with pytest.raises(KeyError, match="unknown scenario"):
        get_scenario("no-such-scenario")
