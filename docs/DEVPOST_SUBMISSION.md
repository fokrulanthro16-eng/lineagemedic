# Devpost submission

Prepared for **Build with DataHub: The Agent Hackathon**, challenge
**Production ML Agents**.

This file is the source text for the submission form. Everything in it is
verifiable from the repository — no claim here goes beyond what the code does.

---

## Project name

**LineageMedic**

## Tagline

Diagnose, contain, and heal silent data failures before they break production ML.

## The problem

Loud data failures page someone. A pipeline crashes, an alert fires, a human
looks at it.

Silent ones do not. A column quietly starts producing bad values upstream — ages
that are negative, admission dates in the future — and every downstream job keeps
succeeding. The dashboards stay green. The feature table refreshes on schedule.
And a production readmission-risk model keeps serving predictions built on
corrupted inputs, for as long as it takes someone to notice by hand.

In healthcare that gap is not an inconvenience. A readmission-risk score derived
from a corrupted age column is a clinical decision made on bad evidence.

## What it does

LineageMedic is a seven-agent diagnostic workflow that runs against a real
DataHub catalog. Given an incident, it:

1. **Scans** the warehouse for data-quality defects.
2. **Retrieves** schema, ownership, and lineage from DataHub.
3. **Walks the lineage graph** to compute a blast radius — which assets are
   actually downstream of the defect, and which are not.
4. **Attributes a root cause** back to the originating table and columns, rather
   than the table where the symptom first appeared.
5. **Proposes remediation**, each action labelled safe/unsafe and reversible with
   an explicit rollback.
6. **Applies a safety policy** and holds everything at a human approval gate.
7. **Writes the incident back to DataHub** — tags and an incident note on every
   affected asset — and verifies each write by reading the metadata back.

## What makes it different from a rules engine with a dashboard

**Severity is derived, never declared.** No branch in the codebase assigns
`critical`. It falls out of two measured facts: how many quality checks failed,
and whether the blast radius reaches an ML model or a production endpoint. The
scenarios carry an `expected_severity`, but that value is only ever *compared
against* the derived result — `scripts/demo.ps1` prints `MISMATCH` if they ever
disagree. It is a test oracle, not an input.

**The blast radius is selective.** The billing branch in the demo shares an
upstream ancestor with the failing patient branch. A naive
everything-downstream traversal would quarantine it. LineageMedic examines it
and clears it, and the dashboard shows it cleared. That distinction is the whole
point: an agent that quarantines everything is not useful.

**Provenance is a type, not a comment.** Every response carries a `DataSource`
of `LIVE_DATAHUB` or `FIXTURE`. Fixture mode says so in the UI and *refuses* the
writeback rather than reporting a fake success. There is no code path that
reports live provenance for fixture data.

**Every write is verified by reading it back.** The writeback agent does not
trust its own HTTP 200. It re-fetches the aspect from DataHub and confirms the
content before reporting `applied`.

## How DataHub is used

Not as a passive sink. DataHub is the source of truth the agents reason over:

- `searchAcrossEntities` and `searchAcrossLineage` to resolve and traverse.
- `entity.lineage` relationship traversal for the ML bridge, because
  `searchAcrossLineage` follows only `DownstreamOf` and not
  `TrainedBy`/`UsedBy`/`Consumes`.
- Entity **subtypes** to classify blast-radius severity. Classifying on entity
  type alone downgrades a critical incident to a warning, because a serving
  endpoint and an ordinary dataset are both datasets to the type system.
- `editableDatasetProperties` and `globalTags` for the incident writeback.

## What DataHub v1.6.0 forced

Stated plainly rather than papered over. One requested lineage hop —
`readmission_risk_model → model_predictions` — is **not expressible** in this
version. Both directions return HTTP 422: `upstreamLineage` is not a valid
aspect for `mlModel`, and a dataset cannot name an `mlModel` as an upstream
because `upstreams[].dataset` is dataset-typed.

Six of the seven hops are live. The model is connected through
`mlModelProperties.trainingJobs`/`downstreamJobs`, which produces
`TrainedBy`/`UsedBy` edges that the DataHub UI does traverse. This was
established by probing a running instance, and the evidence is in
`docs/ARCHITECTURE.md`.

## Built with

Python 3.11, FastAPI, Pydantic v2, React, TypeScript, Vite, SQLite, DataHub OSS
v1.6.0, Docker, pytest, vitest, ruff, mypy, Playwright, PowerShell.

## Try it

Five minutes, no Docker required — fixture mode is the default:

```powershell
.\scripts\setup.ps1
.\scripts\start.ps1
```

The full live-DataHub path, and a section on deliberately trying to catch the
system lying, are in [`docs/JUDGE_TEST_GUIDE.md`](JUDGE_TEST_GUIDE.md).

## Verification status

| Gate | Result |
|------|--------|
| `pytest` | 96 passed |
| Live DataHub integration tests | 15 passed against a running instance |
| `vitest` | 28 passed across 4 files |
| `ruff check .` | clean |
| `mypy` | clean, 31 source files |
| API contract | frontend types in sync with the OpenAPI schema |
| Committed examples | regenerate byte-for-byte from real workflow runs |

All seven screenshots in `docs/screenshots/` were captured from the running
application and a real local DataHub instance. None is mocked or edited.

## Demo video

Not yet recorded. A shot list is in
[`docs/VIDEO_SHOT_LIST.md`](VIDEO_SHOT_LIST.md). No video URL is claimed
anywhere in this repository until a real public one exists.

## Licence

Apache-2.0.
