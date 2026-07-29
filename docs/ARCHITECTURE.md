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

`adapters/fixture.py` serves committed metadata and reports
`DataSource.FIXTURE`. It is the default mode and a clearly labelled fallback,
never a mock of a successful integration — it is an accurate report of a system
with no catalog attached. Consequences, all deliberate:

- Diagnoses carry `context_source: "fixture"`.
- Status endpoints report `datahub_connected: false`.
- An approved writeback returns `skipped_fixture_mode`.

The reverse guarantee holds in live mode: an unreachable DataHub returns **HTTP
503** rather than falling back to fixtures while claiming to be live. Covered by
`test_live_mode_refuses_rather_than_falling_back_to_fixtures`.

---

## The live DataHub integration

Implemented and verified against DataHub OSS v1.6.0.

| File | Responsibility |
|------|----------------|
| `adapters/datahub_mcp.py` | `MetadataPort` over DataHub's GraphQL API: search, asset fetch, lineage traversal. Records every call via `drain_calls()` and returns `DataSource.LIVE_DATAHUB`. |
| `adapters/datahub_sdk.py` | `WritebackPort` over the DataHub Python SDK: emits tags and documentation aspects, then verifies them by reading them back. |
| `scripts/ingest_lineage.py` | Pushes the healthcare warehouse and the ML lineage chain into DataHub so the graph exists to be traversed. |
| `docker/datahub-quickstart.override.yml` | Compose override supplying the token-service signing key the DataHub CLI would normally inject. |
| `tests/test_datahub_integration.py` | Integration tests, skipped when no DataHub is reachable. |

`build_workflow()` in `apps/api/lineagemedic_api/main.py` is the single switch
point: it constructs either the fixture or the live adapters. Everything
else — the seven agents, the models, the workflow orchestrator, the FastAPI
routes, and the entire frontend — was unchanged by the integration, which is the
outcome the port boundary was drawn to produce.

**Config note:** `MCP_SERVER_URL` defaults to `http://localhost:8000/mcp`, which
collides with the API's own default port. Set it explicitly, or move one of the
two, when an MCP server is actually running.

### What the live catalog forced

Four constraints of DataHub v1.6.0 were established by probing the running
instance, and each is documented with its evidence at the point it shaped the
code:

- **ML entities cannot carry dataset lineage.** `upstreamLineage` on `mlModel`
  is rejected 422 ("Unknown aspect upstreamLineage for entity mlModel"); the
  reverse — a dataset naming an `mlModel` as its upstream — is rejected 422 too
  ("Invalid format for aspect: dataset"), because `upstreams[].dataset` is a
  dataset-typed field. `MLModelDeployment` is not in the GraphQL schema at all.
  The dataset chain is therefore bridged by `dataJob` entities and their output
  datasets. See `_ml_bridge_mcps` in `scripts/ingest_lineage.py`.
- **A model links to lineage through jobs, not datasets.** `mlModelProperties`
  accepts `trainingJobs` and `downstreamJobs`, producing `TrainedBy` and
  `UsedBy` edges. Without them the model page renders with no lineage at all,
  since the dataset chain routes around the model rather than through it. Note
  `searchAcrossLineage` does not follow these edges — the UI's relationship-based
  `entity.lineage` resolver does, which is why the browser shows the connection
  and a hop-count traversal does not. See `_properties_aspect`.
- **DOWNSTREAM traversal does not follow dataset→job edges.** Those are
  `Consumes`; only `DownstreamOf` is traversed. Each bridge output dataset
  therefore also carries an `UpstreamLineage` aspect.
- **Subtypes carry the domain meaning.** The bridge entities are a `DATA_JOB`
  and a `DATASET` to DataHub. Only their emitted subtype marks one as a model
  and the other as an endpoint, and severity is derived from those counts —
  classifying on entity type alone silently downgraded a critical incident to a
  warning. See `_kind_for` in `adapters/datahub_mcp.py`.

## Known limitations

- Quality checks run against SQLite. Warehouse-specific dialects are not abstracted.
- Lineage traversal assumes a directed acyclic graph. `computeDepths` in the
  frontend is cycle-safe, but the backend's traversal assumes DataHub emits a DAG.
- The narrator supports Ollama only; no hosted provider is wired, by design.
