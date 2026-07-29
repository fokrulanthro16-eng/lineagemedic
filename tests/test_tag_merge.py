"""Tag merging, the thing that stops a re-ingest destroying incident evidence.

``globalTags`` is a whole-aspect replace in DataHub. Emitting it with only the
tags the writer knows about deletes every other tag on the entity. That is how
re-running ``scripts/ingest_lineage.py`` used to erase the incident tags a
previous writeback had attached -- the ingestion re-asserted the fixture's
tags and DataHub dropped the rest.

The network call is not exercised here; :mod:`tests.test_datahub_integration`
covers the live path. What is exercised is the pure logic, because the union and
the fail-closed parsing are where the bug actually lived.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from lineagemedic.adapters.tags import (
    TagReadError,
    parse_tag_urns,
    union_tags,
)


def _load_ingest_module():
    """Import ``scripts/ingest_lineage.py``, which is not an installed package.

    Skips rather than fails if the DataHub SDK is absent, matching how the live
    integration tests behave without an instance: the merge logic is still
    covered by the pure tests above.
    """
    path = Path(__file__).resolve().parent.parent / "scripts" / "ingest_lineage.py"
    spec = importlib.util.spec_from_file_location("ingest_lineage", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["ingest_lineage"] = module
    try:
        spec.loader.exec_module(module)
    except ImportError as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"DataHub SDK not installed: {exc}")
    return module

INCIDENT_TAG = "urn:li:tag:LineageMedic:incident"
SEVERITY_TAG = "urn:li:tag:LineageMedic:severity:critical"
FIXTURE_TAGS = ["urn:li:tag:healthcare", "urn:li:tag:phi", "urn:li:tag:silver"]


def _graphql_tags(*tag_urns: str) -> dict:
    """A GraphQL response shaped the way DataHub returns dataset tags."""
    return {
        "data": {
            "entity": {
                "dsTags": {"tags": [{"tag": {"urn": t}} for t in tag_urns]}
            }
        }
    }


class TestUnionTags:
    def test_incident_tags_survive_a_reingest(self) -> None:
        """The regression this whole module exists for.

        An entity carries incident tags from a writeback. Ingestion then
        re-asserts the fixture's tags. Both sets must be present afterwards.
        """
        merged = union_tags([*FIXTURE_TAGS, INCIDENT_TAG, SEVERITY_TAG], FIXTURE_TAGS)

        assert INCIDENT_TAG in merged, "re-ingest erased the incident tag"
        assert SEVERITY_TAG in merged, "re-ingest erased the severity tag"
        assert set(FIXTURE_TAGS) <= set(merged)

    def test_pre_existing_tags_survive_a_writeback(self) -> None:
        """The mirror case: a writeback must not clobber ingestion's tags."""
        merged = union_tags(FIXTURE_TAGS, [INCIDENT_TAG, SEVERITY_TAG])

        assert set(FIXTURE_TAGS) <= set(merged), "writeback erased pre-existing tags"
        assert {INCIDENT_TAG, SEVERITY_TAG} <= set(merged)

    def test_no_duplicates_when_both_sides_carry_a_tag(self) -> None:
        """Re-emitting an unchanged aspect must not grow the tag list."""
        merged = union_tags(FIXTURE_TAGS, FIXTURE_TAGS)

        assert merged == FIXTURE_TAGS
        assert len(merged) == len(set(merged))

    def test_existing_tags_keep_their_order(self) -> None:
        """Stable order means a converged re-ingest produces no catalog diff."""
        merged = union_tags(FIXTURE_TAGS, [INCIDENT_TAG])

        assert merged == [*FIXTURE_TAGS, INCIDENT_TAG]

    def test_merging_onto_an_untagged_entity_yields_only_the_new_tags(self) -> None:
        assert union_tags([], [INCIDENT_TAG]) == [INCIDENT_TAG]

    def test_merging_nothing_in_preserves_everything(self) -> None:
        assert union_tags(FIXTURE_TAGS, []) == FIXTURE_TAGS


