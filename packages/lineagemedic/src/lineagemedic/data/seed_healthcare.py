"""Build the local healthcare SQLite database used by the Quality Agent.

The database is generated, not committed, so the repository ships no binary
blobs and every row is auditable in source. Generation is deterministic: a
fixed PRNG seed means the Quality Agent measures identical numbers on every
machine, which is what lets the tests assert on exact counts.

Three tables model the raw -> staging -> features flow, plus a billing table
that deliberately shares an upstream with the patient flow but carries none of
the planted defects. The billing branch is the control: it proves the Impact
Agent contains the blast radius instead of quarantining the whole warehouse.

Planted defects (patient flow only), all confined to the CRITICAL scenario:

* ``raw_patients.age`` - out-of-range values (negative and >130) that survive
  into staging because the staging transform only filters NULLs.
* ``raw_patients.admission_date`` - NULLs in a column the feature pipeline
  treats as mandatory.
* ``staging_patients.last_refreshed_at`` - staleness, set well behind the
  billing branch's refresh so freshness checks separate the two.

The WARNING scenario reads the same tables through a narrower row window where
only the mild nullability defect appears. The HEALTHY scenario reads the
billing branch, which has no planted defects at all.
"""

from __future__ import annotations

import random
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Fixed seed: identical data on every machine, so test assertions can be exact.
SEED = 20260728
TOTAL_PATIENTS = 500
BILLING_ROWS = 300

# Reference "now" for the generated data. Fixed rather than wall-clock so
# freshness deltas stay stable across runs and CI.
REFERENCE_NOW = datetime(2026, 7, 28, 12, 0, 0, tzinfo=UTC)

# Planted-defect volumes, asserted directly by the Quality Agent tests.
INVALID_AGE_ROWS = 37
NULL_ADMISSION_ROWS = 22
STAGING_STALENESS_HOURS = 52

SCHEMA = """
DROP TABLE IF EXISTS raw_patients;
DROP TABLE IF EXISTS staging_patients;
DROP TABLE IF EXISTS patient_features;
DROP TABLE IF EXISTS raw_billing;
DROP TABLE IF EXISTS billing_summary;

CREATE TABLE raw_patients (
    patient_id      TEXT PRIMARY KEY,
    age             INTEGER,
    sex             TEXT,
    admission_date  TEXT,
    discharge_date  TEXT,
    primary_dx      TEXT,
    ingested_at     TEXT NOT NULL
);

CREATE TABLE staging_patients (
    patient_id        TEXT PRIMARY KEY,
    age               INTEGER,
    sex               TEXT,
    admission_date    TEXT,
    length_of_stay    INTEGER,
    primary_dx        TEXT,
    last_refreshed_at TEXT NOT NULL
);

CREATE TABLE patient_features (
    patient_id           TEXT PRIMARY KEY,
    age_bucket           TEXT,
    prior_admissions     INTEGER,
    length_of_stay       INTEGER,
    chronic_flag         INTEGER,
    computed_at          TEXT NOT NULL
);

CREATE TABLE raw_billing (
    claim_id     TEXT PRIMARY KEY,
    patient_id   TEXT,
    amount_cents INTEGER NOT NULL,
    payer        TEXT NOT NULL,
    claim_date   TEXT NOT NULL,
    ingested_at  TEXT NOT NULL
);

CREATE TABLE billing_summary (
    payer             TEXT PRIMARY KEY,
    claim_count       INTEGER NOT NULL,
    total_cents       INTEGER NOT NULL,
    last_refreshed_at TEXT NOT NULL
);
"""

_DIAGNOSES = [
    "heart_failure",
    "copd",
    "pneumonia",
    "diabetes_t2",
    "acute_mi",
    "sepsis",
    "renal_failure",
]
_PAYERS = ["medicare", "medicaid", "commercial_a", "commercial_b", "self_pay"]


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


