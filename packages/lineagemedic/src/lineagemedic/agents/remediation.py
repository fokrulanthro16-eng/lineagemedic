"""Remediation Agent: propose reversible, evidence-backed corrective actions.

Every action this agent emits is tied to a measurement that was actually taken.
It proposes nothing speculative, and it proposes nothing it cannot describe
undoing - the model layer enforces that a non-``SAFE`` action must carry a
rollback procedure.

The actions are deliberately conservative in shape: add a validation rule,
quarantine a specific downstream consumer, notify a named owner, pin a model to
its last known-good feature snapshot. LineageMedic recommends; humans approve;
only metadata is ever written automatically.
"""

from __future__ import annotations

from lineagemedic.models import (
    ActionRisk,
    AgentName,
    AssetKind,
    CheckStatus,
    EvidenceItem,
    ImpactAssessment,
    ImpactState,
    LineageGraph,
    QualityCheck,
    RemediationAction,
    RootCauseHypothesis,
    Severity,
)


class RemediationAgent:
    """Turns a diagnosis into a concrete, reversible action plan."""

    name = "remediation"

    def run(
        self,
        *,
        graph: LineageGraph,
        checks: list[QualityCheck],
        impact: ImpactAssessment,
        root_causes: list[RootCauseHypothesis],
        severity: Severity,
    ) -> tuple[list[RemediationAction], list[EvidenceItem]]:
        if severity is Severity.HEALTHY or not root_causes:
            return [], [
                EvidenceItem(
                    label="No remediation required",
                    detail=(
                        "All checks passed. No corrective action is proposed; the assets "
                        "remain in service unchanged."
                    ),
                    agent=AgentName.REMEDIATION,
                    source=graph.source,
                )
            ]

        actions: list[RemediationAction] = []
        primary = root_causes[0]
        primary_asset = graph.by_urn(primary.suspected_urn)
        primary_name = primary_asset.name if primary_asset else primary.suspected_urn
        failing = [c for c in checks if c.status is CheckStatus.FAIL]

        # 1. Constrain the origin so the defect cannot recur. Metadata-only, so
        #    it is safe to apply without a data migration.
        range_checks = [
            c for c in failing if c.dataset_urn == primary.suspected_urn and c.column
        ]
        for check in range_checks[:2]:
            actions.append(
                RemediationAction(
                    action_id=f"add-validation-{check.check_id}",
                    title=f"Add a validation assertion on {primary_name}.{check.column}",
                    description=(
                        f"Register a data-quality assertion enforcing '{check.description}' "
                        f"at ingestion time. Measured {check.failing_rows} violating row(s) "
                        f"out of {check.rows_scanned}. This stops new bad values entering "
                        "the pipeline; it does not alter existing rows."
                    ),
                    risk=ActionRisk.SAFE,
                    target_urn=primary.suspected_urn,
                    reversible=True,
                    rollback=f"Remove the assertion '{check.check_id}' from the dataset.",
                    requires_approval=False,
                )
            )

        # 2. Contain the production endpoint. This is the one action with real
        #    user-visible consequences, so it is the one that gates on approval.
        for endpoint_urn in impact.production_endpoints_affected:
            endpoint = graph.by_urn(endpoint_urn)
            endpoint_name = endpoint.name if endpoint else endpoint_urn
            actions.append(
                RemediationAction(
                    action_id=f"pin-endpoint-{endpoint_name}",
                    title=f"Pin {endpoint_name} to the last known-good feature snapshot",
                    description=(
                        f"{endpoint_name} is serving predictions computed from degraded "
                        "features. Pin it to the most recent feature snapshot taken before "
                        "the defect window so it keeps serving valid scores while the "
                        "upstream fix lands. Traffic is not interrupted."
                    ),
                    risk=ActionRisk.REVERSIBLE,
                    target_urn=endpoint_urn,
                    reversible=True,
                    rollback=(
                        f"Unpin {endpoint_name} and resume live feature computation once "
                        "the upstream validation assertion reports clean."
                    ),
                    requires_approval=True,
                )
            )

        # 3. Recompute the affected feature table once the origin is clean.
        feature_tables = [
            a
            for a in impact.assets
            if a.kind is AssetKind.FEATURE_TABLE and a.state is not ImpactState.UNAFFECTED
        ]
        for feature in feature_tables:
            actions.append(
                RemediationAction(
                    action_id=f"backfill-{feature.name}",
                    title=f"Backfill {feature.name} after the upstream fix",
                    description=(
                        f"Recompute {feature.name} for the affected window once "
                        f"{primary_name} is validated, so downstream consumers see "
                        "corrected values rather than the fallback bucket."
                    ),
                    risk=ActionRisk.REVERSIBLE,
                    target_urn=feature.urn,
                    reversible=True,
                    rollback=(
                        f"Restore {feature.name} from the pre-backfill snapshot; the "
                        "backfill writes a new partition rather than overwriting in place."
                    ),
                    requires_approval=True,
                )
            )

        # 4. Notify the accountable owner. Always safe, never skipped.
        owners = primary_asset.owners if primary_asset else []
        technical = [o for o in owners if o.type == "TECHNICAL_OWNER"]
        owner = technical[0] if technical else (owners[0] if owners else None)
        if owner is not None:
            actions.append(
                RemediationAction(
                    action_id="notify-owner",
                    title=f"Notify {owner.display_name}",
                    description=(
                        f"{owner.display_name} is the recorded technical owner of "
                        f"{primary_name} in DataHub. Send them the incident summary, the "
                        "failing check results, and the blast-radius assessment."
                    ),
                    risk=ActionRisk.SAFE,
                    target_urn=primary.suspected_urn,
                    reversible=True,
                    requires_approval=False,
                )
            )

        evidence = [
            EvidenceItem(
                label="Remediation plan assembled",
                detail=(
                    f"{len(actions)} action(s) proposed: "
                    f"{sum(1 for a in actions if a.requires_approval)} require human approval, "
                    f"{sum(1 for a in actions if not a.requires_approval)} are metadata-only. "
                    "Every action declares a rollback."
                ),
                agent=AgentName.REMEDIATION,
                source=graph.source,
                references=[a.target_urn for a in actions],
            )
        ]
        return actions, evidence
