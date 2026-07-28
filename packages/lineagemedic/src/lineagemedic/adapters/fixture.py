"""Fixture-backed adapters: Demo Fixture Mode.

These implementations serve the committed graph in
:mod:`lineagemedic.fixtures.graph` with no network access at all. They exist so
the application is fully demonstrable and testable before a DataHub instance is
available, and so the unit suite never depends on a running container.

Two invariants hold here and are enforced by tests:

1.  Every object returned carries ``DataSource.FIXTURE``. Nothing produced by
    this module can be mistaken for a live DataHub reading.
2.  :class:`FixtureWritebackAdapter` never reports ``APPLIED``. A fixture cannot
    mutate DataHub, so it returns ``SKIPPED_FIXTURE_MODE`` and says so plainly.
    Claiming a successful writeback here would be exactly the kind of fabricated
    result the project forbids.

The MCP call records this module produces are truthful: they describe calls
genuinely served from fixtures and are labelled ``FIXTURE``. They are not
imitations of live MCP traffic.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager

from lineagemedic.adapters.base import AdapterError
from lineagemedic.fixtures.graph import DATAHUB_FRONTEND_DEFAULT, build_graph
from lineagemedic.models import (
    Asset,
    DataSource,
    LineageGraph,
    McpCallRecord,
    WritebackReceipt,
    WritebackStatus,
)

FIXTURE_NOTICE = "Demo Fixture Mode - DataHub integration not connected."

#: MCP tool names mirrored from the DataHub MCP server surface. Using the real
#: names keeps the recorded trace meaningful and means the live adapter reports
#: the same identifiers.
FIXTURE_CAPABILITIES = [
    "search",
    "get_dataset",
    "get_lineage",
]

#: Default traversal depth. The patient branch is four hops from raw_patients to
#: production_readmission_endpoint, so a depth of 3 would silently truncate the
#: graph just before the production endpoint - the one node the impact analysis
#: most needs. 5 covers the full chain with headroom.
DEFAULT_LINEAGE_DEPTH = 5


class FixtureMetadataAdapter:
    """Serves DataHub metadata reads from the committed fixture graph."""

    def __init__(self, *, frontend_url: str = DATAHUB_FRONTEND_DEFAULT) -> None:
        self._graph = build_graph(source=DataSource.FIXTURE, frontend_url=frontend_url)
        self._calls: list[McpCallRecord] = []

    @property
    def source(self) -> DataSource:
        return DataSource.FIXTURE

    def health(self) -> tuple[bool, str]:
        """Fixtures are always loadable, but this is not a DataHub connection.

        Returns ``False`` for reachability because no DataHub instance is
        connected; the detail string states what is actually serving the data.
        """
        return False, FIXTURE_NOTICE

    def capabilities(self) -> list[str]:
        return list(FIXTURE_CAPABILITIES)

    @contextmanager
    def _record(self, tool: str, arguments: dict[str, object]) -> Iterator[list[str]]:
        """Time a call and append an :class:`McpCallRecord` for it.

        The yielded list is filled by the caller with the URNs the call
        returned, so the record reflects the real result rather than a guess.
        """
        urns: list[str] = []
        started = time.perf_counter()
        error: str | None = None
        try:
            yield urns
        except Exception as exc:
            error = str(exc)
            raise
        finally:
            self._calls.append(
                McpCallRecord(
                    tool=tool,
                    arguments=dict(arguments),
                    ok=error is None,
                    returned_urns=list(urns),
                    error=error,
                    duration_ms=round((time.perf_counter() - started) * 1000, 3),
                    source=DataSource.FIXTURE,
                )
            )

    def search_assets(self, query: str, limit: int = 10) -> list[Asset]:
        """Case-insensitive substring match over name, description, and tags."""
        with self._record("search", {"query": query, "limit": limit}) as urns:
            needle = query.strip().lower()
            hits: list[Asset] = []
            for asset in self._graph.assets:
                haystack = " ".join(
                    [asset.name, asset.description or "", " ".join(asset.tags)]
                ).lower()
                if needle in haystack:
                    hits.append(asset)
            hits = hits[:limit]
            urns.extend(a.urn for a in hits)
            return hits

    def get_asset(self, urn: str) -> Asset:
        with self._record("get_dataset", {"urn": urn}) as urns:
            asset = self._graph.by_urn(urn)
            if asset is None:
                raise AdapterError(f"asset not present in fixture graph: {urn}")
            urns.append(asset.urn)
            return asset

    def get_lineage(
        self, urn: str, upstream_depth: int = DEFAULT_LINEAGE_DEPTH,
        downstream_depth: int = DEFAULT_LINEAGE_DEPTH,
    ) -> LineageGraph:
        """Return the connected component containing ``urn``.

        Depth arguments are recorded and bounded against, matching the live MCP
        tool signature so the call trace is comparable across modes.
        """
        with self._record(
            "get_lineage",
            {"urn": urn, "upstream_depth": upstream_depth, "downstream_depth": downstream_depth},
        ) as urns:
            if self._graph.by_urn(urn) is None:
                raise AdapterError(f"asset not present in fixture graph: {urn}")

            collected: dict[str, Asset] = {}
            self._walk(urn, downstream_depth, "downstreams", collected)
            self._walk(urn, upstream_depth, "upstreams", collected)
            start = self._graph.by_urn(urn)
            if start is not None:
                collected[urn] = start

            ordered = [a for a in self._graph.assets if a.urn in collected]
            urns.extend(a.urn for a in ordered)
            return LineageGraph(assets=ordered, source=DataSource.FIXTURE)

    def _walk(
        self,
        urn: str,
        depth: int,
        direction: str,
        collected: dict[str, Asset],
    ) -> None:
        """Depth-bounded traversal in one direction, cycle-safe."""
        if depth <= 0:
            return
        asset = self._graph.by_urn(urn)
        if asset is None:
            return
        for neighbour in getattr(asset, direction):
            if neighbour in collected:
                continue
            found = self._graph.by_urn(neighbour)
            if found is None:
                continue
            collected[neighbour] = found
            self._walk(neighbour, depth - 1, direction, collected)

    def drain_calls(self) -> list[McpCallRecord]:
        drained, self._calls = self._calls, []
        return drained


class FixtureWritebackAdapter:
    """Writeback port that honestly refuses to write.

    Fixture mode has no DataHub to mutate. Rather than pretending, this adapter
    returns a receipt explaining that the write was skipped and which URNs a
    live run would have targeted. The approval gate is still enforced first, so
    the ordering of checks matches the live adapter exactly.
    """

    @property
    def source(self) -> DataSource:
        return DataSource.FIXTURE

    def write_incident_metadata(
        self,
        *,
        target_urns: list[str],
        tags: list[str],
        note: str,
        incident_id: str,
        approved: bool,
    ) -> WritebackReceipt:
        if not approved:
            return WritebackReceipt(
                status=WritebackStatus.BLOCKED_PENDING_APPROVAL,
                target_urns=list(target_urns),
                note=(
                    "Writeback blocked: human approval has not been granted for "
                    f"incident {incident_id}."
                ),
                source=DataSource.FIXTURE,
            )
        return WritebackReceipt(
            status=WritebackStatus.SKIPPED_FIXTURE_MODE,
            target_urns=list(target_urns),
            aspects_written=[],
            tags_added=[],
            note=(
                f"{FIXTURE_NOTICE} Approval was granted for incident {incident_id}, "
                f"and a live run would attach {len(tags)} tag(s) and an incident note to "
                f"{len(target_urns)} asset(s). No metadata was written because no DataHub "
                "instance is connected."
            ),
            source=DataSource.FIXTURE,
        )
