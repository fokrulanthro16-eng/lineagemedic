"""LineageMedic HTTP API.

A typed FastAPI application over the diagnosis workflow. Every response body is
a Pydantic model, so the OpenAPI schema at ``/docs`` is generated from the same
definitions the agents use internally.

Three properties are enforced at this layer and covered by tests:

*   **Provenance is never dropped.** Responses carry ``context_source`` and, in
    fixture mode, a ``fixture_mode_notice`` the UI renders as a banner.
*   **Approval precedes mutation.** ``/writeback`` refuses unless the incident
    has been explicitly approved through ``/approve``.
*   **Reset is scoped.** ``/demo/reset`` clears in-process state only.
"""

from __future__ import annotations

import logging
import time
from typing import Literal

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from lineagemedic.adapters.base import AdapterError, MetadataPort, WritebackPort
from lineagemedic.adapters.datahub_mcp import DataHubMetadataAdapter
from lineagemedic.adapters.datahub_sdk import DataHubWritebackAdapter
from lineagemedic.adapters.fixture import (
    FIXTURE_NOTICE,
    FixtureMetadataAdapter,
    FixtureWritebackAdapter,
)
from lineagemedic.llm import build_narrator
from lineagemedic.models import (
    ApprovalState,
    AuditEvent,
    Diagnosis,
    IntegrationStatus,
    McpCallRecord,
    ScenarioSummary,
    Severity,
    WritebackReceipt,
    WritebackStatus,
)
from lineagemedic.scenarios import ALL_SCENARIOS, get_scenario
from lineagemedic.workflow import Workflow
from lineagemedic_api.config import Settings, get_settings
from lineagemedic_api.store import IncidentStore

logging.basicConfig(
    level=get_settings().log_level,
    format="%(asctime)s %(levelname)-8s %(name)s | %(message)s",
)
logger = logging.getLogger("lineagemedic.api")

app = FastAPI(
    title="LineageMedic API",
    version="0.1.0",
    description=(
        "Diagnose, contain, and heal silent data failures before they break "
        "production ML. Uses DataHub for schema, ownership, and lineage context."
    ),
)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

store = IncidentStore()


# ---------------------------------------------------------------------------
# Composition root
# ---------------------------------------------------------------------------


def build_metadata_adapter(cfg: Settings) -> MetadataPort:
    """Pick the read-side adapter for the current mode.

    In live mode the DataHub connection is probed before it is handed out. A
    configured-but-unreachable DataHub returns 503 rather than falling back to
    fixtures: a deployment that believes it is reading a live catalog when it is
    not is precisely the failure this project exists to prevent.
    """
    if cfg.is_fixture_mode:
        return FixtureMetadataAdapter(frontend_url=cfg.datahub_frontend_url)

    adapter = DataHubMetadataAdapter(
        gms_url=cfg.datahub_gms_url,
        frontend_url=cfg.datahub_frontend_url,
        token=cfg.datahub_token,
        timeout_seconds=cfg.mcp_timeout_seconds,
    )
    reachable, detail = adapter.health()
    if not reachable:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                f"LINEAGEMEDIC_MODE=live but DataHub is not reachable. {detail} "
                "Start DataHub, or set LINEAGEMEDIC_MODE=fixture to run in Demo "
                "Fixture Mode. Fixtures are never substituted silently in live mode."
            ),
        )
    return adapter


def build_writeback_adapter(cfg: Settings) -> WritebackPort:
    """Pick the write-side adapter for the current mode."""
    if cfg.is_fixture_mode:
        return FixtureWritebackAdapter()
    return DataHubWritebackAdapter(
        gms_url=cfg.datahub_gms_url,
        frontend_url=cfg.datahub_frontend_url,
        token=cfg.datahub_token,
        timeout_seconds=cfg.mcp_timeout_seconds,
    )


def status_adapter(cfg: Settings) -> MetadataPort:
    """A read adapter for the status endpoints, which must never raise.

    :func:`build_metadata_adapter` raises 503 when live mode cannot reach
    DataHub, which is right for a diagnosis but wrong for a status probe - the
    status endpoints exist precisely to report that DataHub is down. This
    constructs the live adapter without health-gating it, so ``health()`` can
    return the real reason.
    """
    if cfg.is_fixture_mode:
        return FixtureMetadataAdapter(frontend_url=cfg.datahub_frontend_url)
    return DataHubMetadataAdapter(
        gms_url=cfg.datahub_gms_url,
        frontend_url=cfg.datahub_frontend_url,
        token=cfg.datahub_token,
        timeout_seconds=cfg.mcp_timeout_seconds,
    )


