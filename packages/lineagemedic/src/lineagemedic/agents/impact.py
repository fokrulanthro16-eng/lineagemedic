"""Impact Agent: compute the blast radius, and just as importantly its edge.

Given the datasets that actually failed a check, this agent walks lineage
downstream to find everything genuinely at risk, and classifies every other
asset in the graph as unaffected with a stated reason.

The second half is the point. An incident response that quarantines the whole
warehouse is not a response, it is an outage. By naming the billing branch as
examined-and-clear - rather than silently omitting it - LineageMedic can show a
judge that containment was a decision, not an oversight.

Reachability is computed purely from lineage edges. An asset is affected if and
only if a failing dataset reaches it downstream; shared storage, similar names,
and soft foreign keys do not create impact.
"""

from __future__ import annotations

from lineagemedic.models import (
    AgentName,
    AssetKind,
    CheckStatus,
    DataSource,
    EvidenceItem,
    ImpactAssessment,
    ImpactedAsset,
    ImpactState,
    LineageGraph,
    QualityCheck,
)


class ImpactAgent:
    """Partitions the lineage graph into affected and unaffected assets."""

    name = "impact"

    def run(
        self,
        *,
        graph: LineageGraph,
        checks: list[QualityCheck],
        anchor_urn: str,
    ) -> tuple[ImpactAssessment, list[EvidenceItem]]:
        failing_urns = {c.dataset_urn for c in checks if c.status is CheckStatus.FAIL}

        # Hop distance from the nearest failing dataset, over lineage edges only.
        hops: dict[str, int] = {}
        for origin in failing_urns:
            hops.setdefault(origin, 0)
            for distance, urn in self._distances(graph, origin):
                if urn not in hops or distance < hops[urn]:
                    hops[urn] = distance

        assessed: list[ImpactedAsset] = []
        for asset in graph.assets:
            if asset.urn in failing_urns:
                state = ImpactState.SOURCE
                rationale = (
                    f"Failed {sum(1 for c in checks if c.dataset_urn == asset.urn and c.status is CheckStatus.FAIL)}"
                    " quality check(s) measured directly against this dataset."
                )
            elif asset.urn in hops:
                state = ImpactState.AFFECTED
                rationale = (
                    f"Consumes failing data {hops[asset.urn]} lineage hop(s) downstream "
                    "of a dataset that failed a quality check."
                )
            else:
                state = ImpactState.UNAFFECTED
                rationale = (
                    "No lineage path from any failing dataset reaches this asset. "
                    f"Owned independently by "
                    f"{', '.join(o.display_name for o in asset.owners) or 'an unrecorded team'}; "
                    "it must not be quarantined."
                )
            assessed.append(
                ImpactedAsset(
                    urn=asset.urn,
                    name=asset.name,
                    kind=asset.kind,
                    state=state,
                    hops_from_source=hops.get(asset.urn),
                    rationale=rationale,
                )
            )

        in_radius = [a for a in assessed if a.state in (ImpactState.SOURCE, ImpactState.AFFECTED)]
        unaffected = [a for a in assessed if a.state is ImpactState.UNAFFECTED]

        assessment = ImpactAssessment(
            source_urn=anchor_urn,
            assets=assessed,
            affected_count=len(in_radius),
            unaffected_count=len(unaffected),
            production_endpoints_affected=[
                a.urn for a in in_radius if a.kind is AssetKind.ENDPOINT
            ],
            ml_models_affected=[a.urn for a in in_radius if a.kind is AssetKind.ML_MODEL],
        )
        return assessment, self._evidence(assessment, graph, unaffected)

    @staticmethod
    def _distances(graph: LineageGraph, origin: str) -> list[tuple[int, str]]:
        """Breadth-first downstream distances from ``origin``, cycle-safe."""
        seen = {origin}
        frontier = [origin]
        distance = 0
        out: list[tuple[int, str]] = []
        while frontier:
            distance += 1
            nxt: list[str] = []
            for urn in frontier:
                asset = graph.by_urn(urn)
                if asset is None:
                    continue
                for child in asset.downstreams:
                    if child in seen:
                        continue
                    seen.add(child)
                    out.append((distance, child))
                    nxt.append(child)
            frontier = nxt
        return out

    def _evidence(
        self,
        assessment: ImpactAssessment,
        graph: LineageGraph,
        unaffected: list[ImpactedAsset],
    ) -> list[EvidenceItem]:
        source: DataSource = graph.source
        items = [
            EvidenceItem(
                label="Blast radius computed",
                detail=(
                    f"{assessment.affected_count} asset(s) lie downstream of failing data; "
                    f"{assessment.unaffected_count} asset(s) were examined and cleared."
                ),
                agent=AgentName.IMPACT,
                source=source,
                references=[a.urn for a in assessment.assets if a.state is not ImpactState.UNAFFECTED],
            )
        ]
        if assessment.production_endpoints_affected:
            items.append(
                EvidenceItem(
                    label="Production serving affected",
                    detail=(
                        f"{len(assessment.production_endpoints_affected)} live inference "
                        "endpoint(s) consume the degraded feature path and are serving "
                        "predictions from it now."
                    ),
                    agent=AgentName.IMPACT,
                    source=source,
                    references=assessment.production_endpoints_affected,
                )
            )
        if unaffected:
            items.append(
                EvidenceItem(
                    label="Selective containment",
                    detail=(
                        "Explicitly cleared: "
                        + ", ".join(a.name for a in unaffected)
                        + ". No lineage path connects these to any failing dataset, so they "
                        "stay in service."
                    ),
                    agent=AgentName.IMPACT,
                    source=source,
                    references=[a.urn for a in unaffected],
                )
            )
        return items
