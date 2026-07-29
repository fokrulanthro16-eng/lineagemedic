# Judge test guide

Every claim in the README can be reproduced from a clean clone. This guide is
ordered so that the fastest path to a working demo comes first, and the DataHub
integration — which needs Docker — comes after.

Commands are PowerShell, run from the repository root. Each one below was
executed on 2026-07-29 and produced the output shown.

## Requirements

| Tool | Version | Needed for |
|------|---------|------------|
| Python | 3.11 (`py -3.11`) | Everything |
| Node | 20+ | The dashboard |
| Docker Desktop | any recent | Only the live DataHub section |

## Part 1 — the five-minute path (no Docker)

Fixture mode is the default and needs no infrastructure at all.

```powershell
.\scripts\setup.ps1
.\scripts\start.ps1
```

`setup.ps1` creates the virtual environment, installs dependencies, seeds the
SQLite warehouse, and generates the frontend's API types. `start.ps1` launches
the backend on `:8000` and the dashboard on `:5173`, waits for both to answer,
and prints their URLs.

Open <http://localhost:5173> and click **Invalid patient ages reaching the
production readmission model**.

**What to look for.** The verdict is `CRITICAL` and was *derived*, not declared:
5 quality checks failed and the blast radius reaches a deployed model and a
serving endpoint. The Impact panel lists 8 assets in the blast radius and 2
examined and cleared. Because the backend is in fixture mode, a banner says so
and the writeback will refuse.

Then click **Billing branch control check**. The verdict is `HEALTHY`, all
assets are marked *Cleared*, and the root-cause panel reads *"No root cause to
attribute - no checks failed."* This is the control: the billing branch shares
an upstream ancestor with the failing patient branch and is still correctly left
in service.

### Verify severity is derived, not hardcoded

```powershell
.\scripts\demo.ps1
```

This drives all three scenarios through the HTTP API and compares each derived
severity against the scenario's declared `expected_severity`, printing
`MISMATCH` on any disagreement. A clean run prints no `MISMATCH` lines.

To confirm the check has teeth, edit a scenario's `expected_severity` to a wrong
value and run it again — it will report the mismatch.

### Run the test suite

```powershell
.\scripts\test.ps1
```

Expected, and what these produced on 2026-07-29:

| Gate | Result |
|------|--------|
| `pytest` | 96 passed |
| `vitest` | 28 passed across 4 files |
| `ruff check .` | All checks passed |
| `mypy` | Success: no issues found in 31 source files |

The 96 includes 15 DataHub integration tests, which **skip themselves** when no
DataHub is reachable. With Docker down you will see `81 passed, 15 skipped`;
that is correct behaviour, not a failure.

### Verify the API contract cannot drift

```powershell
.\scripts\check_api_types.ps1
```

This regenerates `scripts/openapi.json` from the Pydantic models and
`apps/web/src/api/schema.ts` from that schema, then fails if either differs from
what is committed. Expected output ends with:

```
API schema and frontend types are in sync.
```

## Part 2 — the live DataHub path

This is the part that matters for the hackathon: the lineage, the traversal, and
the writeback are all real.

```powershell
# 1. Start DataHub OSS.
docker compose -p datahub `
  -f $HOME\.datahub\quickstart\docker-compose.yml `
  -f docker\datahub-quickstart.override.yml up -d

# 2. Ingest the healthcare warehouse and the ML lineage chain.
.venv-datahub\Scripts\python.exe scripts\ingest_lineage.py

# 3. Point the API at it.
$env:LINEAGEMEDIC_MODE = "live"
$env:DATAHUB_GMS_URL   = "http://localhost:8080"
.\scripts\start.ps1
```

The override file exists because DataHub's published compose file ships a
112-bit application secret, which is shorter than the 256 bits Play requires for
HS256 session signing — without the override the frontend container exits 255
and the UI never binds. The file explains this in its header.

Step 2 prints what it emitted:

```
EMITTED: 7 entities, 32 aspects, 3 lineage edges, 13 ml-bridge aspects
```

### Confirm the backend is genuinely live

```powershell
Invoke-RestMethod http://localhost:8000/status/integrations
```

`datahub_connected` must be `true`. The dashboard header will show **Live mode -
reading from a connected DataHub instance** with DataHub, MCP, and LLM
indicators. This value comes from the backend probing DataHub; it is never
inferred from the presence of data.

### Verify the lineage in DataHub's own UI