def build_workflow(cfg: Settings | None = None) -> Workflow:
    """Assemble the workflow with the adapters the current mode calls for.

    This is the single switch point between fixture and live operation. The
    workflow and all seven agents depend only on the two protocols, so nothing
    below this function is aware of which backend is in play.
    """
    cfg = cfg or get_settings()
    narrator = build_narrator(
        cfg.llm_provider,
        ollama_url=cfg.ollama_base_url,
        ollama_model=cfg.ollama_model,
    )
    return Workflow(
        metadata=build_metadata_adapter(cfg),
        writeback=build_writeback_adapter(cfg),
        db_path=cfg.db_path,
        narrator=narrator.narrate,
    )


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    status: Literal["ok"]
    version: str
    mode: str
    database_present: bool


class DiagnoseRequest(BaseModel):
    scenario_id: str = Field(description="Scenario to run. See GET /scenarios.")


class ApprovalRequest(BaseModel):
    approved: bool = Field(description="True to approve the plan, False to reject it.")
    approver: str = Field(
        default="operator",
        max_length=120,
        description="Who made the decision. Recorded in the audit log.",
    )
    note: str = Field(default="", max_length=2000)


class ApprovalResponse(BaseModel):
    incident_id: str
    approval_state: ApprovalState
    message: str


class ResetResponse(BaseModel):
    cleared_incidents: int
    message: str


class McpCapabilityResponse(BaseModel):
    connected: bool
    detail: str
    source: str
    tools: list[str]
    recent_calls: list[McpCallRecord]


class DataHubStatusResponse(BaseModel):
    connected: bool
    detail: str
    gms_url: str
    frontend_url: str
    token_configured: bool
    fixture_mode_notice: str | None


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


@app.exception_handler(AdapterError)
async def _adapter_error_handler(_: Request, exc: AdapterError) -> JSONResponse:
    """Surface adapter failures as 502 rather than leaking a stack trace."""
    logger.warning("adapter error: %s", exc)
    return JSONResponse(
        status_code=status.HTTP_502_BAD_GATEWAY,
        content={"detail": f"Metadata backend could not answer: {exc}"},
    )


@app.middleware("http")
async def _log_requests(request: Request, call_next):  # type: ignore[no-untyped-def]
    """Structured access log with duration. Never logs headers or tokens."""
    started = time.perf_counter()
    response = await call_next(request)
    logger.info(
        "%s %s -> %s in %.1fms",
        request.method,
        request.url.path,
        response.status_code,
        (time.perf_counter() - started) * 1000,
    )
    return response


# ---------------------------------------------------------------------------
# Status endpoints
# ---------------------------------------------------------------------------


@app.get("/health", response_model=HealthResponse, tags=["status"])
def health() -> HealthResponse:
    """Liveness probe. Does not touch DataHub."""
    cfg = get_settings()
    return HealthResponse(
        status="ok",
        version=app.version,
        mode=cfg.mode,
        database_present=cfg.db_path.exists(),
    )


@app.get("/status/datahub", response_model=DataHubStatusResponse, tags=["status"])
def datahub_status() -> DataHubStatusResponse:
    """Report DataHub connectivity truthfully, including when it is absent."""
    cfg = get_settings()
    adapter = status_adapter(cfg)
    connected, detail = adapter.health()
    return DataHubStatusResponse(
        connected=connected,
        detail=detail,
        gms_url=cfg.datahub_gms_url,
        frontend_url=cfg.datahub_frontend_url,
        token_configured=bool(cfg.datahub_token),
        fixture_mode_notice=FIXTURE_NOTICE if cfg.is_fixture_mode else None,
    )


@app.get("/status/mcp", response_model=McpCapabilityResponse, tags=["status"])
def mcp_status() -> McpCapabilityResponse:
    """List MCP tools available, and the calls the last diagnosis made."""
    cfg = get_settings()
    adapter = status_adapter(cfg)
    connected, detail = adapter.health()
    recent = store.list()
    return McpCapabilityResponse(
        connected=connected,
        detail=detail,
        source=adapter.source.value,
        tools=adapter.capabilities(),
        recent_calls=recent[0].mcp_calls if recent else [],
    )


