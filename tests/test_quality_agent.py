"""Quality Agent tests.

These assert on exact counts. That is intentional: the seed is deterministic, so
a changed number means either the generator or the check logic moved, and both
are things a reviewer should be told about rather than have silently absorbed by
a tolerant assertion.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lineagemedic.agents.quality import QualityAgent
from lineagemedic.data.seed_healthcare import (
    INVALID_AGE_ROWS,
    NULL_ADMISSION_ROWS,
    STAGING_STALENESS_HOURS,
    TOTAL_PATIENTS,
)
from lineagemedic.models import CheckStatus
from lineagemedic.scenarios import CRITICAL, HEALTHY, WARNING


def _by_id(checks, check_id):
    return next(c for c in checks if c.check_id == check_id)


def test_detects_planted_invalid_ages(db_path: Path, now) -> None:
    checks = QualityAgent(db_path, now=now).run(CRITICAL)
    age = _by_id(checks, "age-range")

    assert age.status is CheckStatus.FAIL
    assert age.failing_rows == INVALID_AGE_ROWS
    assert age.rows_scanned == TOTAL_PATIENTS
    # The samples must be real values pulled from the table, not placeholders.
    assert age.sample_failing_values
    assert all(int(v) < 0 or int(v) > 130 for v in age.sample_failing_values)


def test_detects_planted_null_admission_dates(db_path: Path, now) -> None:
    checks = QualityAgent(db_path, now=now).run(CRITICAL)
    nulls = _by_id(checks, "admission-null-rate")

    assert nulls.status is CheckStatus.FAIL
    assert nulls.failing_rows == NULL_ADMISSION_ROWS


def test_invalid_ages_propagate_into_features(db_path: Path, now) -> None:
    """The causal chain the demo narrates must hold in the data itself."""
    checks = QualityAgent(db_path, now=now).run(CRITICAL)

    staging = _by_id(checks, "age-range-propagated")
    features = _by_id(checks, "feature-unknown-bucket")

    # Staging filters NULL admissions but not out-of-range ages, so every
    # invalid age survives and becomes exactly one 'unknown' feature bucket.
    assert staging.failing_rows == INVALID_AGE_ROWS
    assert features.failing_rows == INVALID_AGE_ROWS


def test_freshness_reports_no_failing_rows(db_path: Path, now) -> None:
    """Staleness must not masquerade as row-level corruption.

    A freshness breach means the table is late, not that its rows are wrong.
    Reporting failing rows here would push the warning scenario to critical.
    """
    checks = QualityAgent(db_path, now=now).run(CRITICAL)
    freshness = _by_id(checks, "staging-freshness")

    assert freshness.status is CheckStatus.FAIL
    assert freshness.observed_value == pytest.approx(STAGING_STALENESS_HOURS, abs=0.1)
    assert freshness.failing_rows == 0


def test_healthy_branch_passes_every_check(db_path: Path, now) -> None:
    checks = QualityAgent(db_path, now=now).run(HEALTHY)

    assert checks, "the healthy scenario must actually execute checks"
    assert all(c.status is CheckStatus.PASS for c in checks)
    assert all(c.failing_rows == 0 for c in checks)


def test_warning_scenario_is_stale_but_structurally_intact(db_path: Path, now) -> None:
    checks = QualityAgent(db_path, now=now).run(WARNING)

    assert _by_id(checks, "staging-freshness-sla").status is CheckStatus.FAIL
    # Row count still passes: the data is late, not missing.
    assert _by_id(checks, "staging-row-floor").status is CheckStatus.PASS
    assert all(c.failing_rows == 0 for c in checks)


def test_checks_carry_real_urns(db_path: Path, now) -> None:
    for check in QualityAgent(db_path, now=now).run(CRITICAL):
        assert check.dataset_urn.startswith("urn:li:dataset:")


def test_missing_database_raises_rather_than_returning_empty(tmp_path: Path, now) -> None:
    """A missing warehouse must fail loudly, never look like clean data."""
    agent = QualityAgent(tmp_path / "does-not-exist.db", now=now)
    with pytest.raises(FileNotFoundError, match="healthcare database not found"):
        agent.run(CRITICAL)
