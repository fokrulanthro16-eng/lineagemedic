"""Regenerate examples/ from real workflow runs.

The committed example payloads are captured from actual diagnoses rather than
written by hand, so they cannot describe behaviour the code does not have. If
the workflow changes, rerun this script and the diff shows exactly what changed
in the contract.

Volatile fields (incident id, timestamps, durations) are normalised so reruns
produce a stable diff instead of noise on every execution.

    py -3.11 scripts/export_examples.py
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from lineagemedic.adapters.fixture import FixtureMetadataAdapter, FixtureWritebackAdapter
from lineagemedic.data.seed_healthcare import REFERENCE_NOW, build_database
from lineagemedic.scenarios import ALL_SCENARIOS
from lineagemedic.workflow import Workflow

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = REPO_ROOT / "examples"

# Values that legitimately differ between runs. Replacing them keeps the
# committed examples reviewable; everything else is the real output.
PLACEHOLDER_ID = "LM-EXAMPLE"
PLACEHOLDER_TIME = REFERENCE_NOW.isoformat()


def _normalise(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if key == "incident_id" and isinstance(item, str):
                result[key] = PLACEHOLDER_ID
            elif key.endswith(("_at", "_time")) and isinstance(item, str):
                result[key] = PLACEHOLDER_TIME
            elif key == "duration_ms":
                result[key] = 0
            else:
                result[key] = _normalise(item)
        return result
    if isinstance(value, list):
        return [_normalise(item) for item in value]
    return value


def main() -> int:
    # Build into a throwaway directory, never the configured ``db_path``. The
    # examples must be reproducible, so both the seed and the judge are pinned
    # to ``REFERENCE_NOW`` -- but the demo's database is deliberately seeded
    # from wall-clock so its HEALTHY control does not age into a warning.
    # Writing a frozen-clock database to the shared path would hand the running
    # demo data that is already hours stale and break that control.
    with tempfile.TemporaryDirectory() as tmp:
        db_path = build_database(Path(tmp) / "healthcare.db", reference_now=REFERENCE_NOW)
        workflow = Workflow(
            metadata=FixtureMetadataAdapter(),
            writeback=FixtureWritebackAdapter(),
            db_path=db_path,
            now=REFERENCE_NOW,
        )

        EXAMPLES.mkdir(parents=True, exist_ok=True)
        for scenario in ALL_SCENARIOS.values():
            diagnosis = workflow.diagnose(scenario)
            payload = _normalise(json.loads(diagnosis.model_dump_json()))
            # Named for the scenario, not for the derived severity: two
            # scenarios can legitimately derive the same severity, and keying
            # on the outcome made one silently overwrite the other's file.
            target = EXAMPLES / f"incident-{scenario.expected_severity.value}.json"
            target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            print(f"  wrote {target.relative_to(REPO_ROOT)} ({diagnosis.severity.value})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
