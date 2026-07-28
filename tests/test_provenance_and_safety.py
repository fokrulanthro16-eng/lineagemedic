"""Anti-fabrication and safety-gate tests.

These are the tests that protect the project's central promise: LineageMedic
does not claim DataHub results it did not obtain, and it does not mutate
anything without a human decision.

They are written to fail loudly if someone later "improves" fixture mode by
having it report success. Deleting or weakening a test here should be treated as
a change to the product's honesty guarantees, not a test-maintenance detail.
"""

from __future__ import annotations

import pytest

from lineagemedic.adapters.base import AdapterError
from lineagemedic.adapters.fixture import FixtureMetadataAdapter, FixtureWritebackAdapter
from lineagemedic.models import (
    ApprovalState,
    DataSource,
    ImpactState,
    Severity,
    WritebackStatus,
)
from lineagemedic.scenarios import CRITICAL, HEALTHY

# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


def test_every_fixture_asset_is_labelled_fixture(metadata: FixtureMetadataAdapter) -> None:
    graph = metadata.get_lineage(CRITICAL.anchor_urn)

    assert graph.source is DataSource.FIXTURE
    assert all(a.source is DataSource.FIXTURE for a in graph.assets)


def test_fixture_adapter_does_not_claim_a_datahub_connection(
    metadata: FixtureMetadataAdapter,
) -> None:
    """Fixture mode must report 'not connected', not a green light."""
    connected, detail = metadata.health()

    assert connected is False
    assert "not connected" in detail.lower()


def test_diagnosis_carries_provenance_and_a_visible_notice(workflow) -> None:
    diagnosis = workflow.diagnose(CRITICAL)

    assert diagnosis.context_source is DataSource.FIXTURE
    assert diagnosis.fixture_mode_notice is not None
    assert "Demo Fixture Mode" in diagnosis.fixture_mode_notice


def test_mcp_call_records_are_real_and_labelled(workflow) -> None:
    """The recorded trace must describe calls that genuinely happened."""
    diagnosis = workflow.diagnose(CRITICAL)

    assert diagnosis.mcp_calls, "the workflow must record the calls it made"
    for call in diagnosis.mcp_calls:
        assert call.source is DataSource.FIXTURE
        assert call.tool in {"search", "get_dataset", "get_lineage"}
        # A successful call must have returned something identifiable.
        if call.ok:
            assert call.returned_urns
        assert call.duration_ms >= 0


def test_unknown_urn_raises_instead_of_inventing_an_asset(
    metadata: FixtureMetadataAdapter,
) -> None:
    """Absence must be an error, never a plausible-looking empty result."""
    with pytest.raises(AdapterError, match="not present"):
        metadata.get_asset("urn:li:dataset:(urn:li:dataPlatform:sqlite,nope,PROD)")


# ---------------------------------------------------------------------------
# Writeback honesty
# ---------------------------------------------------------------------------


def test_fixture_writeback_never_reports_applied(
    writeback: FixtureWritebackAdapter,
) -> None:
    """The single most important anti-fabrication guarantee.

    No DataHub is connected in fixture mode, so no writeback can have occurred.
    Reporting APPLIED here would be a fabricated result.
    """
    receipt = writeback.write_incident_metadata(
        target_urns=["urn:li:dataset:(urn:li:dataPlatform:sqlite,x,PROD)"],
        tags=["LineageMedic:incident"],
        note="test",
        incident_id="LM-TEST",
        approved=True,
    )

    assert receipt.status is WritebackStatus.SKIPPED_FIXTURE_MODE
    assert receipt.status is not WritebackStatus.APPLIED
    assert receipt.aspects_written == []
    assert receipt.tags_added == []
    assert receipt.source is DataSource.FIXTURE
    assert "no datahub instance is connected" in receipt.note.lower()


def test_writeback_refuses_without_approval(writeback: FixtureWritebackAdapter) -> None:
    receipt = writeback.write_incident_metadata(
        target_urns=["urn:li:dataset:(urn:li:dataPlatform:sqlite,x,PROD)"],
        tags=["LineageMedic:incident"],
        note="test",
        incident_id="LM-TEST",
        approved=False,
    )

    assert receipt.status is WritebackStatus.BLOCKED_PENDING_APPROVAL
    assert receipt.aspects_written == []


