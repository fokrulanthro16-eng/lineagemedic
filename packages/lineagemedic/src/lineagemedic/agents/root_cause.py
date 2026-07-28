"""Root Cause Agent: rank candidate explanations against lineage direction.

The ranking rule is structural rather than statistical. When the same defect
appears in a dataset and in its downstream consumer, the upstream occurrence is
the cause and the downstream one is a symptom - propagation only flows one way.
So a failing dataset with no failing upstream is the most likely origin, and
confidence decreases with each hop you move away from it.

The agent never claims certainty it has not earned. Confidence is derived from
observable structure (upstream cleanliness, defect severity, corroborating
checks) and every hypothesis states the reasoning that produced it.
"""

from __future__ import annotations

from lineagemedic.models import (
    AgentName,
    CheckStatus,
    EvidenceItem,
    LineageGraph,
    QualityCheck,
    RootCauseHypothesis,
)


class RootCauseAgent:
    """Produces ranked root-cause hypotheses for the failing checks."""

    name = "root_cause"

    def run(
        self, *, graph: LineageGraph, checks: list[QualityCheck]
    ) -> tuple[list[RootCauseHypothesis], list[EvidenceItem]]:
        failing = [c for c in checks if c.status is CheckStatus.FAIL]
        if not failing:
            return [], [
                EvidenceItem(
                    label="No root cause required",
                    detail="Every executed quality check passed; there is no failure to explain.",
                    agent=AgentName.ROOT_CAUSE,
                    source=graph.source,
                )
            ]

        failing_urns = {c.dataset_urn for c in failing}
        hypotheses: list[RootCauseHypothesis] = []

        for urn in failing_urns:
            asset = graph.by_urn(urn)
            if asset is None:
                continue
            urn_checks = [c for c in failing if c.dataset_urn == urn]

            # An upstream that also failed means this asset is downstream of the
            # real problem: it is inheriting the defect, not originating it.
            failing_upstreams = [u for u in asset.upstreams if u in failing_urns]
            is_origin = not failing_upstreams

            confidence = self._confidence(
                is_origin=is_origin,
                check_count=len(urn_checks),
                worst_ratio=max((c.failure_ratio for c in urn_checks), default=0.0),
            )

            columns = sorted({c.column for c in urn_checks if c.column})
            column_text = f" in column(s) {', '.join(columns)}" if columns else ""

            if is_origin:
                reasoning = (
                    f"{asset.name} failed {len(urn_checks)} check(s){column_text}, and none of "
                    "its upstream assets failed any check. The defect therefore originates "
                    "here rather than being inherited."
                )
                summary = f"Defect originates in {asset.name}{column_text}"
            else:
                upstream_names = ", ".join(
                    upstream.name if (upstream := graph.by_urn(u)) is not None else u
                    for u in failing_upstreams
                )
                reasoning = (
                    f"{asset.name} failed {len(urn_checks)} check(s){column_text}, but its "
                    f"upstream {upstream_names} failed as well. This is most likely "
                    "propagation of an upstream defect rather than a new fault."
                )
                summary = f"{asset.name} inherits a defect from {upstream_names}"

            hypotheses.append(
                RootCauseHypothesis(
                    summary=summary,
                    suspected_urn=urn,
                    confidence=confidence,
                    reasoning=reasoning,
                    supporting_evidence=[
                        f"{c.check_id}: observed {c.observed_value} against threshold "
                        f"{c.threshold} ({c.failing_rows}/{c.rows_scanned} rows)"
                        for c in urn_checks
                    ],
                )
            )

        hypotheses.sort(key=lambda h: h.confidence, reverse=True)

        top = hypotheses[0]
        top_asset = graph.by_urn(top.suspected_urn)
        evidence = [
            EvidenceItem(
                label="Most likely root cause",
                detail=f"{top.summary} (confidence {top.confidence:.0%}). {top.reasoning}",
                agent=AgentName.ROOT_CAUSE,
                source=graph.source,
                references=[top.suspected_urn],
            )
        ]
        # Samples must come from the winning hypothesis's own checks, not from
        # whichever asset happened to be examined last in the loop above.
        top_checks = [c for c in failing if c.dataset_urn == top.suspected_urn]
        top_worst = max(top_checks, key=lambda c: c.failure_ratio, default=None)
        if top_asset is not None and top_worst is not None and top_worst.sample_failing_values:
            evidence.append(
                EvidenceItem(
                    label="Observed offending values",
                    detail=(
                        f"Sample values violating {top_worst.check_id}: "
                        f"{', '.join(top_worst.sample_failing_values[:5])}."
                    ),
                    agent=AgentName.ROOT_CAUSE,
                    source=graph.source,
                    references=[top.suspected_urn],
                )
            )
        if len(hypotheses) > 1:
            evidence.append(
                EvidenceItem(
                    label="Alternative hypotheses considered",
                    detail="; ".join(
                        f"{h.summary} ({h.confidence:.0%})" for h in hypotheses[1:]
                    ),
                    agent=AgentName.ROOT_CAUSE,
                    source=graph.source,
                    references=[h.suspected_urn for h in hypotheses[1:]],
                )
            )
        return hypotheses, evidence

    @staticmethod
    def _confidence(*, is_origin: bool, check_count: int, worst_ratio: float) -> float:
        """Blend structural position with defect strength into ``[0, 1]``.

        Being the lineage origin dominates, because propagation direction is a
        hard structural fact. Corroborating checks and a larger share of failing
        rows add smaller increments.
        """
        score = 0.55 if is_origin else 0.30
        score += min(check_count - 1, 3) * 0.06
        score += min(worst_ratio * 2.0, 0.22)
        return round(min(score, 0.97), 2)
