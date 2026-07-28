"""Context Agent: retrieve DataHub metadata through the MetadataPort.

This agent owns every read against DataHub. It searches for the assets a
scenario names, pulls their schema/ownership/tags, and traverses lineage in both
directions. Everything it returns is stamped with the adapter's
:class:`~lineagemedic.models.DataSource`, so provenance survives all the way to
the UI.

The agent deliberately holds no fallback data. If the adapter cannot answer, the
error propagates: a diagnosis built on a silently empty graph would be worse
than no diagnosis.
"""

from __future__ import annotations

from lineagemedic.adapters.base import AdapterError, MetadataPort
from lineagemedic.models import AgentName, DataSource, EvidenceItem, LineageGraph
from lineagemedic.scenarios import Scenario


class ContextAgent:
    """Assembles the DataHub context for one investigation."""

    name = "context"

    def __init__(self, metadata: MetadataPort) -> None:
        self._metadata = metadata

    def run(self, scenario: Scenario) -> tuple[LineageGraph, list[EvidenceItem]]:
        """Fetch the lineage subgraph around the scenario anchor.

        Control assets named by the scenario are fetched explicitly and merged
        in, so branches that lineage does not connect to the anchor are still
        present for the Impact Agent to evaluate and clear.
        """
        source = self._metadata.source
        graph = self._metadata.get_lineage(scenario.anchor_urn)

        # Merge in control-branch assets, plus their own lineage, so the Impact
        # Agent can state positively that they were examined and found clear.
        merged: dict[str, object] = {a.urn: a for a in graph.assets}
        for control_urn in scenario.control_urns:
            try:
                control_graph = self._metadata.get_lineage(control_urn)
            except AdapterError:
                # A control asset missing from the catalog is not fatal to the
                # diagnosis; it is reported as absent rather than invented.
                continue
            for asset in control_graph.assets:
                merged.setdefault(asset.urn, asset)

        full = LineageGraph(
            assets=list(merged.values()),  # type: ignore[arg-type]
            source=source,
        )

        anchor = full.by_urn(scenario.anchor_urn)
        anchor_name = anchor.name if anchor else scenario.anchor_urn
        owner_names = sorted(
            {o.display_name for a in full.assets for o in a.owners}
        )

        evidence = [
            EvidenceItem(
                label="DataHub lineage retrieved",
                detail=(
                    f"Traversed lineage from {anchor_name} and resolved "
                    f"{len(full.assets)} connected assets across "
                    f"{len({a.platform for a in full.assets})} platforms."
                ),
                agent=AgentName.CONTEXT,
                source=source,
                references=[a.urn for a in full.assets],
            ),
            EvidenceItem(
                label="Ownership resolved",
                detail=(
                    f"Owning teams on the affected graph: {', '.join(owner_names)}."
                    if owner_names
                    else "No ownership is recorded on the retrieved assets."
                ),
                agent=AgentName.CONTEXT,
                source=source,
                references=[o.urn for a in full.assets for o in a.owners],
            ),
        ]

        if source is DataSource.FIXTURE:
            evidence.append(
                EvidenceItem(
                    label="Context provenance",
                    detail=(
                        "Metadata was served from committed fixtures, not from a live "
                        "DataHub instance. Asset URNs, schemas, and lineage edges mirror "
                        "the graph the ingestion scripts create, but nothing was read "
                        "over the network."
                    ),
                    agent=AgentName.CONTEXT,
                    source=source,
                )
            )
        return full, evidence
