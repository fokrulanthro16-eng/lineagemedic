"""Live ``MetadataPort`` over a real DataHub instance.

This adapter answers the same three questions the fixture adapter does -
*search*, *get one asset*, *traverse lineage* - but does so by calling a running
DataHub. Everything it returns carries :attr:`DataSource.LIVE_DATAHUB`, and every
call is recorded so the UI can display a verifiable trace.

Transport
---------

Reads go over DataHub's GraphQL endpoint (``/api/graphql``), which is the
supported public read API for search, entity fetch, and lineage. The tool names
recorded in the call trace (``search``, ``get_dataset``, ``get_lineage``) match
the DataHub MCP server's tool surface and the fixture adapter's
``capabilities()``, so a trace is comparable across all three modes rather than
being a different vocabulary per backend.

Honesty rules this module must not break
----------------------------------------

*   **Never invent a node.** If DataHub does not know an entity, the adapter
    raises :class:`AdapterError`. It never substitutes fixture data, because a
    caller must be able to distinguish "the catalog has no such asset" from
    "the catalog was unreachable".
*   **Never claim health it did not observe.** :meth:`health` performs a real
    request and reports the outcome, including the failure reason.
*   **Record failures too.** A call that raises is still appended to the trace
    with ``ok=False`` and the error text.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import httpx

from lineagemedic.adapters.base import AdapterError
from lineagemedic.models import (
    Asset,
    AssetKind,
    DataSource,
    LineageGraph,
    McpCallRecord,
    Owner,
    SchemaField,
)

#: Tool names mirrored from the DataHub MCP server surface, so the recorded
#: trace uses identical identifiers in fixture and live mode.
LIVE_CAPABILITIES = ["search", "get_dataset", "get_lineage"]

#: The patient chain is four hops from raw_patients to the production endpoint.
#: A depth of 3 would truncate the graph just short of the endpoint, which is
#: the single node the impact analysis most needs to see.
DEFAULT_LINEAGE_DEPTH = 5

#: DataHub ownership type URNs mapped back onto the domain vocabulary. Anything
#: unrecognised becomes TECHNICAL_OWNER rather than being dropped, so an owner
#: is never silently lost from the graph.
_OWNER_TYPES = {
    "TECHNICAL_OWNER": "TECHNICAL_OWNER",
    "BUSINESS_OWNER": "BUSINESS_OWNER",
    "DATA_STEWARD": "DATA_STEWARD",
    "DATAOWNER": "TECHNICAL_OWNER",
}

_ENTITY_PATH = {
    "DATASET": "dataset",
    "MLMODEL": "mlModel",
    "MLMODEL_DEPLOYMENT": "mlModelDeployment",
    "MLMODELDEPLOYMENT": "mlModelDeployment",
}

# GraphQL fragment shared by the entity and lineage queries. Kept in one place so
# the two code paths cannot drift into returning differently-shaped assets.
_ENTITY_FIELDS = """
    urn
    type
    ... on Dataset {
      name
      platform { name }
      properties { name description }
      subTypes { typeNames }
      tags { tags { tag { urn name } } }
      ownership { owners { owner { ... on CorpGroup { urn name }
                                   ... on CorpUser  { urn username } }
                           type } }
      schemaMetadata { fields { fieldPath nativeDataType nullable description } }
    }
    ... on MLModel {
      name
      platform { name }
      properties { description }
      tags { tags { tag { urn name } } }
      ownership { owners { owner { ... on CorpGroup { urn name }
                                   ... on CorpUser  { urn username } }
                           type } }
    }
    ... on MLModelDeployment {
      name
      platform { name }
      properties { description }
      tags { tags { tag { urn name } } }
      ownership { owners { owner { ... on CorpGroup { urn name }
                                   ... on CorpUser  { urn username } }
                           type } }
    }
