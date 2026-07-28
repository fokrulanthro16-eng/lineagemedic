"""Port definitions for the two external systems LineageMedic talks to.

`MetadataPort` covers reads (search, schema, ownership, lineage) and is
implemented by the MCP client. `WritebackPort` covers the one mutation the
application performs. They are separate protocols on purpose: the read path is
exercised on every diagnosis, while the write path is gated behind human
approval and is the only place the application can change DataHub state.

Implementations live beside this module:

* :mod:`lineagemedic.adapters.fixture` - committed fixtures, no network. Used
  in Demo Fixture Mode and in every unit test.
* :mod:`lineagemedic.adapters.live` - real DataHub MCP server plus the DataHub
  Python SDK. Wired up in the containerized environment.

Both implementations must return :class:`~lineagemedic.models.DataSource`
truthfully on every payload. That flag is what the UI banner and the
anti-fabrication tests key off.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from lineagemedic.models import (
    Asset,
    DataSource,
    LineageGraph,
    McpCallRecord,
    WritebackReceipt,
)


class AdapterError(RuntimeError):
    """Raised when an adapter cannot satisfy a request.

    Adapters raise rather than returning empty or synthetic data: a caller must
    never be able to mistake "the backend was unreachable" for "DataHub said
    there is nothing here".
    """


@runtime_checkable
class MetadataPort(Protocol):
    """Read-side access to DataHub metadata.

    Every method records an :class:`McpCallRecord` via :meth:`drain_calls`, so
    the workflow can attach a verifiable tool-call trace to its output.
    """

    @property
    def source(self) -> DataSource:
        """Provenance of everything this adapter returns."""
        ...

    def health(self) -> tuple[bool, str]:
        """Return ``(reachable, human_readable_detail)``. Must not raise."""
        ...

    def capabilities(self) -> list[str]:
        """Names of the MCP tools this adapter can invoke."""
        ...

    def search_assets(self, query: str, limit: int = 10) -> list[Asset]:
        """Find assets matching a free-text query."""
        ...

    def get_asset(self, urn: str) -> Asset:
        """Fetch one asset with schema, ownership, and tags.

        Raises :class:`AdapterError` if the URN is unknown.
        """
        ...

    def get_lineage(
        self, urn: str, upstream_depth: int = 3, downstream_depth: int = 3
    ) -> LineageGraph:
        """Traverse lineage in both directions from ``urn``."""
        ...

    def drain_calls(self) -> list[McpCallRecord]:
        """Return the calls recorded since the last drain, and reset the buffer."""
        ...


@runtime_checkable
class WritebackPort(Protocol):
    """Write-side access to DataHub metadata.

    The single mutation LineageMedic performs: attach incident knowledge to the
    affected assets. Implementations must use patch/merge semantics so unrelated
    tags, owners, and documentation survive untouched.
    """

    @property
    def source(self) -> DataSource:
        """Provenance flag for receipts this adapter issues."""
        ...

    def write_incident_metadata(
        self,
        *,
        target_urns: list[str],
        tags: list[str],
        note: str,
        incident_id: str,
        approved: bool,
    ) -> WritebackReceipt:
        """Attach incident tags and a note to ``target_urns``.

        Implementations MUST refuse when ``approved`` is ``False`` and return a
        receipt with
        :attr:`~lineagemedic.models.WritebackStatus.BLOCKED_PENDING_APPROVAL`
        rather than performing a partial write. The approval gate is enforced
        here as well as in the Safety Agent so that no call path can bypass it.
        """
        ...