def build_database(db_path: Path) -> Path:
    """Create (or recreate) the healthcare database at ``db_path``.

    Returns the path written. Safe to call repeatedly: the schema drops and
    recreates every table, so this doubles as the demo-reset primitive.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    rng = random.Random(SEED)

    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA)
        _seed_patient_branch(conn, rng)
        _seed_billing_branch(conn, rng)
        conn.commit()
    finally:
        conn.close()
    return db_path


def _seed_patient_branch(conn: sqlite3.Connection, rng: random.Random) -> None:
    """Populate raw -> staging -> features, planting the defects on the way."""
    ingested = _iso(REFERENCE_NOW - timedelta(hours=6))

    # Choose which rows carry planted defects. Disjoint sets so each check
    # measures one defect cleanly and the counts stay independently assertable.
    ids = [f"PT{i:05d}" for i in range(TOTAL_PATIENTS)]
    shuffled = ids[:]
    rng.shuffle(shuffled)
    invalid_age_ids = set(shuffled[:INVALID_AGE_ROWS])
    null_admission_ids = set(shuffled[INVALID_AGE_ROWS : INVALID_AGE_ROWS + NULL_ADMISSION_ROWS])

    # (patient_id, age, sex, admission_iso, discharge_iso, diagnosis, ingested_iso).
    # Dates are stored as ISO strings, matching the TEXT columns in the schema.
    raw_rows: list[tuple[str, int, str, str | None, str | None, str, str]] = []
    for pid in ids:
        if pid in invalid_age_ids:  # noqa: SIM108 - the comment below belongs to this branch
            # Two flavours of out-of-range so the sample values are informative:
            # a negative sentinel and an implausible centenarian overflow.
            age = rng.choice([-1, -7, 148, 151, 999])
        else:
            age = rng.randint(18, 96)

        admission = None if pid in null_admission_ids else REFERENCE_NOW - timedelta(
            days=rng.randint(3, 240)
        )
        los = rng.randint(1, 21)
        discharge = admission + timedelta(days=los) if admission else None

        raw_rows.append(
            (
                pid,
                age,
                rng.choice(["M", "F"]),
                _iso(admission) if admission else None,
                _iso(discharge) if discharge else None,
                rng.choice(_DIAGNOSES),
                ingested,
            )
        )
    conn.executemany(
        "INSERT INTO raw_patients VALUES (?, ?, ?, ?, ?, ?, ?)",
        raw_rows,
    )

    # Staging transform: the realistic bug. It drops rows with a NULL admission
    # date but performs no range validation on age, so every invalid age flows
    # straight through to the feature pipeline. This is the defect the Root
    # Cause Agent is expected to localise to raw_patients.age.
    staged = _iso(REFERENCE_NOW - timedelta(hours=STAGING_STALENESS_HOURS))
    staging_rows = []
    for pid, row_age, row_sex, adm_iso, dis_iso, row_dx, _ in raw_rows:
        if adm_iso is None or dis_iso is None:
            continue
        los = (datetime.fromisoformat(dis_iso) - datetime.fromisoformat(adm_iso)).days
        staging_rows.append((pid, row_age, row_sex, adm_iso, los, row_dx, staged))
    conn.executemany(
        "INSERT INTO staging_patients VALUES (?, ?, ?, ?, ?, ?, ?)",
        staging_rows,
    )

    # Feature pipeline: buckets age without validating it, so invalid ages
    # become an "unknown" bucket that the model silently consumes.
    computed = _iso(REFERENCE_NOW - timedelta(hours=STAGING_STALENESS_HOURS - 2))
    feature_rows = []
    for pid, age, _sex, _adm, los, dx, _ts in staging_rows:
        feature_rows.append(
            (
                pid,
                _age_bucket(age),
                rng.randint(0, 5),
                los,
                1 if dx in {"heart_failure", "copd", "diabetes_t2"} else 0,
                computed,
            )
        )
    conn.executemany(
        "INSERT INTO patient_features VALUES (?, ?, ?, ?, ?, ?)",
        feature_rows,
    )


def _age_bucket(age: int | None) -> str:
    """Bucket an age, mirroring the (unvalidated) production feature logic."""
    if age is None or age < 0 or age > 130:
        return "unknown"
    if age < 35:
        return "18-34"
    if age < 55:
        return "35-54"
    if age < 75:
        return "55-74"
    return "75+"


def _seed_billing_branch(conn: sqlite3.Connection, rng: random.Random) -> None:
    """Populate the billing control branch. No planted defects live here."""
    ingested = _iso(REFERENCE_NOW - timedelta(hours=2))
    refreshed = _iso(REFERENCE_NOW - timedelta(hours=1))

    claims = []
    for i in range(BILLING_ROWS):
        claims.append(
            (
                f"CLM{i:05d}",
                f"PT{rng.randint(0, TOTAL_PATIENTS - 1):05d}",
                rng.randint(5_00, 48_000_00),
                rng.choice(_PAYERS),
                _iso(REFERENCE_NOW - timedelta(days=rng.randint(1, 90))),
                ingested,
            )
        )
    conn.executemany("INSERT INTO raw_billing VALUES (?, ?, ?, ?, ?, ?)", claims)

    totals: dict[str, list[int]] = {}
    for _cid, _pid, amount, payer, _cd, _ing in claims:
        bucket = totals.setdefault(payer, [0, 0])
        bucket[0] += 1
        bucket[1] += amount
    conn.executemany(
        "INSERT INTO billing_summary VALUES (?, ?, ?, ?)",
        [(p, c, t, refreshed) for p, (c, t) in sorted(totals.items())],
    )


if __name__ == "__main__":  # pragma: no cover - manual invocation helper
    target = Path(__file__).resolve().parents[4] / "data" / "healthcare.db"
    print(f"building {target}")
    build_database(target)
    print("done")