"""

_SEARCH_QUERY = f"""
query search($input: SearchAcrossEntitiesInput!) {{
  searchAcrossEntities(input: $input) {{
    searchResults {{ entity {{ {_ENTITY_FIELDS} }} }}
  }}
}}
"""

_ENTITY_QUERY = f"""
query getEntity($urn: String!) {{
  entity(urn: $urn) {{ {_ENTITY_FIELDS} }}
}}
"""

_LINEAGE_QUERY = f"""
query getLineage($urn: String!, $direction: LineageDirection!, $depth: Int!) {{
  searchAcrossLineage(
    input: {{urn: $urn, direction: $direction, count: 100, query: "*",
            searchFlags: {{maxHops: $depth}}}}
  ) {{
    searchResults {{ degree entity {{ {_ENTITY_FIELDS} }} }}
  }}
}}
"""


class DataHubMetadataAdapter:
    """Reads schema, ownership, tags, and lineage from a live DataHub.

    The adapter is stateless apart from its call buffer; each instance is cheap
    to construct per request.
    """

    def __init__(
        self,
        *,
        gms_url: str,
        frontend_url: str,
        token: str = "",
        timeout_seconds: float = 30.0,
    ) -> None:
        self._gms_url = gms_url.rstrip("/")
        self._frontend_url = frontend_url.rstrip("/")
        self._token = token
        self._timeout = timeout_seconds
        self._calls: list[McpCallRecord] = []

    @property
    def source(self) -> DataSource:
        return DataSource.LIVE_DATAHUB

    # -- transport ----------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        """Execute one GraphQL request, raising ``AdapterError`` on any failure.

        GraphQL returns HTTP 200 with an ``errors`` array for query-level
        problems, so a status check alone is not sufficient to conclude success.
        """
        try:
            response = httpx.post(
                f"{self._gms_url}/api/graphql",
                json={"query": query, "variables": variables},
                headers=self._headers(),
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise AdapterError(f"DataHub GraphQL request failed: {exc}") from exc

        if response.status_code >= 400:
            raise AdapterError(
                f"DataHub GraphQL returned HTTP {response.status_code}: {response.text[:200]}"
            )
        payload = response.json()
        if payload.get("errors"):
            messages = "; ".join(e.get("message", "?") for e in payload["errors"])
            raise AdapterError(f"DataHub GraphQL error: {messages}")
        data = payload.get("data")
        if data is None:
            raise AdapterError("DataHub GraphQL returned no data block")
        return data

    @contextmanager
    def _record(self, tool: str, arguments: dict[str, object]) -> Iterator[list[str]]:
        """Time a call and append a truthful record of its outcome."""
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
                    source=DataSource.LIVE_DATAHUB,
                )
            )

    # -- port implementation ------------------------------------------------

    def health(self) -> tuple[bool, str]:
        """Probe GMS and report exactly what came back. Never raises."""
        try:
            response = httpx.get(f"{self._gms_url}/health", timeout=min(self._timeout, 10.0))
            if response.status_code < 400:
                return True, f"Connected to DataHub GMS at {self._gms_url}."
            return False, (
                f"DataHub GMS at {self._gms_url} returned HTTP {response.status_code}."
            )
        except httpx.HTTPError as exc:
            return False, f"DataHub GMS at {self._gms_url} is unreachable: {exc}"

    def capabilities(self) -> list[str]:
        return list(LIVE_CAPABILITIES)

    def search_assets(self, query: str, limit: int = 10) -> list[Asset]:
        with self._record("search", {"query": query, "limit": limit}) as urns:
            data = self._graphql(
                _SEARCH_QUERY,
                {
                    "input": {
                        "types": [],
                        "query": query or "*",
                        "start": 0,
                        "count": limit,
                    }
                },
            )
            results = (data.get("searchAcrossEntities") or {}).get("searchResults") or []
            assets = [
                self._to_asset(r["entity"]) for r in results if r.get("entity")
            ]
            urns.extend(a.urn for a in assets)
            return assets

    def get_asset(self, urn: str) -> Asset:
        with self._record("get_dataset", {"urn": urn}) as urns:
            data = self._graphql(_ENTITY_QUERY, {"urn": urn})
            entity = data.get("entity")
            if not entity:
                raise AdapterError(f"DataHub has no entity with URN {urn}")
            asset = self._to_asset(entity)
            urns.append(asset.urn)
            return asset

    def get_lineage(
        self,
        urn: str,
        upstream_depth: int = DEFAULT_LINEAGE_DEPTH,
        downstream_depth: int = DEFAULT_LINEAGE_DEPTH,
    ) -> LineageGraph:
        """Fetch the anchor plus its upstream and downstream neighbourhoods.

        DataHub answers each direction separately. The two result sets are
        merged, and edges are reconstructed from the ``degree`` of each hit so
        the returned graph carries the same ``upstreams``/``downstreams`` shape
        the fixture graph does.
        """
        with self._record(
            "get_lineage",
            {
                "urn": urn,
                "upstream_depth": upstream_depth,
                "downstream_depth": downstream_depth,
            },
        ) as urns:
            anchor_entity = self._graphql(_ENTITY_QUERY, {"urn": urn}).get("entity")
            if not anchor_entity:
                raise AdapterError(f"DataHub has no entity with URN {urn}")

            collected: dict[str, Asset] = {}
            anchor = self._to_asset(anchor_entity)
            collected[anchor.urn] = anchor

            # Node URNs per direction, keyed by hop distance from the anchor.
            # DataHub returns a flat result set, so the edge structure has to be
            # rebuilt from each hit's ``degree``.
            by_direction: dict[str, dict[int, list[str]]] = {
                "UPSTREAM": {},
                "DOWNSTREAM": {},
            }

            for direction, depth in (
                ("UPSTREAM", upstream_depth),
                ("DOWNSTREAM", downstream_depth),
            ):
                data = self._graphql(
                    _LINEAGE_QUERY,
                    {"urn": urn, "direction": direction, "depth": max(1, depth)},
                )
                results = (data.get("searchAcrossLineage") or {}).get("searchResults") or []
                for result in results:
                    entity = result.get("entity")
                    if not entity:
                        continue
                    asset = self._to_asset(entity)
                    collected.setdefault(asset.urn, asset)
                    degree = int(result.get("degree") or 1)
                    by_direction[direction].setdefault(degree, []).append(asset.urn)

            self._link_edges(anchor.urn, collected, by_direction)

            ordered = list(collected.values())
            urns.extend(a.urn for a in ordered)
            return LineageGraph(assets=ordered, source=DataSource.LIVE_DATAHUB)

    @staticmethod
    def _link_edges(
        anchor_urn: str,
        collected: dict[str, Asset],
        by_direction: dict[str, dict[int, list[str]]],
    ) -> None:
        """Rebuild ``upstreams``/``downstreams`` from hop distances.

        DataHub's ``searchAcrossLineage`` returns a flat list annotated with a
        ``degree`` (hops from the anchor) rather than an adjacency list. The
        chain is reconstructed by connecting each degree-*n* node to the
        degree-*(n-1)* nodes, with degree 1 attaching to the anchor itself.

        This is an approximation and is documented as such: for a linear chain -
        which is exactly what the patient and billing branches are - it recovers
        the true edges. For a graph where two nodes at the same depth have
        different parents, it can connect a node to a sibling's parent. The
        impact analysis depends on *reachability*, which this preserves; it does
        not depend on the precise parent of each node.
        """
        for direction, levels in by_direction.items():
            if not levels:
                continue
            for degree in sorted(levels):
                parents = [anchor_urn] if degree == 1 else levels.get(degree - 1, [])
                if not parents:
                    continue
                for child_urn in levels[degree]:
                    child = collected.get(child_urn)
                    if child is None:
                        continue
                    for parent_urn in parents:
                        parent = collected.get(parent_urn)
                        if parent is None or parent_urn == child_urn:
                            continue
                        # UPSTREAM results are ancestors of the anchor, so the
                        # edge points from the result toward the anchor.
                        if direction == "UPSTREAM":
                            src, dst = child, parent
                        else:
                            src, dst = parent, child
                        if dst.urn not in src.downstreams:
                            src.downstreams.append(dst.urn)
                        if src.urn not in dst.upstreams:
                            dst.upstreams.append(src.urn)

    def drain_calls(self) -> list[McpCallRecord]:
        drained, self._calls = self._calls, []
        return drained

    # -- translation --------------------------------------------------------

    def _to_asset(self, entity: dict[str, Any]) -> Asset:
        """Translate a GraphQL entity into the domain :class:`Asset`.

        Fields DataHub does not supply are left empty rather than guessed. An
        asset with no recorded owners must come back with an empty owner list,
        not a plausible-looking default.
        """
        urn = entity.get("urn")
        if not urn:
            raise AdapterError(f"DataHub entity has no URN: {entity!r}")

        entity_type = (entity.get("type") or "").upper()
        properties = entity.get("properties") or {}
        name = entity.get("name") or properties.get("name") or urn.split(",")[-2:-1] or urn
        if isinstance(name, list):
            name = name[0] if name else urn

        platform = ((entity.get("platform") or {}).get("name")) or "unknown"
        subtypes = (entity.get("subTypes") or {}).get("typeNames") or []

        return Asset(
            urn=urn,
            name=str(name),
            kind=self._kind_for(entity_type, subtypes),
            platform=platform,
            description=properties.get("description"),
            tags=self._tags(entity),
            owners=self._owners(entity),
            schema_fields=self._schema_fields(entity),
            # Edge lists are populated by the lineage query; a single-entity
            # fetch legitimately does not know them.
            upstreams=[],
            downstreams=[],
            source=DataSource.LIVE_DATAHUB,
            datahub_url=self._entity_url(urn, entity_type),
        )

    @staticmethod
    def _kind_for(entity_type: str, subtypes: list[str]) -> AssetKind:
        """Classify an entity, preferring the DataHub subtype when present."""
        if entity_type in ("MLMODEL",):
            return AssetKind.ML_MODEL
        if entity_type in ("MLMODEL_DEPLOYMENT", "MLMODELDEPLOYMENT"):
            return AssetKind.ENDPOINT
        lowered = {s.lower() for s in subtypes}
        if "feature table" in lowered:
            return AssetKind.FEATURE_TABLE
        return AssetKind.DATASET

    @staticmethod
    def _tags(entity: dict[str, Any]) -> list[str]:
        wrapper = entity.get("tags") or {}
        names: list[str] = []
        for association in wrapper.get("tags") or []:
            name = (association.get("tag") or {}).get("name")
            if name:
                names.append(str(name))
        return names

    @staticmethod
    def _owners(entity: dict[str, Any]) -> list[Owner]:
        ownership = entity.get("ownership") or {}
        owners: list[Owner] = []
        for record in ownership.get("owners") or []:
            owner = record.get("owner") or {}
            owner_urn = owner.get("urn")
            if not owner_urn:
                continue
            raw_type = record.get("type") or "TECHNICAL_OWNER"
            owners.append(
                Owner(
                    urn=owner_urn,
                    display_name=owner.get("name") or owner.get("username") or owner_urn,
                    type=_OWNER_TYPES.get(str(raw_type).upper(), "TECHNICAL_OWNER"),  # type: ignore[arg-type]
                    contact=None,
                )
            )
        return owners

    @staticmethod
    def _schema_fields(entity: dict[str, Any]) -> list[SchemaField]:
        schema = entity.get("schemaMetadata") or {}
        fields: list[SchemaField] = []
        for field in schema.get("fields") or []:
            path = field.get("fieldPath")
            if not path:
                continue
            fields.append(
                SchemaField(
                    name=path,
                    native_type=field.get("nativeDataType") or "UNKNOWN",
                    nullable=bool(field.get("nullable", True)),
                    description=field.get("description"),
                )
            )
        return fields

    def _entity_url(self, urn: str, entity_type: str) -> str:
        path = _ENTITY_PATH.get(entity_type, "dataset")
        return f"{self._frontend_url}/{path}/{urn}"
