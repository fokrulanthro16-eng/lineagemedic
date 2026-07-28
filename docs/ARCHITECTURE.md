# Architecture

This document explains how LineageMedic is put together, why the boundaries sit
where they do, and exactly which files change when the live DataHub integration
lands.

## The shape of the problem

Diagnosing a silent data failure requires two kinds of information that live in
different places:

1. **Measurements** — what is actually wrong with the data right now. This
   comes from querying the warehouse.
2. **Context** — what the assets are, who owns them, and what depends on them.
   This comes from the metadata catalog.

Neither alone is sufficient. Measurements without lineage tell you a column is
bad but not what it contaminates. Lineage without measurements tells you what
*could* be affected but not whether anything is wrong. LineageMedic's core loop
is joining the two: measure, then walk the graph from the measurement's
location.

## Ports and adapters

The catalog is behind two protocols in
`packages/lineagemedic/src/lineagemedic/adapters/base.py`:

```python
@runtime_checkable
class MetadataPort(Protocol):
    def source(self) -> DataSource: ...
    def health(self) -> tuple[bool, str]: ...
    def capabilities(self) -> list[str]: ...
    def search_assets(self, query: str, limit: int = 10) -> list[Asset]: ...
    def get_asset(self, urn: str) -> Asset: ...
    def get_lineage(self, urn: str, ...) -> LineageGraph: ...
    def drain_calls(self) -> list[McpCallRecord]: ...

@runtime_checkable
class WritebackPort(Protocol):
    def source(self) -> DataSource: ...
    def write_incident_metadata(self, ...) -> WritebackReceipt: ...
```

Reads and writes are separated deliberately. Reading the catalog is safe and
happens on every diagnosis; writing to it mutates shared state that other teams
depend on, and is gated behind human approval. Giving them one interface would
make it easy for a future change to acquire write capability implicitly.

`source()` is on both ports and is not decorative. It forces every adapter to
declare its provenance, and that value is stamped onto the results. There is no
way to obtain a lineage graph without also obtaining a statement of where it
came from.

### Why `Protocol` rather than an abstract base class

Structural typing means an adapter does not import from LineageMedic to satisfy
the interface — it just has the right shape, and mypy verifies that at the
composition root. When the live adapter is written against the DataHub SDK, it
will not inherit from anything here.

## The workflow

`packages/lineagemedic/src/lineagemedic/workflow.py` runs seven agents in a
fixed order. Each returns typed evidence that later agents consume:

| Agent | Module | Reads | Produces |
|-------|--------|-------|----------|
| Quality | `agents/quality.py` | SQLite warehouse | Check results with observed values and thresholds |
| Context | `agents/context.py` | `MetadataPort` | Assets, owners, schema, lineage graph |
| Impact | `agents/impact.py` | Lineage + checks | Affected and cleared assets, blast radius |
| Root Cause | `agents/root_cause.py` | All of the above | Ranked hypotheses with confidence |
| Remediation | `agents/remediation.py` | Root cause | Ordered plan with rollback |
| Safety | `agents/safety.py` | Plan | Which steps mutate shared state |
| Writeback | `agents/writeback.py` | `WritebackPort` | Receipt, or a refusal |

The order is a genuine dependency chain, not a presentation sequence. Impact
cannot run before Context has resolved the graph; Remediation cannot be
proposed before a root cause is identified.

### Determinism

The same inputs always produce the same diagnosis:

- The warehouse is generated from a fixed PRNG seed.
- A fixed reference clock (`REFERENCE_NOW = 2026-07-28T12:00:00Z`) makes
  freshness checks answer identically on every run.
- No agent consults a clock, a random source, or a network service to reach a
  conclusion.

This is why `examples/*.json` can be regenerated in CI and diffed byte-for-byte.
A nondeterministic pipeline could not be held to that standard.

### Where the LLM sits — and does not

`packages/lineagemedic/src/lineagemedic/llm.py` provides an optional narrator
that rephrases the summary. It runs **after** the diagnosis is complete and
cannot alter severity, evidence, root cause, or the remediation plan. If Ollama
is unavailable the narrator is a no-op and the diagnosis is byte-identical.

The reason is simple: an incident-response tool whose conclusions change based
on the availability of a language model is not trustworthy during an incident.

## Severity derivation

Severity is computed from measurements:

- **critical** — checks failed *and* the blast radius reaches a deployed model
  or production endpoint.
- **warning** — a measurable problem that has not reached production assets.
- **healthy** — checks passed; the asset is explicitly cleared and stays in
  service.