@app.get("/status/integrations", response_model=IntegrationStatus, tags=["status"])
def integration_status() -> IntegrationStatus:
    """Consolidated status for the UI status bar."""
    cfg = get_settings()
    adapter = status_adapter(cfg)
    datahub_ok, datahub_detail = adapter.health()
    narrator = build_narrator(
        cfg.llm_provider, ollama_url=cfg.ollama_base_url, ollama_model=cfg.ollama_model
    )
    llm_ok, llm_detail = narrator.available()
    return IntegrationStatus(
        mode="fixture" if cfg.is_fixture_mode else "live",
        datahub_connected=datahub_ok,
        datahub_detail=datahub_detail,
        mcp_connected=datahub_ok,
        mcp_detail=detail_for_mcp(
            datahub_ok, datahub_detail, fixture_mode=cfg.is_fixture_mode
        ),
        llm_provider=narrator.provider_name,
        llm_available=llm_ok,
        llm_detail=llm_detail,
        fixture_mode_notice=FIXTURE_NOTICE if cfg.is_fixture_mode else None,
    )


def detail_for_mcp(connected: bool, detail: str, *, fixture_mode: bool = True) -> str:
    """Phrase the MCP status so the reason for a disconnection is unambiguous.

    A disconnection means two different things in the two modes, and conflating
    them would be a fabricated status. In fixture mode nothing is expected to be
    connected and reads are genuinely served from fixtures. In live mode a
    disconnection is a fault: no fixture substitution happens, and diagnosis
    requests fail rather than returning fixture data.
    """
    if connected:
        return detail
    if fixture_mode:
        return (
            "No MCP server is connected. Metadata reads are served from committed "
            "fixtures and are labelled as such on every response."
        )
    return (
        f"Live mode is configured but DataHub is not reachable. {detail} Diagnosis "
        "requests will fail with 503; fixture data is never substituted in live mode."
    )


# ---------------------------------------------------------------------------
# Scenarios and incidents
# ---------------------------------------------------------------------------


@app.get("/scenarios", response_model=list[ScenarioSummary], tags=["incidents"])
def list_scenarios() -> list[ScenarioSummary]:
    """The runnable scenarios, ordered worst-expected-case first."""
    return sorted(
        (s.summary() for s in ALL_SCENARIOS.values()),
        key=lambda s: s.expected_severity.rank,
        reverse=True,
    )


@app.post("/diagnose", response_model=Diagnosis, tags=["incidents"])
def diagnose(request: DiagnoseRequest) -> Diagnosis:
    """Run the seven-agent workflow. Performs no mutation."""
    try:
        scenario = get_scenario(request.scenario_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from None

    store.record(
        kind="diagnosis_started",
        message=f"Diagnosis started for scenario {scenario.scenario_id}.",
        metadata={"scenario_id": scenario.scenario_id},
    )
    workflow = build_workflow()
    try:
        diagnosis = workflow.diagnose(scenario)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from None

    store.put(diagnosis)
    store.record(
        kind="diagnosis_completed",
        incident_id=diagnosis.incident_id,
        message=(
            f"Severity {diagnosis.severity.value}; {diagnosis.impact.affected_count} "
            f"asset(s) affected, {diagnosis.impact.unaffected_count} cleared."
        ),
        metadata={"severity": diagnosis.severity.value, "scenario_id": scenario.scenario_id},
    )
    if diagnosis.approval_state is ApprovalState.PENDING:
        store.record(
            kind="approval_requested",
            incident_id=diagnosis.incident_id,
            message=(
                f"{len(diagnosis.remediation)} action(s) require human approval before "
                "any state change or writeback."
            ),
        )
    return diagnosis


@app.get("/incidents", response_model=list[Diagnosis], tags=["incidents"])
def list_incidents() -> list[Diagnosis]:
    """All diagnoses computed since the last reset, newest first."""
    return store.list()


@app.get("/incidents/{incident_id}", response_model=Diagnosis, tags=["incidents"])
def get_incident(incident_id: str) -> Diagnosis:
    diagnosis = store.get(incident_id)
    if diagnosis is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown incident {incident_id}"
        )
    return diagnosis


@app.get("/incidents/{incident_id}/evidence", tags=["incidents"])
def get_evidence(incident_id: str) -> dict[str, object]:
    """Evidence and the MCP call trace backing one incident."""
    diagnosis = get_incident(incident_id)
    return {
        "incident_id": diagnosis.incident_id,
        "context_source": diagnosis.context_source.value,
        "fixture_mode_notice": diagnosis.fixture_mode_notice,
        "evidence": [e.model_dump(mode="json") for e in diagnosis.evidence],
        "mcp_calls": [c.model_dump(mode="json") for c in diagnosis.mcp_calls],
        "quality_checks": [c.model_dump(mode="json") for c in diagnosis.quality_checks],
    }


