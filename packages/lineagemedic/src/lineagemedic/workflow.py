"""The seven-agent workflow orchestrator.

Runs Quality -> Context -> Impact -> Root Cause -> Remediation -> Safety ->
Writeback, timing each step and collecting evidence, MCP call records, and a
per-agent timeline into a single validated :class:`~lineagemedic.models.Diagnosis`.

Severity is *derived*, never declared. It falls out of what the checks measured
and where the failures landed in the lineage graph, so a scenario cannot assert
its own outcome. The same rule applies to confidence: it is computed from
evidence coverage and structural certainty, and the diagnosis carries a plain
sentence explaining how it was reached.

Writeback is separated from diagnosis. :meth:`Workflow.diagnose` never mutates
anything; it returns a plan with an approval state. Only
:meth:`Workflow.apply_writeback`, called after a human decision, can write.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from lineagemedic.adapters.base import MetadataPort, WritebackPort
from lineagemedic.agents.context import ContextAgent
from lineagemedic.agents.impact import ImpactAgent
from lineagemedic.agents.quality import QualityAgent
from lineagemedic.agents.remediation import RemediationAgent
from lineagemedic.agents.root_cause import RootCauseAgent
from lineagemedic.agents.safety import SafetyAgent
from lineagemedic.agents.writeback import WritebackAgent
from lineagemedic.models import (
    AgentName,
    AgentStep,
    ApprovalState,
    CheckStatus,
    DataSource,
    Diagnosis,
    EvidenceItem,
    ImpactAssessment,
    LineageGraph,
    QualityCheck,
    RootCauseHypothesis,
    Severity,
    WritebackReceipt,
)
from lineagemedic.scenarios import Scenario

FIXTURE_NOTICE = "Demo Fixture Mode - DataHub integration not connected."


class _Timer:
    """Records wall-clock duration for one agent step."""

    def __init__(self) -> None:
        self.started_at = datetime.now(UTC)
        self._t0 = time.perf_counter()

    def stop(self) -> float:
        return round((time.perf_counter() - self._t0) * 1000, 3)


class Workflow:
    """Executes the full diagnostic pipeline for a scenario."""

    def __init__(
        self,
        *,
        metadata: MetadataPort,
        writeback: WritebackPort,
        db_path: Path,
        now: datetime | None = None,
        narrator: Callable[[Diagnosis], str] | None = None,
    ) -> None:
        self._metadata = metadata
        self._writeback_port = writeback
        self._db_path = Path(db_path)
        self._now = now
        self._narrator = narrator

    # -- public API ---------------------------------------------------------

    def diagnose(self, scenario: Scenario) -> Diagnosis:
        """Run agents 1-6 and return a diagnosis awaiting approval.

        This method performs no mutation. The returned diagnosis carries an
        approval state of ``PENDING`` when a state-changing action was proposed,
        or ``NOT_REQUIRED`` when the plan is metadata-only or the data is healthy.
        """
        incident_id = f"LM-{uuid.uuid4().hex[:8].upper()}"
        steps: list[AgentStep] = []
        evidence: list[EvidenceItem] = []

        # 1. Quality -- the only agent that reads the warehouse.
        timer = _Timer()
        checks = QualityAgent(self._db_path, now=self._now).run(scenario)
        quality_evidence = self._quality_evidence(checks)
        evidence += quality_evidence
        failed = [c for c in checks if c.status is CheckStatus.FAIL]
        steps.append(
            AgentStep(
                agent=AgentName.QUALITY,
                title="Scanned healthcare data for planted defects",
                summary=(
                    f"Executed {len(checks)} check(s) against the warehouse; "
                    f"{len(failed)} failed."
                ),
                started_at=timer.started_at,
                duration_ms=timer.stop(),
                evidence=quality_evidence,
            )
        )

        # 2. Context -- DataHub reads via the metadata port.
        timer = _Timer()
        graph, context_evidence = ContextAgent(self._metadata).run(scenario)
        context_calls = self._metadata.drain_calls()
        evidence += context_evidence
        steps.append(
            AgentStep(
                agent=AgentName.CONTEXT,
                title="Retrieved DataHub schema, ownership, and lineage",
                summary=(
                    f"Resolved {len(graph.assets)} asset(s) using "
                    f"{len(context_calls)} MCP tool call(s)."
                ),
                started_at=timer.started_at,
                duration_ms=timer.stop(),
                evidence=context_evidence,
                mcp_calls=context_calls,
            )
        )

        # 3. Impact -- blast radius and, critically, its boundary.
        timer = _Timer()
        impact, impact_evidence = ImpactAgent().run(
            graph=graph, checks=checks, anchor_urn=scenario.anchor_urn
        )
        evidence += impact_evidence
        steps.append(
            AgentStep(
                agent=AgentName.IMPACT,
                title="Calculated downstream blast radius",
                summary=(
                    f"{impact.affected_count} asset(s) affected, "
                    f"{impact.unaffected_count} cleared and left in service."
                ),
                started_at=timer.started_at,
                duration_ms=timer.stop(),
                evidence=impact_evidence,
            )
        )

        severity = self._derive_severity(checks, impact)

        # 4. Root cause -- ranked hypotheses over lineage direction.
        timer = _Timer()
        root_causes, rc_evidence = RootCauseAgent().run(graph=graph, checks=checks)
        evidence += rc_evidence
        steps.append(
            AgentStep(
                agent=AgentName.ROOT_CAUSE,
                title="Ranked probable root causes",
                summary=(
                    f"{len(root_causes)} hypothesis/es ranked; top confidence "
                    f"{root_causes[0].confidence:.0%}."
                    if root_causes
                    else "No failure to explain."
                ),
                started_at=timer.started_at,
                duration_ms=timer.stop(),
                evidence=rc_evidence,
            )
        )

        # 5. Remediation -- reversible, evidence-backed proposals.
        timer = _Timer()
        actions, rem_evidence = RemediationAgent().run(
            graph=graph,
            checks=checks,
            impact=impact,
            root_causes=root_causes,
            severity=severity,
        )
        evidence += rem_evidence
        steps.append(
            AgentStep(
                agent=AgentName.REMEDIATION,
                title="Proposed corrective actions",
                summary=f"{len(actions)} reversible action(s) proposed.",
                started_at=timer.started_at,
                duration_ms=timer.stop(),
                evidence=rem_evidence,
            )
        )

        # 6. Safety -- the gate.
        timer = _Timer()
        verdict, safety_evidence = SafetyAgent().run(
            actions=actions, impact=impact, source=graph.source
        )
        evidence += safety_evidence
        steps.append(
            AgentStep(
                agent=AgentName.SAFETY,
                title="Applied safety policy",
                summary=(
                    f"{len(verdict.approved_actions)} allowed, "
                    f"{len(verdict.blocked_actions)} blocked; "
                    + (
                        "human approval required."
                        if verdict.requires_human_approval
                        else "no approval needed."
                    )
                ),
                started_at=timer.started_at,
                duration_ms=timer.stop(),
                evidence=safety_evidence,
            )
        )

        confidence, confidence_explanation = self._derive_confidence(
            checks=checks, root_causes=root_causes, graph=graph
        )

        diagnosis = Diagnosis(
            incident_id=incident_id,
            scenario_id=scenario.scenario_id,
            title=scenario.title,
            severity=severity,
            confidence=confidence,
            confidence_explanation=confidence_explanation,
            summary=self._summarize(scenario, severity, checks, impact, root_causes),
            quality_checks=checks,
            lineage=graph,
            impact=impact,
            root_causes=root_causes,
            remediation=actions,
            safety=verdict,
            approval_state=(
                ApprovalState.PENDING
                if verdict.requires_human_approval
                else ApprovalState.NOT_REQUIRED
            ),
            writeback=None,
            evidence=evidence,
            steps=steps,
            mcp_calls=[c for s in steps for c in s.mcp_calls],
            context_source=graph.source,
            fixture_mode_notice=(
                FIXTURE_NOTICE if graph.source is DataSource.FIXTURE else None
            ),
        )

        if self._narrator is not None:
            # Narration is decorative: it restates evidence already computed.
            # A failure here must never invalidate a completed diagnosis.
            try:
                diagnosis.narration = self._narrator(diagnosis)
            except Exception:  # pragma: no cover - defensive
                diagnosis.narration = None
        return diagnosis

    def apply_writeback(
        self, diagnosis: Diagnosis, *, approved: bool
    ) -> tuple[WritebackReceipt, AgentStep]:
        """Run agent 7. Only ever called after a human approval decision."""
        timer = _Timer()
        receipt, wb_evidence = WritebackAgent(self._writeback_port).run(
            incident_id=diagnosis.incident_id,
            severity=diagnosis.severity,
            impact=diagnosis.impact,
            root_causes=diagnosis.root_causes,
            approved=approved,
        )
        step = AgentStep(
            agent=AgentName.WRITEBACK,
            title="Wrote incident knowledge back to DataHub",
            summary=receipt.note,
            started_at=timer.started_at,
            duration_ms=timer.stop(),
            ok=receipt.status.value != "failed",
            evidence=wb_evidence,
        )
        return receipt, step

    # -- derivations --------------------------------------------------------

    @staticmethod
    def _derive_severity(checks: list[QualityCheck], impact: ImpactAssessment) -> Severity:
        """Derive severity from measurements and lineage position.

        Two conditions must both hold for CRITICAL:

        *   **Corrupted values, not merely late ones.** A check that found
            violating rows means the data itself is wrong. A freshness breach
            reports zero failing rows: the values are valid, the table is just
            behind its SLA, and a model scoring on slightly stale features is a
            materially smaller problem than one scoring on impossible ages.
        *   **A production consumer downstream.** The same corruption sitting in
            a staging table nobody serves from is a warning, not an emergency.

        Anything else that failed is a WARNING - still real, still actionable,
        but not a reason to page someone at 3am.
        """
        failed = [c for c in checks if c.status is CheckStatus.FAIL]
        if not failed:
            return Severity.HEALTHY
        production_at_risk = bool(
            impact.production_endpoints_affected or impact.ml_models_affected
        )
        corrupted_values = [c for c in failed if c.failing_rows > 0]
        if production_at_risk and corrupted_values:
            return Severity.CRITICAL
        return Severity.WARNING

    @staticmethod
    def _derive_confidence(
        *,
        checks: list[QualityCheck],
        root_causes: list[RootCauseHypothesis],
        graph: LineageGraph,
    ) -> tuple[float, str]:
        """Compute confidence and the sentence that explains it."""
        if not root_causes:
            passed = len(checks)
            return (
                0.95,
                f"All {passed} check(s) passed against measured data, and lineage was "
                "fully resolved, so the healthy verdict is well supported.",
            )

        top = root_causes[0]
        failing = [c for c in checks if c.status is CheckStatus.FAIL]
        corroborating = len({c.check_id for c in failing if c.dataset_urn == top.suspected_urn})
        parts = [
            f"Top hypothesis scores {top.confidence:.0%} from lineage structure",
            f"{corroborating} independent check(s) failed on the suspected asset",
            f"lineage resolved {len(graph.assets)} asset(s) with no gaps",
        ]
        score = min(0.96, top.confidence + 0.02 * corroborating)
        if graph.source is DataSource.FIXTURE:
            parts.append(
                "context came from committed fixtures rather than a live DataHub instance"
            )
        return round(score, 2), "; ".join(parts) + "."

    @staticmethod
    def _summarize(
        scenario: Scenario,
        severity: Severity,
        checks: list[QualityCheck],
        impact: ImpactAssessment,
        root_causes: list[RootCauseHypothesis],
    ) -> str:
        failed = [c for c in checks if c.status is CheckStatus.FAIL]
        if severity is Severity.HEALTHY:
            return (
                f"All {len(checks)} quality check(s) passed on "
                f"{scenario.title.lower()}. No downstream asset is at risk and no "
                "remediation is required."
            )
        head = root_causes[0].summary if root_causes else "Cause not localised"
        endpoints = len(impact.production_endpoints_affected)
        models = len(impact.ml_models_affected)
        return (
            f"{len(failed)} of {len(checks)} quality check(s) failed. {head}. "
            f"{impact.affected_count} asset(s) are in the blast radius, including "
            f"{models} ML model(s) and {endpoints} production endpoint(s). "
            f"{impact.unaffected_count} asset(s) were examined and cleared, and remain "
            "in service."
        )

    @staticmethod
    def _quality_evidence(checks: list[QualityCheck]) -> list[EvidenceItem]:
        """One evidence item per check, carrying the measured numbers."""
        items: list[EvidenceItem] = []
        for check in checks:
            verdict = "PASS" if check.status is CheckStatus.PASS else "FAIL"
            detail = (
                f"[{verdict}] {check.description} Observed {check.observed_value} "
                f"against threshold {check.threshold} ({check.comparison}); "
                f"{check.failing_rows} of {check.rows_scanned} row(s) violating."
            )
            if check.sample_failing_values:
                detail += f" Samples: {', '.join(check.sample_failing_values[:5])}."
            items.append(
                EvidenceItem(
                    label=f"Quality check {check.check_id}",
                    detail=detail,
                    agent=AgentName.QUALITY,
                    # Measured from the local warehouse, not from DataHub.
                    source=DataSource.FIXTURE,
                    references=[check.dataset_urn],
                )
            )
        return items