def test_workflow_writeback_respects_the_gate(workflow) -> None:
    diagnosis = workflow.diagnose(CRITICAL)

    blocked, _ = workflow.apply_writeback(diagnosis, approved=False)
    assert blocked.status is WritebackStatus.BLOCKED_PENDING_APPROVAL

    allowed, step = workflow.apply_writeback(diagnosis, approved=True)
    assert allowed.status is WritebackStatus.SKIPPED_FIXTURE_MODE
    assert step.agent.value == "writeback"


# ---------------------------------------------------------------------------
# Safety gate
# ---------------------------------------------------------------------------


def test_critical_incident_requires_human_approval(workflow) -> None:
    diagnosis = workflow.diagnose(CRITICAL)

    assert diagnosis.severity is Severity.CRITICAL
    assert diagnosis.safety.requires_human_approval is True
    assert diagnosis.approval_state is ApprovalState.PENDING


def test_healthy_incident_needs_no_approval(workflow) -> None:
    diagnosis = workflow.diagnose(HEALTHY)

    assert diagnosis.severity is Severity.HEALTHY
    assert diagnosis.remediation == []
    assert diagnosis.approval_state is ApprovalState.NOT_REQUIRED


def test_no_action_ever_targets_a_cleared_asset(workflow) -> None:
    """Remediation must stay inside the blast radius it computed."""
    diagnosis = workflow.diagnose(CRITICAL)
    cleared = {
        a.urn for a in diagnosis.impact.assets if a.state is ImpactState.UNAFFECTED
    }

    for action in diagnosis.remediation:
        assert action.target_urn not in cleared, (
            f"{action.action_id} targets cleared asset {action.target_urn}"
        )


def test_every_proposed_action_is_reversible(workflow) -> None:
    diagnosis = workflow.diagnose(CRITICAL)

    assert diagnosis.remediation, "the critical scenario must propose actions"
    for action in diagnosis.remediation:
        assert action.reversible is True
        if action.risk.value != "safe":
            assert action.rollback, f"{action.action_id} must declare a rollback"


def test_safety_agent_blocks_actions_on_cleared_assets() -> None:
    """Directly exercise the containment rule with a deliberately bad plan."""
    from lineagemedic.agents.safety import SafetyAgent
    from lineagemedic.models import (
        ActionRisk,
        AssetKind,
        ImpactAssessment,
        ImpactedAsset,
        RemediationAction,
    )

    cleared_urn = "urn:li:dataset:(urn:li:dataPlatform:sqlite,billing,PROD)"
    impact = ImpactAssessment(
        source_urn="urn:li:dataset:(urn:li:dataPlatform:sqlite,src,PROD)",
        assets=[
            ImpactedAsset(
                urn=cleared_urn,
                name="billing_summary",
                kind=AssetKind.DATASET,
                state=ImpactState.UNAFFECTED,
                rationale="unrelated branch",
            )
        ],
        affected_count=0,
        unaffected_count=1,
    )
    overreaching = RemediationAction(
        action_id="quarantine-billing",
        title="Quarantine billing_summary",
        description="Would wrongly take an unrelated asset out of service.",
        risk=ActionRisk.REVERSIBLE,
        target_urn=cleared_urn,
        reversible=True,
        rollback="restore",
        requires_approval=True,
    )

    verdict, _ = SafetyAgent().run(
        actions=[overreaching], impact=impact, source=DataSource.FIXTURE
    )

    assert "quarantine-billing" in verdict.blocked_actions
    assert "quarantine-billing" not in verdict.approved_actions
    assert "unaffected" in verdict.blocking_reasons["quarantine-billing"]


def test_safety_agent_blocks_destructive_actions() -> None:
    from lineagemedic.agents.safety import SafetyAgent
    from lineagemedic.models import ActionRisk, ImpactAssessment, RemediationAction

    destructive = RemediationAction(
        action_id="drop-table",
        title="Drop the corrupted table",
        description="Irreversible data loss.",
        risk=ActionRisk.DESTRUCTIVE,
        target_urn="urn:li:dataset:(urn:li:dataPlatform:sqlite,raw,PROD)",
        reversible=False,
        rollback="restore from backup",
        requires_approval=True,
    )
    impact = ImpactAssessment(
        source_urn="urn:li:dataset:(urn:li:dataPlatform:sqlite,raw,PROD)",
        assets=[],
        affected_count=0,
        unaffected_count=0,
    )

    verdict, _ = SafetyAgent().run(
        actions=[destructive], impact=impact, source=DataSource.FIXTURE
    )

    assert verdict.blocked_actions == ["drop-table"]
    assert "never executed automatically" in verdict.blocking_reasons["drop-table"]
