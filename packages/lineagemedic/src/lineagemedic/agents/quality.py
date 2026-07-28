"""Quality Agent: measure the data, report what is actually there.

This is the only agent that touches the warehouse. It executes each
:class:`~lineagemedic.scenarios.CheckSpec` as real SQL against the bundled
SQLite database and returns measured numbers. No thresholds are pre-evaluated
and no result is assumed: if the seeded data changes, the verdict changes with
it.

Failing-value samples are drawn from the data so the evidence panel can show a
judge the actual offending values (``-7``, ``999``) rather than an assertion
that offending values exist.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from lineagemedic.fixtures.graph import TABLE_TO_URN
from lineagemedic.models import CheckStatus, QualityCheck
from lineagemedic.scenarios import CheckSpec, Scenario

#: How many offending values to surface per failing check.
SAMPLE_LIMIT = 5


class QualityAgent:
    """Executes a scenario's checks against the healthcare database."""

    name = "quality"

    def __init__(self, db_path: Path, *, now: datetime | None = None) -> None:
        self._db_path = Path(db_path)
        # Injectable clock so freshness checks are deterministic under test.
        self._now = now or datetime.now(timezone.utc)

    def run(self, scenario: Scenario) -> list[QualityCheck]:
        """Execute every check in ``scenario`` and return measured results."""
        if not self._db_path.exists():
            raise FileNotFoundError(
                f"healthcare database not found at {self._db_path}. "
                "Run scripts/setup.ps1 or lineagemedic.data.seed_healthcare to build it."
            )
        conn = sqlite3.connect(f"file:{self._db_path}?mode=ro", uri=True)
        try:
            conn.row_factory = sqlite3.Row
            return [self._execute(conn, spec) for spec in scenario.checks]
        finally:
            conn.close()

    # -- check strategies ---------------------------------------------------

    def _execute(self, conn: sqlite3.Connection, spec: CheckSpec) -> QualityCheck:
        if spec.kind == "range":
            return self._range_check(conn, spec)
        if spec.kind == "null_rate":
            return self._null_rate_check(conn, spec)
        if spec.kind == "freshness":
            return self._freshness_check(conn, spec)
        if spec.kind == "row_count":
            return self._row_count_check(conn, spec)
        raise ValueError(f"unsupported check kind: {spec.kind}")

    def _urn_for(self, table: str) -> str:
        urn = TABLE_TO_URN.get(table)
        if urn is None:
            raise ValueError(f"table {table!r} has no mapped DataHub URN")
        return urn

    def _total_rows(self, conn: sqlite3.Connection, table: str) -> int:
        return int(conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])

    def _range_check(self, conn: sqlite3.Connection, spec: CheckSpec) -> QualityCheck:
        """Fraction of rows whose column falls outside the allowed interval."""
        assert spec.column is not None, "range check requires a column"
        total = self._total_rows(conn, spec.table)
        predicate = (
            f"{spec.column} IS NOT NULL AND "
            f"({spec.column} < :lo OR {spec.column} > :hi)"
        )
        params = {"lo": spec.min_value, "hi": spec.max_value}
        failing = int(
            conn.execute(
                f"SELECT COUNT(*) AS n FROM {spec.table} WHERE {predicate}", params
            ).fetchone()["n"]
        )
        samples = [
            str(r[0])
            for r in conn.execute(
                f"SELECT {spec.column} FROM {spec.table} WHERE {predicate} "
                f"ORDER BY {spec.column} LIMIT {SAMPLE_LIMIT}",
                params,
            ).fetchall()
        ]
        ratio = failing / total if total else 0.0
        return self._build(spec, ratio, total, failing, samples)

    def _null_rate_check(self, conn: sqlite3.Connection, spec: CheckSpec) -> QualityCheck:
        """Fraction of rows that are NULL, or that match an explicit predicate.

        The ``where`` override lets a scenario express "rows equal to a sentinel"
        (such as ``age_bucket = 'unknown'``) with the same ratio semantics.
        """
        assert spec.column is not None, "null_rate check requires a column"
        total = self._total_rows(conn, spec.table)
        predicate = spec.where or f"{spec.column} IS NULL"
        failing = int(
            conn.execute(
                f"SELECT COUNT(*) AS n FROM {spec.table} WHERE {predicate}"
            ).fetchone()["n"]
        )
        samples: list[str] = []
        if spec.where:
            samples = [
                str(r[0])
                for r in conn.execute(
                    f"SELECT {spec.column} FROM {spec.table} WHERE {predicate} "
                    f"LIMIT {SAMPLE_LIMIT}"
                ).fetchall()
            ]
        elif failing:
            samples = ["NULL"]
        ratio = failing / total if total else 0.0
        return self._build(spec, ratio, total, failing, samples)

    def _freshness_check(self, conn: sqlite3.Connection, spec: CheckSpec) -> QualityCheck:
        """Hours elapsed since the newest watermark in the column."""
        assert spec.column is not None, "freshness check requires a column"
        total = self._total_rows(conn, spec.table)
        row = conn.execute(
            f"SELECT MAX({spec.column}) AS latest FROM {spec.table}"
        ).fetchone()
        latest_raw = row["latest"]
        if latest_raw is None:
            # No watermark at all is maximally stale, not silently healthy.
            return self._build(spec, float("inf"), total, total, [])
        latest = datetime.fromisoformat(str(latest_raw))
        if latest.tzinfo is None:
            latest = latest.replace(tzinfo=timezone.utc)
        age_hours = (self._now - latest).total_seconds() / 3600.0
        # Freshness reports zero failing *rows*: every row is structurally
        # valid, the table is merely late. Counting all rows as "failing" here
        # would make staleness indistinguishable from mass corruption when
        # severity is derived downstream.
        return self._build(
            spec,
            round(age_hours, 2),
            total,
            0,
            [str(latest_raw)],
        )

    def _row_count_check(self, conn: sqlite3.Connection, spec: CheckSpec) -> QualityCheck:
        """Absolute row count against a floor or ceiling."""
        total = self._total_rows(conn, spec.table)
        return self._build(spec, float(total), total, 0, [])

    # -- shared construction ------------------------------------------------

    def _build(
        self,
        spec: CheckSpec,
        observed: float,
        rows_scanned: int,
        failing_rows: int,
        samples: list[str],
    ) -> QualityCheck:
        """Apply the comparison and assemble the result object."""
        status = (
            CheckStatus.PASS
            if self._passes(observed, spec.threshold, spec.comparison)
            else CheckStatus.FAIL
        )
        return QualityCheck(
            check_id=spec.check_id,
            description=spec.description,
            dataset_urn=self._urn_for(spec.table),
            column=spec.column,
            status=status,
            observed_value=observed,
            threshold=spec.threshold,
            comparison=spec.comparison,
            rows_scanned=rows_scanned,
            failing_rows=failing_rows,
            sample_failing_values=samples,
        )

    @staticmethod
    def _passes(observed: float, threshold: float, comparison: str) -> bool:
        if comparison == "lte":
            return observed <= threshold
        if comparison == "gte":
            return observed >= threshold
        if comparison == "eq":
            return observed == threshold
        raise ValueError(f"unsupported comparison: {comparison}")
