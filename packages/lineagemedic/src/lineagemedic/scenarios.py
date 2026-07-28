"""The three runnable incident scenarios.

A scenario is a declarative description of *what to inspect* - which tables,
which columns, which thresholds, and which asset anchors the investigation. The
agents are generic: they execute whatever a scenario points them at and report
what they measure. Nothing about the outcome is hard-coded here.

That separation is what makes the demo honest. The critical scenario is expected
to come back critical because the seeded data really does contain 37
out-of-range ages, not because a severity is stapled to the scenario. Change the
data and the same scenario yields a different verdict.

``expected_severity`` is documentation and a test oracle - the workflow never
reads it when deciding an outcome.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from lineagemedic.fixtures.graph import (
    URN_BILLING_SUMMARY,
    URN_PATIENT_FEATURES,
    URN_RAW_BILLING,
    URN_RAW_PATIENTS,
    URN_STAGING_PATIENTS,
)
from lineagemedic.models import ScenarioSummary, Severity


@dataclass(frozen=True)
class CheckSpec:
    """One data-quality check to execute against the SQLite database.

    ``kind`` selects the measurement strategy in the Quality Agent:

    * ``range``      - fraction of rows where ``column`` falls outside
      ``[min_value, max_value]``.
    * ``null_rate``  - fraction of rows where ``column`` is NULL.
    * ``freshness``  - hours since the maximum value of ``column``, compared
      against ``threshold`` in hours.
    * ``row_count``  - absolute row count compared against ``threshold``.
    """

    check_id: str
    description: str
    table: str
    kind: Literal["range", "null_rate", "freshness", "row_count"]
    threshold: float
    comparison: Literal["lte", "gte", "eq"]
    column: str | None = None
    min_value: float | None = None
    max_value: float | None = None
    #: Optional SQL predicate narrowing the rows under test, used by the
    #: warning scenario to inspect a recent window rather than all history.
    where: str | None = None


@dataclass(frozen=True)
class Scenario:
    """A complete, runnable investigation."""

    scenario_id: str
    title: str
    description: str
    #: The asset the investigation starts from; impact radiates downstream.
    anchor_urn: str
    checks: list[CheckSpec]
    #: Assets that must be evaluated for impact even if lineage does not reach
    #: them. Used to prove the billing branch is explicitly considered and
    #: cleared, rather than merely never looked at.
    control_urns: list[str] = field(default_factory=list)
    expected_severity: Severity = Severity.HEALTHY

    def summary(self) -> ScenarioSummary:
        return ScenarioSummary(
            scenario_id=self.scenario_id,
            title=self.title,
            description=self.description,
            expected_severity=self.expected_severity,
        )


CRITICAL = Scenario(
    scenario_id="critical-age-corruption",
    title="Invalid patient ages reaching the production readmission model",
    description=(
        "Out-of-range values in raw_patients.age pass through staging without "
        "validation and degrade the age_bucket feature consumed by the live "
        "readmission risk endpoint. Missing admission dates compound the loss, "
        "and the staging table is stale."
    ),
    anchor_urn=URN_RAW_PATIENTS,
    checks=[
        CheckSpec(
            check_id="age-range",
            description="raw_patients.age must fall within a plausible human range (0-130).",
            table="raw_patients",
            kind="range",
            column="age",
            min_value=0,
            max_value=130,
            threshold=0.01,
            comparison="lte",
        ),
        CheckSpec(
            check_id="admission-null-rate",
            description="raw_patients.admission_date is required by the feature pipeline.",
            table="raw_patients",
            kind="null_rate",
            column="admission_date",
            threshold=0.02,
            comparison="lte",
        ),
        CheckSpec(
            check_id="age-range-propagated",
            description="staging_patients.age must be validated before feature computation.",
            table="staging_patients",
            kind="range",
            column="age",
            min_value=0,
            max_value=130,
            threshold=0.01,
            comparison="lte",
        ),
        CheckSpec(
            check_id="staging-freshness",
            description="staging_patients must refresh at least every 24 hours.",
            table="staging_patients",
            kind="freshness",
            column="last_refreshed_at",
            threshold=24.0,
            comparison="lte",
        ),
        CheckSpec(
            check_id="feature-unknown-bucket",
            description=(
                "patient_features.age_bucket should rarely be 'unknown'; a high rate "
                "means invalid ages reached the model."
            ),
            table="patient_features",
            kind="null_rate",
            column="age_bucket",
            threshold=0.01,
            comparison="lte",
            where="age_bucket = 'unknown'",
        ),
    ],
    control_urns=[URN_RAW_BILLING, URN_BILLING_SUMMARY],
    expected_severity=Severity.CRITICAL,
)


WARNING = Scenario(
    scenario_id="warning-staging-staleness",
    title="Staging refresh lag on the patient branch",
    description=(
        "The staging_patients watermark has drifted past its refresh SLA. Values "
        "are structurally valid, so the model still scores, but features are "
        "computed from data that is no longer current."
    ),
    anchor_urn=URN_STAGING_PATIENTS,
    checks=[
        CheckSpec(
            check_id="staging-freshness-sla",
            description="staging_patients must refresh at least every 48 hours.",
            table="staging_patients",
            kind="freshness",
            column="last_refreshed_at",
            threshold=48.0,
            comparison="lte",
        ),
        CheckSpec(
            check_id="staging-row-floor",
            description="staging_patients must retain at least 400 rows after cleaning.",
            table="staging_patients",
            kind="row_count",
            threshold=400,
            comparison="gte",
        ),
        CheckSpec(
            check_id="feature-freshness",
            description="patient_features must be recomputed at least every 48 hours.",
            table="patient_features",
            kind="freshness",
            column="computed_at",
            threshold=48.0,
            comparison="lte",
        ),
    ],
    control_urns=[URN_RAW_BILLING, URN_BILLING_SUMMARY],
    expected_severity=Severity.WARNING,
)


HEALTHY = Scenario(
    scenario_id="healthy-billing-branch",
    title="Billing branch control check",
    description=(
        "The billing branch shares a warehouse with the patient flow but has "
        "independent ownership and no dependency on patient attributes. This "
        "control run establishes that LineageMedic reports healthy when the data "
        "is in fact healthy."
    ),
    anchor_urn=URN_RAW_BILLING,
    checks=[
        CheckSpec(
            check_id="billing-amount-range",
            description="raw_billing.amount_cents must be non-negative and below $500k.",
            table="raw_billing",
            kind="range",
            column="amount_cents",
            min_value=0,
            max_value=50_000_000,
            threshold=0.01,
            comparison="lte",
        ),
        CheckSpec(
            check_id="billing-payer-null-rate",
            description="raw_billing.payer is required for claim routing.",
            table="raw_billing",
            kind="null_rate",
            column="payer",
            threshold=0.01,
            comparison="lte",
        ),
        CheckSpec(
            check_id="billing-freshness",
            description="billing_summary must refresh at least every 24 hours.",
            table="billing_summary",
            kind="freshness",
            column="last_refreshed_at",
            threshold=24.0,
            comparison="lte",
        ),
    ],
    control_urns=[URN_RAW_PATIENTS, URN_PATIENT_FEATURES],
    expected_severity=Severity.HEALTHY,
)


ALL_SCENARIOS: dict[str, Scenario] = {
    s.scenario_id: s for s in (CRITICAL, WARNING, HEALTHY)
}


def get_scenario(scenario_id: str) -> Scenario:
    """Look up a scenario by id, raising ``KeyError`` with the valid options."""
    try:
        return ALL_SCENARIOS[scenario_id]
    except KeyError:
        raise KeyError(
            f"unknown scenario {scenario_id!r}; valid ids: {sorted(ALL_SCENARIOS)}"
        ) from None