@app.get("/audit", response_model=list[AuditEvent], tags=["incidents"])
def get_audit(incident_id: str | None = None) -> list[AuditEvent]:
    """Append-only audit log, newest first."""
    return store.audit(incident_id)


# ---------------------------------------------------------------------------
# Approval and writeback
# ---------------------------------------------------------------------------


@app.post("/incidents/{incident_id}/approve", response_model=ApprovalResponse, tags=["approval"])
def approve(incident_id: str, request: ApprovalRequest) -> ApprovalResponse:
    """Record a human decision on the remediation plan."""
    diagnosis = get_incident(incident_id)

    if diagnosis.approval_state is ApprovalState.NOT_REQUIRED:
        return ApprovalResponse(
            incident_id=incident_id,
            approval_state=ApprovalState.NOT_REQUIRED,
            message=(
                "This incident proposed no state-changing action, so there is nothing "
                "to approve."
            ),
        )

    new_state = ApprovalState.APPROVED if request.approved else ApprovalState.REJECTED
    diagnosis.approval_state = new_state
    store.put(diagnosis)
    store.record(
        kind="approval_granted" if request.approved else "approval_rejected",
        incident_id=incident_id,
        message=(
            f"{request.approver} {'approved' if request.approved else 'rejected'} the "
            f"remediation plan." + (f" Note: {request.note}" if request.note else "")
        ),
        actor=request.approver,
    )
    return ApprovalResponse(
        incident_id=incident_id,
        approval_state=new_state,
        message=(
            "Approved. Writeback to DataHub is now permitted."
            if request.approved
            else "Rejected. No action will be taken and nothing will be written to DataHub."
        ),
    )


@app.post("/incidents/{incident_id}/writeback", response_model=WritebackReceipt, tags=["approval"])
def writeback(incident_id: str) -> WritebackReceipt:
    """Write incident knowledge to DataHub. Requires prior approval."""
    diagnosis = get_incident(incident_id)
    approved = diagnosis.approval_state is ApprovalState.APPROVED

    store.record(
        kind="writeback_attempted",
        incident_id=incident_id,
        message=f"Writeback attempted with approval_state={diagnosis.approval_state.value}.",
    )

    if not approved:
        store.record(
            kind="writeback_blocked",
            incident_id=incident_id,
            message="Writeback blocked: the plan has not been approved by a human.",
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Writeback requires approval. Incident {incident_id} is currently "
                f"{diagnosis.approval_state.value}. POST to /incidents/{incident_id}/approve "
                "first."
            ),
        )

    receipt, step = build_workflow().apply_writeback(diagnosis, approved=True)
    diagnosis.writeback = receipt
    diagnosis.steps = [*diagnosis.steps, step]
    store.put(diagnosis)
    store.record(
        kind=(
            "writeback_applied"
            if receipt.status is WritebackStatus.APPLIED
            else "writeback_blocked"
        ),
        incident_id=incident_id,
        message=receipt.note,
        metadata={"status": receipt.status.value, "targets": len(receipt.target_urns)},
    )
    return receipt


# ---------------------------------------------------------------------------
# Demo control
# ---------------------------------------------------------------------------


@app.post("/demo/reset", response_model=ResetResponse, tags=["demo"])
def demo_reset() -> ResetResponse:
    """Clear LineageMedic-owned state only.

    Does not touch DataHub, and does not rebuild the warehouse. Use
    ``scripts/setup.ps1`` to regenerate the SQLite database.
    """
    cleared = store.reset()
    return ResetResponse(
        cleared_incidents=cleared,
        message=(
            f"Cleared {cleared} incident(s) and the audit log. No DataHub metadata and "
            "no warehouse data were modified."
        ),
    )


@app.get("/", tags=["status"])
def root() -> dict[str, object]:
    """Service banner with the effective, redacted configuration."""
    cfg = get_settings()
    return {
        "service": "LineageMedic",
        "version": app.version,
        "tagline": (
            "Diagnose, contain, and heal silent data failures before they break "
            "production ML."
        ),
        "docs": "/docs",
        "config": cfg.redacted(),
        "fixture_mode_notice": FIXTURE_NOTICE if cfg.is_fixture_mode else None,
        "severities": [s.value for s in Severity],
    }
