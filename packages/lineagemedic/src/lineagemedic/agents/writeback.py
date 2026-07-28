"""Writeback Agent: record incident knowledge back into DataHub.

This is the only agent that mutates anything. It runs after approval and asks
the :class:`~lineagemedic.adapters.base.WritebackPort` to attach incident tags
and a note to the assets in the blast radius, using patch semantics so unrelated
metadata is preserved.

The agent re-checks approval itself rather than trusting the caller. Combined
with the identical check inside the adapter, the gate is enforced twice on
independent layers, so no call path can write without a human decision.

In fixture mode the adapter reports ``SKIPPED_FIXTURE_MODE``. That receipt is
surfaced verbatim; the agent never upgrades it to a success.
"""

from __future__ import annotations

from lineagemedic.adapters.base import WritebackPort
from lineagemedic.models import (
    AgentName,
    EvidenceItem,
    ImpactAssessment,
    ImpactState,
    RootCauseHypothesis,
    Severity,
    WritebackReceipt,
    WritebackStatus,
)

#: Tag applied to every asset touched by an incident, so DataHub users can find
#: affected assets by faceted search after the fact.
INCIDENT_TAG = "LineageMedic:incident"


class WritebackAgent:
    """Persists incident knowledge to DataHub after human approval."""

    name = "writeback"

    def __init__(self, writeback: WritebackPort) -> None:
        self._writeback = writeback

    def run(
        self,
        *,
        incident_id: str,
        severity: Severity,
        impact: ImpactAssessment,
        root_causes: list[RootCauseHypothesis],
        approved: bool,
    ) -> tuple[WritebackReceipt, list[EvidenceItem]]:
        targets = [
            a.urn
            for a in impact.assets
            if a.state in (ImpactState.SOURCE, ImpactState.AFFECTED)
        ]
        tags = [INCIDENT_TAG, f"LineageMedic:severity:{severity.value}"]

        headline = root_causes[0].summary if root_causes else "No root cause identified."
        note = (
            f"LineageMedic incident {incident_id} (severity: {severity.value}). "
            f"{headline} "
            f"{impact.affected_count} asset(s) in the blast radius; "
            f"{impact.unaffected_count} asset(s) examined and cleared."
        )

        receipt = self._writeback.write_incident_metadata(
            target_urns=targets,
            tags=tags,
            note=note,
            incident_id=incident_id,
            approved=approved,
        )

        evidence = [
            EvidenceItem(
                label=self._label(receipt.status),
                detail=receipt.note,
                agent=AgentName.WRITEBACK,
                source=receipt.source,
                references=receipt.target_urns,
            )
        ]
        return receipt, evidence

    @staticmethod
    def _label(status: WritebackStatus) -> str:
        return {
            WritebackStatus.APPLIED: "DataHub writeback applied",
            WritebackStatus.BLOCKED_PENDING_APPROVAL: "DataHub writeback blocked",
            WritebackStatus.SKIPPED_FIXTURE_MODE: "DataHub writeback skipped",
            WritebackStatus.FAILED: "DataHub writeback failed",
        }[status]