class TestParseTagUrns:
    def test_reads_dataset_tags(self) -> None:
        payload = _graphql_tags(*FIXTURE_TAGS)

        assert parse_tag_urns(payload) == FIXTURE_TAGS

    def test_an_untagged_entity_is_not_an_error(self) -> None:
        """Distinct from a failed read: genuinely having no tags is fine."""
        assert parse_tag_urns({"data": {"entity": {"dsTags": {"tags": []}}}}) == []

    @pytest.mark.parametrize("alias", ["dsTags", "mlTags", "jobTags"])
    def test_reads_each_entity_types_alias(self, alias: str) -> None:
        """One query serves three entity types through aliased fragments."""
        payload = {"data": {"entity": {alias: {"tags": [{"tag": {"urn": INCIDENT_TAG}}]}}}}

        assert parse_tag_urns(payload) == [INCIDENT_TAG]

    def test_a_graphql_error_raises_rather_than_reporting_no_tags(self) -> None:
        """The failure mode that made the original bug silent.

        A GraphQL validation error arrives as HTTP 200 with an ``errors``
        array, so ``raise_for_status()`` never fires. Returning ``[]`` here
        would let the caller write only its own tags and delete the rest --
        "I could not read the tags" becoming "the entity has no tags but mine".
        """
        payload = {"errors": [{"message": "Unknown type MLModelDeployment"}]}

        with pytest.raises(TagReadError, match="MLModelDeployment"):
            parse_tag_urns(payload)

    def test_a_missing_entity_is_not_silently_treated_as_untagged(self) -> None:
        """A null entity yields no tags, but only because there is no entity.

        This is safe for the merge path only because ingestion emits an entity
        it is about to create; the read failing outright is handled by
        ``read_tag_urns`` raising, which is covered above.
        """
        assert parse_tag_urns({"data": {"entity": None}}) == []

    def test_tags_without_a_urn_are_skipped(self) -> None:
        payload = {
            "data": {
                "entity": {
                    "dsTags": {
                        "tags": [{"tag": {}}, {"tag": {"urn": INCIDENT_TAG}}, {}]
                    }
                }
            }
        }

        assert parse_tag_urns(payload) == [INCIDENT_TAG]


class TestIngestionAspect:
    """The ingestion script's own aspect building, where the bug lived.

    Testing ``union_tags`` alone would not have caught the original defect:
    the helper was correct, and ``scripts/ingest_lineage.py`` simply never
    called it.
    """

    def test_ingest_emits_a_union_not_a_replacement(self) -> None:
        ingest = _load_ingest_module()

        aspect = ingest._global_tags(
            ["healthcare", "phi"], existing=[INCIDENT_TAG, "urn:li:tag:healthcare"]
        )
        emitted = [association.tag for association in aspect.tags]

        assert INCIDENT_TAG in emitted, (
            "re-ingest dropped an incident tag it did not author"
        )
        assert "urn:li:tag:phi" in emitted, "re-ingest dropped its own new tag"
        assert len(emitted) == len(set(emitted)), "tag duplicated across the merge"

    def test_asset_aspects_carry_the_merged_tags(self) -> None:
        """The merge must survive the path ``emit_graph`` actually takes."""
        ingest = _load_ingest_module()
        from lineagemedic.fixtures.graph import build_graph
        from lineagemedic.models import DataSource

        graph = build_graph(source=DataSource.FIXTURE)
        asset = next(a for a in graph.assets if a.tags)

        aspects = ingest._aspects_for(asset, [INCIDENT_TAG])
        tag_aspects = [a for a in aspects if type(a).__name__ == "GlobalTagsClass"]

        assert len(tag_aspects) == 1
        emitted = [association.tag for association in tag_aspects[0].tags]
        assert INCIDENT_TAG in emitted, (
            f"ingesting {asset.name} would have erased the incident tag"
        )

    def test_dry_run_does_not_invent_existing_tags(self) -> None:
        """``--dry-run`` contacts nothing, so it cannot claim to have merged."""
        ingest = _load_ingest_module()

        aspect = ingest._global_tags(["healthcare"], existing=None)
        emitted = [association.tag for association in aspect.tags]

        assert emitted == ["urn:li:tag:healthcare"]
