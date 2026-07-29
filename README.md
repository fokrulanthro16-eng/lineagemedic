# LineageMedic

**Diagnose, contain, and heal silent data failures before they break production ML.**

A silent data failure is the kind that does not raise an alarm. No pipeline
crashes, no job goes red, no page fires. A column starts carrying impossible
values, a refresh quietly falls behind, and the model keeps serving predictions
built on top of it. By the time anyone notices, the question is no longer "what
broke" but "what has this been contaminating, and for how long".

LineageMedic answers that question with evidence. It runs quality checks against
a real warehouse, walks the lineage graph to find where the defect entered and
what it reaches, proposes a remediation plan, and — only after a human approves
— writes its findings back to the catalog.

> ### Two modes, and every response says which one produced it.
>
> **Live mode** (`LINEAGEMEDIC_MODE=live`) talks to a real DataHub OSS instance:
> it reads lineage and metadata over DataHub's GraphQL API and, once a human
> approves, writes tags and descriptions back and verifies them by reading them
> out again. Responses carry `context_source: "live_datahub"`.
>
> **Fixture mode** is the default fallback and runs against committed fixtures
> and a local SQLite warehouse, with no DataHub required. Every response is
> labelled: `context_source: "fixture"`, a persistent banner in the dashboard,
> and an approved writeback returns `skipped_fixture_mode` rather than reporting
> a success that never happened.
>
> Live mode never silently degrades — if DataHub is unreachable it reports the
> failure instead of quietly serving fixtures. See
> [Honesty guarantees](#honesty-guarantees) and [Running against a real DataHub](#running-against-a-real-datahub).

---

## Why this is not just a rules engine with a dashboard

Three design decisions carry the project.

**Severity is derived, never declared.** A scenario describes a situation; it
does not get to announce how bad it is. Severity falls out of the measurements
— how many checks failed, whether the blast radius reaches a production
endpoint. The scenarios record an `expected_severity`, and `scripts/demo.ps1`
compares it against what the workflow actually derived, printing `MISMATCH` if
they diverge. The demo is a live assertion, not a slideshow.

**Provenance is a type, not a convention.** Every object derived from metadata
carries a `DataSource` enum (`LIVE_DATAHUB` or `FIXTURE`). It is a required
field, so there is no code path that produces a lineage result without
recording where it came from. That is what makes the fixture-mode labelling
structural rather than a string someone remembered to add.

**The approval gate is enforced three times.** The Safety agent classifies the
action, the Writeback agent refuses to act on an unapproved plan, and the HTTP
endpoint returns 403. Each layer is independently tested, because a gate that
exists in one layer is a gate that a refactor can remove.

## Architecture

```
                    ┌──────────────────────────────────┐
                    │   React + TypeScript dashboard   │
                    │   types generated from OpenAPI   │
                    └────────────────┬─────────────────┘
                                     │ HTTP
                    ┌────────────────▼─────────────────┐
                    │        FastAPI  (apps/api)       │
                    │  approval gate · audit · status  │
                    └────────────────┬─────────────────┘
                                     │
                    ┌────────────────▼─────────────────┐
                    │   Workflow  (7 agents, ordered)  │
                    │                                  │
                    │  Quality → Context → Impact →    │
                    │  Root Cause → Remediation →      │
                    │  Safety → Writeback              │
                    └───────┬──────────────────┬───────┘
                            │                  │
                  MetadataPort (read)   WritebackPort (write)
                            │                  │
            ┌───────────────▼──────┐   ┌───────▼───────────────┐
            │  Fixture adapter     │   │  Fixture adapter      │
            │  (default)           │   │  → skipped_fixture    │
            ├──────────────────────┤   ├───────────────────────┤
            │  DataHub MCP adapter │   │  DataHub SDK adapter  │
            │  (live mode)         │   │  (live mode)          │
            └──────────────────────┘   └───────────────────────┘
```

The two ports are `typing.Protocol` definitions. The workflow depends on those
interfaces and never on a concrete adapter, which is what made the DataHub phase
an addition rather than a rewrite: the live adapters implement the same
protocols and get injected at the composition root in `apps/api/lineagemedic_api/main.py`.

### The seven agents

Each agent consumes the previous agents' evidence and contributes typed output.
None of them calls an LLM to reach a conclusion.

| # | Agent | Question it answers |
|---|-------|---------------------|
| 1 | Quality | Which checks fail, on which columns, by how much? |
| 2 | Context | What does the catalog know about these assets — schema, owners, lineage? |
| 3 | Impact | What is downstream, and does it reach a model or a production endpoint? |
| 4 | Root Cause | Where did the defect enter, and what is the competing hypothesis? |
| 5 | Remediation | What should be done, in what order, and what is the rollback? |
| 6 | Safety | Which of those steps mutate shared state and therefore need approval? |
| 7 | Writeback | Record findings to the catalog — refusing unless approval was granted. |

An optional Ollama narrator can rewrite the summary in plainer language. It is
strictly presentational: if it is absent, slow, or wrong, the diagnosis, the
severity, and the evidence are unchanged. **The demo does not depend on any
LLM being available.**

## Scenarios

| Scenario ID | Derived severity | What it demonstrates |
|-------------|------------------|----------------------|
| `critical-age-corruption` | critical | Impossible ages in `raw_patients` propagate through staging and features into the deployed readmission model. |
| `warning-staging-staleness` | warning | A refresh lag that has not yet corrupted anything, but is trending toward it. |
| `healthy-billing-branch` | healthy | The billing branch is examined and cleared, proving containment is selective rather than a blanket alarm. |

The healthy scenario is the one that matters most. A tool that flags everything
during an incident is not diagnosing; the billing branch shares an upstream
ancestor with the failing assets and is still correctly left in service.

## Quickstart (Windows / PowerShell)

Requires Python 3.11 (`py -3.11`) and Node 20+. No Docker, no cloud account, no
API key.

```powershell
.\scripts\setup.ps1     # venv, dependencies, seeded warehouse, generated types
.\scripts\start.ps1     # backend on :8000, dashboard on :5173
.\scripts\demo.ps1      # drive all three scenarios through the API
.\scripts\test.ps1      # ruff, mypy, pytest, tsc, vitest
.\scripts\stop.ps1      # stop both services
```

`start.ps1` records each process ID with its start time, and `stop.ps1`
re-validates that start time before killing anything — Windows recycles PIDs,
and a stale PID file must never be able to terminate an unrelated process.

## Honesty guarantees

The project is built so that a fabricated success is difficult to produce by
accident and impossible to produce quietly.

- **No fake writebacks.** In fixture mode an approved writeback returns
  `status: "skipped_fixture_mode"` with an explanatory note. The dashboard
  renders that as *"No writeback performed - fixture mode"*.
- **No inferred connections.** The dashboard reports DataHub connectivity from
  the backend's `datahub_connected` field. It never concludes a connection
  exists because data happens to be present.
- **No silent degradation.** In live mode an unreachable DataHub is reported as
  a failure. The API never falls back to fixtures while claiming to be live.
- **No invented assets.** The live adapter checks DataHub's `exists` flag and
  raises for an unknown URN, so a caller can tell "the catalog has no such
  asset" from "the catalog was unreachable". It never fills a gap with fixture
  data.
- **No destroyed metadata.** A writeback merges into the tags already on an
  entity. If the current tags cannot be read, the adapter raises rather than
  writing, because DataHub's `globalTags` is a whole-aspect replace and a failed
  read would otherwise silently delete every existing tag.
- **No hand-written examples.** `examples/*.json` are captured from real
  workflow runs by `scripts/export_examples.py`, and CI regenerates and diffs
  them, so they cannot describe behaviour the code lacks.
- **No drifting types.** The frontend's types are generated from the OpenAPI
  schema, which is generated from the Pydantic models. CI fails if the
  committed artifacts are stale.
- **No secrets.** Tokens are read from the environment, never logged, and the
  status endpoint reports only whether a token is configured — never its value.

## Repository layout

```
packages/lineagemedic/     Core library: models, agents, workflow, adapters
apps/api/                  FastAPI application
apps/web/                  React + TypeScript + Vite dashboard
scripts/                   PowerShell lifecycle scripts and Python generators
examples/                  Real captured diagnoses (critical, warning, healthy)
skills/                    Reusable incident-response skill
docs/                      Architecture, demo script, environment audit
tests/                     Backend test suite
```

## Development

```powershell
.\scripts\test.ps1                  # every gate
.\scripts\test.ps1 -BackendOnly     # ruff, mypy, pytest
.\scripts\test.ps1 -Coverage        # with coverage reports
.\scripts\check_api_types.ps1       # fail if the API contract is stale
```

After changing any API response model, run `check_api_types.ps1` and commit the
regenerated `scripts/openapi.json` and `apps/web/src/api/schema.ts`.

## Running against a real DataHub

Live mode has been exercised end to end against DataHub OSS v1.6.0.

```powershell
# 1. Start DataHub OSS. The override supplies the token-service signing key
#    that `datahub docker quickstart` would normally inject; see the file's
#    header for why it is needed and why the key is safe to commit.
docker compose -p datahub `
  -f $HOME\.datahub\quickstart\docker-compose.yml `
  -f docker\datahub-quickstart.override.yml up -d

# 2. Ingest the lineage. Needs the DataHub SDK, which pins a dependency tree
#    of its own and so lives in a separate Python 3.11 environment.
.venv-datahub\Scripts\python.exe scripts\ingest_lineage.py

# 3. Run the API against it.
$env:LINEAGEMEDIC_MODE = "live"
$env:DATAHUB_GMS_URL   = "http://localhost:8080"
.\scripts\start.ps1
```

The ingested graph is two deliberately disconnected branches — a patient chain
from `raw_patients` through to the served predictions, and an independent
billing branch. Containment is a real property of the graph, not an assertion in
the UI: a patient incident must leave billing untouched, and the integration
suite fails if the two branches ever become reachable from each other.

The integration tests skip themselves when no DataHub is reachable, so the
default suite stays runnable with nothing installed:

```powershell
$env:LINEAGEMEDIC_MODE = "live"; $env:DATAHUB_GMS_URL = "http://localhost:8080"
.venv\Scripts\python.exe -m pytest tests\test_datahub_integration.py
```

They cover the parts that only a live instance can prove: that lineage traverses
the full chain, that the blast radius reaches an ML model and a production
endpoint by kind (severity is derived from those counts), that an unapproved
writeback mutates nothing, and that an approved one is verified by reading the
metadata back out of DataHub.

DataHub v1.6.0 constraints that shaped the ingestion — chiefly that `mlModel`
and `mlModelDeployment` cannot take part in a lineage traversal at all — are
documented with their evidence in `_ml_bridge_mcps` in
[scripts/ingest_lineage.py](scripts/ingest_lineage.py).

## Status

The application is complete and runs in both modes. Live DataHub reads,
lineage traversal, and approval-gated writeback are implemented and verified
against a running instance; fixture mode remains the labelled default so the
project is runnable with no infrastructure.

## License

Apache-2.0. See [LICENSE](LICENSE).
