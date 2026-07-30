# Demo runbook

Every command below was executed in order on 2026-07-30 against a live DataHub
v1.6.0 instance, and the output quoted is what it printed. This is the sequence
to follow before recording.

For the shot-by-shot recording plan, see [`VIDEO_SHOT_LIST.md`](VIDEO_SHOT_LIST.md).

## 0. Prerequisites

| Tool | Version | Checked with |
|------|---------|--------------|
| Python | 3.11 | `py -3.11 --version` |
| Node | 20+ (22 used here) | `node --version` |
| Docker Desktop | any recent | `docker ps` |

PowerShell may refuse to run the scripts under the default execution policy. If
so, run this once per terminal — it is process-scoped and does not change the
machine's policy:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
```

## 1. Start DataHub

```powershell
docker compose -p datahub `
  -f $HOME\.datahub\quickstart\docker-compose.yml `
  -f docker\datahub-quickstart.override.yml up -d
```

Takes 2-4 minutes from cold. Wait for GMS to report healthy:

```powershell
docker ps --format "table {{.Names}}`t{{.Status}}"
```

Eight running containers, with `datahub-datahub-gms-1` and
`datahub-datahub-frontend-react-1` both `(healthy)`. Two more —
`datahub-kafka-setup-1` and `datahub-datahub-upgrade-1` — are one-shot jobs that
finish and show `Exited (0)` under `docker ps -a`; that is success, not a
failure. The frontend binds ~20
seconds after GMS, so if <http://localhost:9002> refuses the connection, wait
and retry rather than restarting anything.

Confirm GMS answers:

```powershell
Invoke-RestMethod http://localhost:8080/config | Select-Object -ExpandProperty versions
```

## 2. Ingest the catalog

```powershell
.venv-datahub\Scripts\python.exe scripts\ingest_lineage.py
```

Prints exactly:

```
EMITTED: 7 entities, 32 aspects, 3 lineage edges, 13 ml-bridge aspects
```

Safe to re-run. Ingestion reads each entity's current `globalTags` and unions
rather than replacing, so a re-ingest does not erase incident tags from an
earlier writeback — verified live on 2026-07-30.

## 3. Start the backend and frontend

Both are started by one script. **Set the two environment variables first** —
without them the backend comes up in fixture mode and the DataHub shots will not
be real:

```powershell
$env:LINEAGEMEDIC_MODE = "live"
$env:DATAHUB_GMS_URL   = "http://localhost:8080"
.\scripts\start.ps1
```

```
=== Starting LineageMedic ===
Backend ready:  http://127.0.0.1:8000  (docs at /docs)
Dashboard ready: http://localhost:5173
```

To run them separately instead:

```powershell
# Backend only
$env:PYTHONPATH = "$PWD\packages\lineagemedic\src;$PWD\apps\api"
.venv\Scripts\python.exe -m uvicorn lineagemedic_api.main:app --host 127.0.0.1 --port 8000

# Frontend only, in a second terminal
cd apps\web
npm run dev
```

Stop everything with `.\scripts\stop.ps1`.

## 4. Confirm live mode before recording

```powershell
Invoke-RestMethod http://localhost:8000/status/integrations
```

`datahub_connected` and `mcp_connected` must both be **True**:

```
mode              : live
datahub_connected : True
mcp_connected     : True
llm_available     : True
```

This comes from the backend probing DataHub. It is never inferred from the
presence of data, so a `True` here means the connection is real.

## 5. Browser URLs

| URL | What it is |
|-----|------------|
| **<http://localhost:5173>** | **The dashboard — the URL to open for the demo** |
| <http://localhost:9002> | DataHub's own UI, for independent verification |
| <http://localhost:8000/docs> | FastAPI's generated API docs, if a judge asks |

Sign in to `:9002` (`datahub` / `datahub`) in a second tab **before** you start
recording, so no credential entry is filmed.

## 6. Optional pre-flight

```powershell
.\scripts\demo.ps1
```

Drives all three scenarios through the HTTP API and compares each derived
severity against the scenario's declared `expected_severity`. A clean run prints
no `MISMATCH` line and ends with the writeback receipt. This is the fastest
proof that the whole chain works before you commit to a take.

## Verified state, 2026-07-30

| Check | Result |
|-------|--------|
| DataHub containers | 8 up, GMS + frontend healthy, v1.6.0 |
| `ingest_lineage.py` | 7 entities, 32 aspects, 3 lineage edges, 13 ml-bridge aspects |
| Fixture mode, 3 scenarios | critical / warning / healthy, 0 MISMATCH |
| Live mode, 3 scenarios | critical / warning / healthy, 0 MISMATCH |
| Live mode `context_source` | `live_datahub` on all three |
| Blast radius, critical | 8 affected, 2 cleared (live) — 5 / 2 in fixture, see ARCHITECTURE.md |
| Approval gate | writeback button disabled pre-approval; API returns 403 |
| Writeback | `applied`, verified by reading tags and note back from DataHub |
| Dashboard | 0 console errors, 0 page errors, 0 failed requests |
| `test.ps1` | ruff / mypy / pytest / tsc / vitest all PASS |
| `pytest` with DataHub up | **96 passed, 0 skipped** |
| `check_api_types.ps1` | in sync |
| `export_examples.py` | no diff in `examples/` |
| `npm run build` | clean, 214 kB JS / 12.6 kB CSS |

## Troubleshooting

**Dashboard loads but every scenario errors.** The backend is not running or not
on `:8000`. Check `logs\api.err.log`.

**Header says fixture mode.** The two environment variables were not set in the
terminal that ran `start.ps1`. Stop, set them, start again — they are read at
process start.

**A diagnosis takes 5-10 seconds.** Expected in live mode; the Context agent is
making real MCP calls. Fixture mode is near-instant.

**`:9002` refuses to connect while GMS is healthy.** The frontend container
binds a little after GMS. Wait ~20 seconds.

**Nothing appears in DataHub's UI after ingestion.** Search is
Elasticsearch-backed and lags a second or two behind the write. The entity pages
themselves are immediate — navigate directly rather than searching.