Open <http://localhost:9002> and sign in with the local development credential
(`datahub` / `datahub`).

Search for `train_readmission_model` and open its **Lineage** tab. You should
see `patient_features` upstream, and three downstream entities:
`serve_readmission_endpoint`, `model_predictions`, and
`readmission_risk_model`. This is the graph the blast-radius calculation walks.

Then open `staging_patients` → **Lineage** to see the upstream chain
`raw_patients → staging_patients → patient_features`.

### Verify the writeback is real

In the dashboard, run the critical scenario, scroll to **Remediation and
approval**, click **Approve plan**, then **Attempt DataHub writeback**.

The receipt panel turns green and reads *"Metadata written to DataHub /
APPLIED"*, naming the incident ID, the number of assets, and the aspects
written (`editableDatasetProperties`, `globalTags`).

Now confirm it independently, in DataHub rather than in our own UI. Open
`model_predictions` in DataHub and look at its **Documentation** tab and the
**Tags** section of the summary panel. You will see the incident note naming the
incident ID, severity, root cause columns, and blast-radius counts — plus the
tags `LineageMedic:incident` and `LineageMedic:severity:critical`.

You can also query GMS directly, bypassing both UIs:

```powershell
$body = '{"query":"{ dataset(urn:\"urn:li:dataset:(urn:li:dataPlatform:mlflow,lineagemedic.model_predictions,PROD)\") { editableProperties { description } tags { tags { tag { urn } } } } }"}'
Invoke-RestMethod -Method Post -Uri http://localhost:8080/api/graphql -ContentType 'application/json' -Body $body | ConvertTo-Json -Depth 8
```

### Verify the approval gate actually blocks

Ask for a writeback without approving first. Run a diagnosis, take the returned
`incident_id`, and post straight to the writeback endpoint:

```powershell
$id = (Invoke-RestMethod -Method Post -Uri http://localhost:8000/diagnose `
  -ContentType 'application/json' `
  -Body '{"scenario_id":"critical-age-corruption"}').incident_id

Invoke-RestMethod -Method Post -Uri "http://localhost:8000/incidents/$id/writeback" `
  -ContentType 'application/json' -Body '{}'
```

The API returns **403** and nothing is written:

```
Writeback requires approval. Incident LM-E91259E1 is currently pending.
POST to /incidents/LM-E91259E1/approve first.
```

This is enforced at three independent layers — the Safety agent, the Writeback
agent, and the HTTP endpoint — each separately tested.

### Run the live integration tests

```powershell
$env:LINEAGEMEDIC_MODE = "live"; $env:DATAHUB_GMS_URL = "http://localhost:8080"
.venv\Scripts\python.exe -m pytest tests\test_datahub_integration.py
```

15 tests, all passing against a running instance. They cover what only a live
catalog can prove: that lineage traverses the full chain, that the blast radius
reaches an ML model and a production endpoint *by subtype*, that an unapproved
writeback mutates nothing, that an approved one is verified by reading the
metadata back, and that re-running the ingestion does not erase the incident
tags a previous writeback attached.

## Part 3 — trying to catch it lying

The project is built so a fabricated success is hard to produce. Some things to
try:

**Kill DataHub mid-session.** Stop the GMS container and run a diagnosis in live
mode. The API reports the failure — it does not fall back to fixtures while
still claiming `context_source: "live_datahub"`.

**Check fixture mode admits it.** Run without `LINEAGEMEDIC_MODE=live`, approve
a plan, and attempt the writeback. It returns `skipped_fixture_mode` and the
dashboard says *"No writeback performed - fixture mode"* rather than showing a
success.

**Check the examples are not hand-written.**

```powershell
.venv\Scripts\python.exe scripts\export_examples.py
git diff --stat examples\
```

No diff: the committed examples are regenerated from real workflow runs. CI runs
this same check.

**Look for a hardcoded severity.** `grep` the agents for the literal
`"critical"`. It appears in the derivation logic in
`packages/lineagemedic/src/lineagemedic/agents/` and in scenario definitions as
`expected_severity` — which is only ever compared against the derived value,
never fed into it.

## Cleanup

```powershell
.\scripts\stop.ps1
docker compose -p datahub -f $HOME\.datahub\quickstart\docker-compose.yml down
```

`stop.ps1` re-validates each recorded process start time before terminating
anything, because Windows recycles PIDs and a stale PID file must never be able
to kill an unrelated process.
