"""Live ``WritebackPort`` over the DataHub Python SDK.

The one mutation LineageMedic performs: attach incident tags and an incident
note to the assets in the blast radius, after a human has approved it.

Three properties this module must hold, each of which the fixture adapter also
holds so the two behave identically at the boundary:

1.  **Approval precedes mutation.** An unapproved call returns
    ``BLOCKED_PENDING_APPROVAL`` and emits nothing. This is checked before any
    network call is constructed, not after.
2.  **Writes are additive.** Existing tags and documentation on an asset belong
    to other teams. The adapter reads the current ``globalTags`` aspect and
    merges, so an incident tag never removes a tag someone else put there.
3.  **A receipt reports what happened, not what was attempted.** ``APPLIED`` is
    returned only when the emit succeeded *and* a subsequent read-back confirmed
    the tag is present in DataHub. A partial or unverifiable write is reported
    as ``FAILED`` with the error text.

Rule 3 is the reason :meth:`DataHubWritebackAdapter.verify_tags` exists and is
called on the success path rather than being left to the test suite: the
application should not be able to claim a successful writeback it has not
observed.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from lineagemedic.adapters.tags import TagReadError, read_tag_urns, union_tags
from lineagemedic.models import DataSource, WritebackReceipt, WritebackStatus

if TYPE_CHECKING:  # pragma: no cover - typing only
    from datahub.emitter.rest_emitter import DatahubRestEmitter


#: ``TagReadError`` is re-exported because it moved to
#: :mod:`lineagemedic.adapters.tags` when ``scripts/ingest_lineage.py`` needed
#: the same read-then-merge behaviour. Importers of this module keep working.
__all__ = ["DataHubSdkUnavailableError", "DataHubWritebackAdapter", "TagReadError"]


class DataHubSdkUnavailableError(RuntimeError):
    """Raised when the DataHub SDK is needed but not installed.

    The message names the exact install command rather than surfacing a bare
    ``ImportError``, because this is a configuration problem an operator can fix
    in one step.
    """

    def __init__(self, cause: Exception) -> None:
        super().__init__(
            "The DataHub Python SDK is required for live writeback but is not "
            "installed. Install it with:  pip install 'lineagemedic[datahub]'  "
            f"(underlying import error: {cause})"
        )


def _sdk() -> Any:
    """Import the DataHub SDK on demand.

    Deferred rather than module-level so that importing this module - which the
    API's composition root does unconditionally - does not require the SDK to be
    present in a fixture-mode install.
    """
    try:
        from datahub.emitter.mce_builder import make_tag_urn
        from datahub.emitter.mcp import MetadataChangeProposalWrapper
        from datahub.emitter.rest_emitter import DatahubRestEmitter
        from datahub.metadata.schema_classes import (
            AuditStampClass,
            EditableDatasetPropertiesClass,
            GlobalTagsClass,
            TagAssociationClass,
        )
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise DataHubSdkUnavailableError(exc) from exc

    return SimpleNamespace(
        make_tag_urn=make_tag_urn,
        MetadataChangeProposalWrapper=MetadataChangeProposalWrapper,
        DatahubRestEmitter=DatahubRestEmitter,
        AuditStampClass=AuditStampClass,
        EditableDatasetPropertiesClass=EditableDatasetPropertiesClass,
        GlobalTagsClass=GlobalTagsClass,
        TagAssociationClass=TagAssociationClass,
    )

logger = logging.getLogger("lineagemedic.adapters.datahub_sdk")

#: Actor recorded on the audit stamp. A service identity, never a real person.
_WRITEBACK_ACTOR = "urn:li:corpuser:datahub"

_ENTITY_PATH = {
    "mlModel": "mlModel",
    "mlModelDeployment": "mlModelDeployment",
    "dataset": "dataset",
}


def _entity_type_for(urn: str) -> str:
    """Infer the DataHub entity type from a URN prefix.

    ``dataJob`` is here because the ML half of the live lineage chain is bridged
    by datajob entities (see ``_ml_bridge_mcps`` in ``scripts/ingest_lineage.py``),
    so they legitimately appear in a blast radius and become writeback targets.
    GMS rejects a proposal whose ``entityType`` disagrees with its URN, so
    defaulting these to ``dataset`` failed the write with "entityType dataset
    does not match the entity type datajob".
    """
    if urn.startswith("urn:li:mlModelDeployment:"):
        return "mlModelDeployment"
    if urn.startswith("urn:li:mlModel:"):
        return "mlModel"
    if urn.startswith("urn:li:dataJob:"):
        return "dataJob"
    if urn.startswith("urn:li:dataFlow:"):
        return "dataFlow"
    return "dataset"


class DataHubWritebackAdapter:
    """Emits incident metadata to a live DataHub and verifies the result."""

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
        self._sdk = _sdk()
        self._emitter: DatahubRestEmitter = self._sdk.DatahubRestEmitter(
            gms_server=self._gms_url,
            token=token or None,
        )

    @property
    def source(self) -> DataSource:
        return DataSource.LIVE_DATAHUB

    # -- read helpers -------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _existing_tags(self, urn: str) -> list[str]:
        """Current tag URNs on an entity, so a merge does not clobber them.

        Raises :class:`TagReadError` if the current state cannot be established.
        That is deliberate, and was not the original design: an earlier version
        returned an empty list on failure and let the caller write only the
        incident tags. Because ``globalTags`` is a whole-aspect replace, "I could
        not read the tags" then became "the asset now has no tags but mine",
        and a live run destroyed the healthcare/phi/silver tags on
        ``staging_patients``.

        The query and its failure handling live in
        :mod:`lineagemedic.adapters.tags` because ``scripts/ingest_lineage.py``
        must merge on exactly the same terms.
        """
        return read_tag_urns(
            self._gms_url, urn, headers=self._headers(), timeout=self._timeout
        )

    def verify_tags(self, urn: str, expected_tags: list[str]) -> tuple[bool, list[str]]:
        """Read an entity back and report which expected tags are really present.

        This is the check that separates "we sent a write" from "DataHub has the
        data". Its result decides whether the receipt says ``APPLIED``.

        A read failure is reported as "nothing verified" rather than propagating,
        because an unverifiable write must be reported as ``FAILED``, which is
        what the caller does with a non-empty ``missing`` list.
        """
        try:
            present = set(self._existing_tags(urn))
        except TagReadError:
            return False, list(expected_tags)
        expected_urns = [self._sdk.make_tag_urn(t) for t in expected_tags]
        missing = [t for t, u in zip(expected_tags, expected_urns, strict=True) if u not in present]
        return (not missing), missing

    # -- port implementation ------------------------------------------------

    def write_incident_metadata(
        self,
        *,
        target_urns: list[str],
        tags: list[str],
        note: str,
        incident_id: str,
        approved: bool,
    ) -> WritebackReceipt:
        """Attach incident tags and a note, then verify by reading back."""
        if not approved:
            # The gate, enforced before any request is built.
            return WritebackReceipt(
                status=WritebackStatus.BLOCKED_PENDING_APPROVAL,
                target_urns=list(target_urns),
                note=(
                    "Writeback blocked: human approval has not been granted for "
                    f"incident {incident_id}."
                ),
                source=DataSource.LIVE_DATAHUB,
            )

        written_urns: list[str] = []
        aspects_written: list[str] = []
        failures: list[str] = []

        for urn in target_urns:
            entity_type = _entity_type_for(urn)
            try:
                merged = self._merge_tags(urn, tags)
                self._emitter.emit(
                    self._sdk.MetadataChangeProposalWrapper(
                        entityType=entity_type,
                        entityUrn=urn,
                        aspect=merged,
                    )
                )
                aspects_written.append("globalTags")

                # ``editableDatasetProperties`` is a dataset aspect. Emitting it
                # against a model, endpoint, or datajob URN is rejected by GMS,
                # so those targets receive the incident tag only.
                if entity_type == "dataset":
                    self._emitter.emit(
                        self._sdk.MetadataChangeProposalWrapper(
                            entityType="dataset",
                            entityUrn=urn,
                            aspect=self._sdk.EditableDatasetPropertiesClass(
                                description=note,
                                lastModified=self._sdk.AuditStampClass(
                                    time=0, actor=_WRITEBACK_ACTOR
                                ),
                            ),
                        )
                    )
                    aspects_written.append("editableDatasetProperties")
                written_urns.append(urn)
            except Exception as exc:  # surfaced in the receipt, never swallowed
                failures.append(f"{urn}: {exc}")

        if failures:
            return WritebackReceipt(
                status=WritebackStatus.FAILED,
                target_urns=list(target_urns),
                aspects_written=sorted(set(aspects_written)),
                tags_added=[],
                note=(
                    f"Writeback for incident {incident_id} failed on "
                    f"{len(failures)} of {len(target_urns)} target(s)."
                ),
                source=DataSource.LIVE_DATAHUB,
                error="; ".join(failures),
            )

        # Verification: only now can this report success truthfully.
        unverified: list[str] = []
        for urn in written_urns:
            ok, missing = self.verify_tags(urn, tags)
            if not ok:
                unverified.append(f"{urn} missing {missing}")

        if unverified:
            return WritebackReceipt(
                status=WritebackStatus.FAILED,
                target_urns=list(target_urns),
                aspects_written=sorted(set(aspects_written)),
                tags_added=[],
                note=(
                    "Writeback was emitted but could not be verified by reading the "
                    "metadata back from DataHub. Reporting failure rather than an "
                    "unconfirmed success."
                ),
                source=DataSource.LIVE_DATAHUB,
                error="; ".join(unverified),
            )

        return WritebackReceipt(
            status=WritebackStatus.APPLIED,
            target_urns=written_urns,
            aspects_written=sorted(set(aspects_written)),
            tags_added=list(tags),
            note=(
                f"Attached {len(tags)} incident tag(s) and an incident note to "
                f"{len(written_urns)} asset(s) for incident {incident_id}. Each write "
                "was verified by reading the metadata back from DataHub."
            ),
            datahub_urls=[self._entity_url(u) for u in written_urns],
            source=DataSource.LIVE_DATAHUB,
        )

    # -- internals ----------------------------------------------------------

    def _merge_tags(self, urn: str, tags: list[str]) -> Any:
        """Union the incident tags with whatever the asset already carries."""
        merged = union_tags(
            self._existing_tags(urn), [self._sdk.make_tag_urn(t) for t in tags]
        )
        return self._sdk.GlobalTagsClass(
            tags=[self._sdk.TagAssociationClass(tag=t) for t in merged]
        )

    def _entity_url(self, urn: str) -> str:
        path = _ENTITY_PATH.get(_entity_type_for(urn), "dataset")
        return f"{self._frontend_url}/{path}/{urn}"

    def health(self) -> tuple[bool, str]:
        """Report whether the write path can reach DataHub. Never raises."""
        try:
            self._emitter.test_connection()
            return True, f"DataHub REST sink reachable at {self._gms_url}."
        except Exception as exc:
            return False, f"DataHub REST sink unreachable at {self._gms_url}: {exc}"