Scenarios record an `expected_severity`, but that value is never an input to
the derivation. It exists so `scripts/demo.ps1` can compare expectation against
outcome and print `MISMATCH` when they disagree, and so the test suite can
assert the derivation still behaves.

## The approval gate

Three independent layers refuse an unapproved writeback:

1. **Safety agent** classifies writeback as an action requiring approval.
2. **Writeback agent** refuses to act unless `approval_state == "approved"`.
3. **HTTP endpoint** returns 403 before reaching the workflow.

Each is separately tested. Defence in depth here is warranted because the
failure mode — a tool that silently mutates a shared catalog — is exactly the
class of problem this project exists to prevent.

## The API contract

Frontend types are generated, not hand-written:

```
Pydantic models  →  scripts/export_openapi.py  →  scripts/openapi.json
                 →  openapi-typescript          →  apps/web/src/api/schema.ts
```

`scripts/check_api_types.ps1` regenerates both and fails if either differs from
what is committed; CI runs the same check. This makes "changed a response model
and forgot the frontend" a red build instead of a runtime surprise.

`apps/web/src/api/types.ts` layers readable domain aliases over the generated
schema. Only that file should be edited by hand — `schema.ts` is overwritten.

## Fixture mode

This build ships `adapters/fixture.py`, which serves committed metadata and
reports `DataSource.FIXTURE`. Consequences, all deliberate:

- Diagnoses carry `context_source: "fixture"`.
- Status endpoints report `datahub_connected: false`.
- An approved writeback returns `skipped_fixture_mode`.
- `LINEAGEMEDIC_MODE=live` returns **HTTP 501** rather than falling back to
  fixtures while claiming to be live. This is covered by
  `test_live_mode_refuses_rather_than_falling_back_to_fixtures`.

Fixture mode is not a mock of a successful integration. It is an accurate
report of a system with no catalog attached.

---

## Files requiring real integration work

When DataHub and Docker are available, these are the files that change. Nothing
else in the codebase should need to move, because the workflow depends only on
the two protocols.

### New files

| File | Responsibility |
|------|----------------|
| `packages/lineagemedic/src/lineagemedic/adapters/datahub_mcp.py` | `MetadataPort` over the DataHub MCP Server: search, asset fetch, lineage traversal. Must record every call via `drain_calls()` and return `DataSource.LIVE_DATAHUB`. |
| `packages/lineagemedic/src/lineagemedic/adapters/datahub_sdk.py` | `WritebackPort` over the DataHub Python SDK: emit incident metadata, tags, and documentation aspects. Returns a receipt describing what was actually emitted. |
| `scripts/ingest_lineage.py` | Push the healthcare warehouse and the ML lineage chain into DataHub so the graph exists to be traversed. |
| `docker-compose.yml` (or DataHub quickstart) | Local DataHub OSS. |
| `tests/test_datahub_integration.py` | Integration tests, skipped when no DataHub is reachable. |

### Modified files

| File | Change |
|------|--------|
| `apps/api/lineagemedic_api/main.py` | `build_workflow()` currently raises 501 for live mode. Replace that branch with construction of the live adapters. **This is the single switch point.** |
| `apps/api/lineagemedic_api/config.py` | Already reads `DATAHUB_GMS_URL`, `DATAHUB_FRONTEND_URL`, `DATAHUB_GMS_TOKEN`, `MCP_SERVER_URL`, and `MCP_TIMEOUT_SECONDS`. **Note:** `MCP_SERVER_URL` defaults to `http://localhost:8000/mcp`, which collides with the API's own default port. Set it explicitly, or move one of the two, when the MCP server is actually running. |
| `packages/lineagemedic/src/lineagemedic/agents/context.py` | Should need no logic change — it consumes `MetadataPort`. Verify URN construction matches what the live catalog returns. |
| `README.md` | Remove the fixture-mode notice once live mode is genuinely reachable. |

### Explicitly unchanged

The seven agents, the models, the workflow orchestrator, the FastAPI routes,
and the entire frontend. If connecting DataHub requires editing those, the port
boundary was drawn in the wrong place.

## Known limitations

- Live DataHub adapters are not implemented in this build; live mode returns 501.
- Quality checks run against SQLite. Warehouse-specific dialects are not abstracted.
- Lineage traversal assumes a directed acyclic graph. `computeDepths` in the
  frontend is cycle-safe, but the backend's traversal assumes DataHub emits a DAG.
- The narrator supports Ollama only; no hosted provider is wired, by design.
