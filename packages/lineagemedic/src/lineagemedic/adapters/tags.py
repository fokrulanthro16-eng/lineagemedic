"""Reading and merging DataHub ``globalTags``.

``globalTags`` is a whole-aspect replace in DataHub, not an append. Emitting the
aspect with only the tags you know about therefore *deletes* every tag you did
not name. That is a data-loss bug rather than a cosmetic one: it silently
destroys tags owned by other teams, and it wiped the incident tags a previous
LineageMedic writeback had attached.

Both writers in this repository must therefore read the current aspect and union
against it before emitting:

* :class:`~lineagemedic.adapters.datahub_sdk.DataHubWritebackAdapter`, which
  attaches incident tags during a writeback, and
* ``scripts/ingest_lineage.py``, which re-asserts the fixture graph's tags.

The logic lives here, in one place, so the two cannot drift. Splitting the
network call from the parsing and the union also makes the merge testable
without a running DataHub -- the parts most likely to be wrong are pure.
"""

from __future__ import annotations

import httpx

#: Tag URNs are read through GraphQL rather than the aspect API because the
#: aspect endpoint returns raw tag URNs without confirming the entity exists.
#:
#: ``MLModelDeployment`` is deliberately absent: it is not a type in DataHub
#: v1.6.0's GraphQL schema, and naming it fails the *whole* query at validation
#: rather than just that fragment. The aliases keep the remaining fragments from
#: conflicting on the shape of ``tags``.
TAGS_QUERY = """
query tags($urn: String!) {
  entity(urn: $urn) { ... on Dataset { dsTags: tags { tags { tag { urn } } } }
                      ... on MLModel { mlTags: tags { tags { tag { urn } } } }
                      ... on DataJob { jobTags: tags { tags { tag { urn } } } } }
}
"""


class TagReadError(RuntimeError):
    """Raised when an entity's current tags cannot be established.

    Because ``globalTags`` is replaced wholesale rather than appended to, a
    write that proceeds without knowing the current tags will delete them. This
    error exists so that an unreadable current state stops the write instead of
    silently narrowing it.
    """


def parse_tag_urns(payload: dict) -> list[str]:
    """Extract tag URNs from a GraphQL response body.

    Raises :class:`TagReadError` when the response carries an ``errors`` array.
    A GraphQL *validation* error is returned as HTTP 200 with that array, so
    ``raise_for_status()`` does not fire and a structurally broken query is
    otherwise indistinguishable from an asset that simply has no tags. Treating
    the two alike is what previously turned "I could not read the tags" into
    "the asset now has no tags but mine".
    """
    if payload.get("errors"):
        messages = "; ".join(e.get("message", "?") for e in payload["errors"])
        raise TagReadError(messages)

    entity = (payload.get("data") or {}).get("entity") or {}
    wrapper = entity.get("dsTags") or entity.get("mlTags") or entity.get("jobTags") or {}

    tag_urns: list[str] = []
    for association in wrapper.get("tags") or []:
        tag_urn = (association.get("tag") or {}).get("urn")
        if tag_urn:
            tag_urns.append(str(tag_urn))
    return tag_urns


def union_tags(existing: list[str], incoming: list[str]) -> list[str]:
    """Union two tag-URN lists, preserving order and dropping duplicates.

    Order is preserved rather than sorted so a re-ingest produces no spurious
    diff in the catalog, and existing tags come first because they were there
    first. Both inputs are assumed to be fully-qualified tag URNs.
    """
    merged: list[str] = []
    for tag_urn in [*existing, *incoming]:
        if tag_urn not in merged:
            merged.append(tag_urn)
    return merged


def read_tag_urns(
    gms_url: str,
    urn: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 10.0,
) -> list[str]:
    """Current tag URNs on an entity, so a merge does not clobber them.

    Raises :class:`TagReadError` if the current state cannot be established for
    any reason -- transport failure, unparseable body, or a GraphQL error. The
    caller must not fall back to writing only its own tags; see the module
    docstring.
    """
    try:
        response = httpx.post(
            f"{gms_url}/api/graphql",
            json={"query": TAGS_QUERY, "variables": {"urn": urn}},
            headers=headers or {},
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise TagReadError(
            f"Could not read existing tags for {urn}, so a safe merge is "
            f"impossible and the write was not attempted: {exc}"
        ) from exc

    try:
        return parse_tag_urns(payload)
    except TagReadError as exc:
        raise TagReadError(
            f"Could not read existing tags for {urn}, so a safe merge is "
            f"impossible and the write was not attempted: {exc}"
        ) from exc
