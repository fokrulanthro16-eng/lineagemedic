# Repository polish audit

Audit performed 2026-07-29 against the working tree at commit `96978e5`, with
the full stack running: DataHub OSS v1.6.0rc1 (8 containers), the LineageMedic
API on `:8000` in live mode, and the dashboard on `:5173`.

Every number below was measured by running the command named beside it. Nothing
here is estimated.

## What already existed and is worth keeping

| Area | State | Evidence |
|------|-------|----------|
| Backend | FastAPI + Pydantic v2, 7205 lines of Python | `git ls-files '*.py' \| xargs wc -l` |
| Frontend | React + TypeScript + Vite, 3168 lines | `git ls-files '*.ts' '*.tsx' \| xargs wc -l` |
| Python tests | 78 passed | `pytest` |
| Live DataHub integration tests | 14 passed against a real instance | `pytest tests/test_datahub_integration.py` |
| Frontend tests | 28 passed across 4 files | `npm test -- --run` |
| Lint | clean | `ruff check .` |
| Types (Python) | clean, 30 source files | `mypy packages/lineagemedic/src apps/api scripts` |
| API contract | frontend types in sync with the OpenAPI schema | `scripts/check_api_types.ps1` |
| CI | present | `.github/workflows/ci.yml` |
| Licence | Apache-2.0, 169 lines, unmodified | `LICENSE` |
| Governance docs | `CONTRIBUTING.md`, `SECURITY.md` | repository root |
| Worked examples | 3 committed JSON diagnoses | `examples/` |

The architecture is genuinely sound and was left alone. Ports and adapters
isolate DataHub behind two `typing.Protocol` definitions, so the live
integration changed exactly one composition-root function and nothing else.

## Gaps found

### 1. Documentation set was thin

Only three documents existed: `ARCHITECTURE.md`, `DEMO_SCRIPT.md`, and
`ENVIRONMENT_AUDIT.md`. For a hackathon submission a judge also needs a
reproducible test path, a submission summary, and a way to see the product
without installing it.

### 2. No screenshots at all

`docs/screenshots/` did not exist. The README described behaviour that a reader
had to take on trust. Seven screenshots were captured for this polish, all from
the running application and the real local DataHub — see below.

### 3. README structure did not lead with the result

The previous README opened with prose and reached the quickstart at line 126.
A judge skimming for thirty seconds saw no evidence of a working product.

### 4. `gh` CLI not installed

Phase 5 (repository description and topics) cannot be automated from this
environment: `gh` is not on `PATH`. The required values are recorded in
`docs/SUBMISSION_CHECKLIST.md` for the repository owner to apply through the
GitHub web UI. This is the only part of the polish that needs a human.

## Secret and privacy scan

Scanned with `git grep` for credentials, tokens, personal filesystem paths, and
email addresses.

- **No personal paths.** No `C:\Users\...` or `/Users/...` anywhere tracked.
- **No personal email addresses.** Every address in `examples/` uses the
  reserved `.example` TLD (`data-platform@lineagemedic.example`).
- **No hardcoded credentials.** Every `token` / `password` match is a parameter
  name, a dataclass field, or an `os.environ.get` read with an empty default.
- **One committed literal, deliberate and documented.**
  `docker/datahub-quickstart.override.yml` sets `DATAHUB_SECRET` to a 384-bit
  random value. This is not a leaked credential: DataHub's published compose
  file ships a 112-bit default that is too short for Play's HS256 session
  signing, so the frontend container exits 255 and the UI never binds. The file
  documents the failure mode, states that it is a localhost-only development
  default, and instructs any real deployment to supply its own secret and
  delete the file. It is kept so the quickstart works on a fresh clone.

## Screenshots captured

All seven were captured with Playwright driving a real browser against the
running services, and each was visually inspected before being committed. None
is mocked, staged, or edited.

| File | Source | What it proves |
|------|--------|----------------|
| `01-lineagemedic-critical-dashboard.png` | `localhost:5173` | Critical verdict, incident ID, 80% confidence, live DataHub/MCP/LLM indicators |
| `02-selective-blast-radius.png` | `localhost:5173` | The billing branch is cleared while the patient branch is quarantined |
| `03-datahub-downstream-lineage.png` | `localhost:9002` | `train_readmission_model` with three real downstream results in DataHub's own graph |
| `04-datahub-upstream-lineage.png` | `localhost:9002` | `raw_patients → staging_patients → patient_features` in DataHub |
| `05-datahub-writeback.png` | `localhost:9002` | The incident note and both tags, read back from DataHub after a writeback |
| `06-approval-and-receipt.png` | `localhost:5173` | The approval gate at `approved`, and the applied writeback receipt naming its aspects |
| `07-healthy-control.png` | `localhost:5173` | The healthy scenario reports healthy and attributes no root cause |

## Bug found and fixed during the audit

The healthy control scenario silently degraded to `warning` roughly 23 hours
after the database was seeded.

`build_database` seeded every timestamp as an offset from a frozen
`REFERENCE_NOW`, but the Quality Agent compares freshness against the real
clock. The `billing-freshness` check therefore breached its 24-hour threshold
once a day had passed — `demo.ps1` printed `MISMATCH: expected healthy`, and
`billing_summary` was observed at 28.63h against a 24.0h threshold.

The test suite could never catch this, because `tests/conftest.py` injects a
fixed clock into both the seeder and the agent, so its assertions stayed exact
while the demo rotted. Fixed in `96978e5` by defaulting `build_database` to
wall-clock and having the tests pin the clock explicitly. Both properties now
hold at once: deterministic tests, and a demo that does not age.

This mattered for the submission. A judge running the demo more than a day
after seeding would have seen the control scenario contradict its own label.

## Known limitation, stated deliberately

The requested lineage hop `readmission_risk_model → model_predictions` is not
expressible in DataHub v1.6.0. Both directions are rejected with HTTP 422:
`upstreamLineage` is not a valid aspect for `mlModel`, and a dataset cannot name
an `mlModel` as its upstream because `upstreams[].dataset` is dataset-typed.
Six of the seven requested hops are live; the model is connected through
`trainingJobs`/`downstreamJobs`, which the DataHub UI traverses. This is
documented in `ARCHITECTURE.md` with the evidence, rather than papered over.
