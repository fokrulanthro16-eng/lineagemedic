"""Safety Agent: the gate between a recommendation and a change.

Two rules, applied without exception:

1.  Anything not classified ``SAFE`` requires explicit human approval before it
    can run. LineageMedic will describe such an action in full, but will not
    perform it on its own initiative.
2.  A ``DESTRUCTIVE`` action is blocked outright, approval or not. Irreversible
    operations are outside what an automated responder should hold; they get
    handed to a human with the evidence attached.

The agent also refuses to act on assets it cleared. If an action targets an
asset the Impact Agent marked unaffected, that is a containment failure, and
blocking it here is what keeps the billing branch in service.
"""

from __future__ import annotations

from lineagemedic.models import (
    ActionRisk,
    AgentName,
    DataSource,
    EvidenceItem,
    ImpactAssessment,
    ImpactState,
    RemediationAction,
    SafetyVerdict,
)


class SafetyAgent:
    """Validates a remediation plan before anything is allowed to execute."""

    name = "safety"

    def run(
        self,
        *,
        actions: list[RemediationAction],
        impact: ImpactAssessment,
        source: DataSource,
    ) -> tuple[SafetyVerdict, list[EvidenceItem]]:
        cleared_urns = {a.urn for a in impact.assets if a.state is ImpactState.UNAFFECTED}

        approved: list[str] = []
        blocked: list[str] = []
        reasons: dict[str, str] = {}
        notes: list[str] = []

        for action in actions:
            if action.risk is ActionRisk.DESTRUCTIVE:
                blocked.append(action.action_id)
                reasons[action.action_id] = (
                    "Destructive actions are never executed automatically. Hand this to the "
                    "asset owner with the incident evidence attached."
                )
                continue

            if action.target_urn in cleared_urns:
                blocked.append(action.action_id)
                reasons[action.action_id] = (
                    "Target was assessed as unaffected by this incident. Acting on it would "
                    "expand the blast radius beyond what the evidence supports."
                )
                continue

            if not action.reversible:
                blocked.append(action.action_id)
                reasons[action.action_id] = (
                    "Action does not declare a reversible path, so it cannot be applied "
                    "under an automated incident response."
                )
                continue

            approved.append(action.action_id)

        gated = [a for a in actions if a.requires_approval and a.action_id in approved]
        requires_human = bool(gated)

        if requires_human:
            notes.append(
                f"{len(gated)} action(s) change serving or storage state and are held at the "
                "approval gate: " + ", ".join(a.action_id for a in gated) + "."
            )
        if blocked:
            notes.append(f"{len(blocked)} action(s) were blocked outright by policy.")
        if cleared_urns:
            notes.append(
                f"{len(cleared_urns)} cleared asset(s) are protected from modification by "
                "this incident response."
            )

        verdict = SafetyVerdict(
            approved_actions=approved,
            blocked_actions=blocked,
            requires_human_approval=requires_human,
            blocking_reasons=reasons,
            notes=notes,
        )

        evidence = [
            EvidenceItem(
                label="Safety review completed",
                detail=(
                    f"{len(approved)} action(s) cleared policy, {len(blocked)} blocked. "
                    + (
                        "Human approval is required before any state-changing action runs."
                        if requires_human
                        else "No state-changing action was proposed, so no approval is needed."
                    )
                ),
                agent=AgentName.SAFETY,
                source=source,
            )
        ]
        for action_id, reason in reasons.items():
            evidence.append(
                EvidenceItem(
                    label=f"Blocked: {action_id}",
                    detail=reason,
                    agent=AgentName.SAFETY,
                    source=source,
                )
            )
        return verdict, evidence
