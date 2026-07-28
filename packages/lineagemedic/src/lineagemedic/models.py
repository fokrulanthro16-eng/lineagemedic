"""Typed domain models for LineageMedic.

Every structure that crosses an agent boundary, an adapter boundary, or the HTTP
API is defined here as a Pydantic model. Agents never pass raw dicts to each
other; the workflow is a pipeline of validated objects.

Two conventions matter throughout:

*   **Provenance is mandatory.** Anything sourced from DataHub carries a
    :class:`DataSource` saying whether it came from a live instance or from a
    committed fixture. There is no "unknown" default — a caller must state it.
*   **Nothing claims success it did not observe.** Writeback receipts and MCP
    call records model failure as a first-class outcome, so a blocked or errored
    call is representable rather than being squeezed into a success shape.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utcnow() -> datetime:
    """Timezone-aware UTC now. Used as the default factory for every timestamp."""
    return datetime.now(UTC)


class StrictModel(BaseModel):
    """Base for all LineageMedic models: reject unknown fields, stay immutable-ish."""

    model_config = ConfigDict(extra="forbid", use_enum_values=False, validate_assignment=True)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class DataSource(str, Enum):
    """Where a piece of context actually came from.

    This is the anti-fabrication primitive. ``FIXTURE`` is surfaced in the API
    and rendered as a banner in the UI so a demo can never be mistaken for a
    live DataHub reading.
    """

    LIVE_DATAHUB = "live_datahub"
    FIXTURE = "fixture"


class Severity(str, Enum):
    """Incident severity, ordered."""

    CRITICAL = "critical"
    WARNING = "warning"
    HEALTHY = "healthy"

    @property
    def rank(self) -> int:
        """Higher means worse. Used for sorting and for max() reductions."""
        return {"healthy": 0, "warning": 1, "critical": 2}[self.value]


class AssetKind(str, Enum):
    """The kind of node in the lineage graph."""

    DATASET = "dataset"
    FEATURE_TABLE = "feature_table"
    ML_MODEL = "ml_model"
    ENDPOINT = "endpoint"


class ImpactState(str, Enum):
    """Whether an asset lies in the blast radius of an incident."""

    AFFECTED = "affected"
    UNAFFECTED = "unaffected"
    SOURCE = "source"


class CheckStatus(str, Enum):
    """Outcome of a single data-quality check."""

    PASS = "pass"
    FAIL = "fail"


class AgentName(str, Enum):
    """The seven workflow agents, in execution order."""

    QUALITY = "quality"
    CONTEXT = "context"
    IMPACT = "impact"
    ROOT_CAUSE = "root_cause"
    REMEDIATION = "remediation"
    SAFETY = "safety"
    WRITEBACK = "writeback"


class ApprovalState(str, Enum):
    """Human approval gate state for a remediation plan."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    NOT_REQUIRED = "not_required"


class ActionRisk(str, Enum):
    """How dangerous a proposed remediation action is."""

    SAFE = "safe"
    REVERSIBLE = "reversible"
    DESTRUCTIVE = "destructive"


class WritebackStatus(str, Enum):
    """Terminal state of an attempted DataHub metadata writeback."""

    APPLIED = "applied"
    BLOCKED_PENDING_APPROVAL = "blocked_pending_approval"
    SKIPPED_FIXTURE_MODE = "skipped_fixture_mode"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# Lineage and DataHub context
# ---------------------------------------------------------------------------


class SchemaField(StrictModel):
    """One column of a dataset schema, as reported by DataHub."""

    name: str
    native_type: str
    nullable: bool = True
    description: str | None = None


class Owner(StrictModel):
    """An owning user or group.

    ``contact`` is a team channel or distribution list, never a personal email
    address — fixtures use role accounts so no personal information ships in the
    repository.
    """

    urn: str
    display_name: str
    type: Literal["TECHNICAL_OWNER", "BUSINESS_OWNER", "DATA_STEWARD"]
    contact: str | None = None


class Asset(StrictModel):
    """A node in the lineage graph: dataset, feature table, model, or endpoint."""

    urn: str
    name: str
    kind: AssetKind
    platform: str
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    owners: list[Owner] = Field(default_factory=list)
    schema_fields: list[SchemaField] = Field(default_factory=list)
    upstreams: list[str] = Field(default_factory=list)
    downstreams: list[str] = Field(default_factory=list)
    source: DataSource
    datahub_url: str | None = None

    @field_validator("urn")
    @classmethod
    def _urn_must_look_like_urn(cls, v: str) -> str:
        if not v.startswith("urn:li:"):
            raise ValueError(f"not a DataHub URN: {v!r}")
        return v


class LineageGraph(StrictModel):
    """The subgraph retrieved for one incident, plus its provenance."""

    assets: list[Asset]
    source: DataSource
    retrieved_at: datetime = Field(default_factory=utcnow)

    def by_urn(self, urn: str) -> Asset | None:
        """Look up a single asset, or ``None`` if it is not in this subgraph."""
        return next((a for a in self.assets if a.urn == urn), None)

    def downstream_closure(self, start_urn: str) -> list[str]:
        """Every URN reachable downstream of ``start_urn``, breadth-first.

        Cycle-safe: an asset is expanded at most once. The starting URN is not
        included in the result.
        """
        seen: set[str] = {start_urn}
        order: list[str] = []
        queue: list[str] = [start_urn]
        while queue:
            current = queue.pop(0)
            asset = self.by_urn(current)
            if asset is None:
                continue
            for child in asset.downstreams:
                if child not in seen:
                    seen.add(child)
                    order.append(child)
                    queue.append(child)
        return order


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


class QualityCheck(StrictModel):
    """One executed data-quality check and its measured result.

    These numbers come from really querying the bundled SQLite database — they
    are computed, never hard-coded.
    """

    check_id: str
    description: str
    dataset_urn: str
    column: str | None = None
    status: CheckStatus
    observed_value: float
    threshold: float
    comparison: Literal["lte", "gte", "eq"]
    rows_scanned: int
    failing_rows: int = 0
    sample_failing_values: list[str] = Field(default_factory=list)

    @property
    def failure_ratio(self) -> float:
        """Fraction of scanned rows that failed, in ``[0, 1]``."""
        if self.rows_scanned == 0:
            return 0.0
        return self.failing_rows / self.rows_scanned


class EvidenceItem(StrictModel):
    """A single human-readable fact backing a conclusion.

    Evidence is what LineageMedic shows instead of chain-of-thought: each item
    names its origin agent and points at the artifact it was derived from.
    """

    label: str
    detail: str
    agent: AgentName
    source: DataSource
    references: list[str] = Field(default_factory=list)
    observed_at: datetime = Field(default_factory=utcnow)


# ---------------------------------------------------------------------------
# MCP call transparency
# ---------------------------------------------------------------------------


class McpCallRecord(StrictModel):
    """An audit record of one MCP tool invocation.

    Recorded for every call regardless of outcome, so the UI can prove which
    tools ran, with what (sanitized) arguments, and what URNs came back.
    """

    tool: str
    arguments: dict[str, Any]
    ok: bool
    returned_urns: list[str] = Field(default_factory=list)
    error: str | None = None
    duration_ms: float
    source: DataSource
    called_at: datetime = Field(default_factory=utcnow)


# ---------------------------------------------------------------------------
# Impact, root cause, remediation
# ---------------------------------------------------------------------------


class ImpactedAsset(StrictModel):
    """An asset classified against the blast radius, with the reason why."""

    urn: str
    name: str
    kind: AssetKind
    state: ImpactState
    hops_from_source: int | None = None
    rationale: str


class ImpactAssessment(StrictModel):
    """The full affected/unaffected partition for an incident."""

    source_urn: str
    assets: list[ImpactedAsset]
    affected_count: int
    unaffected_count: int
    production_endpoints_affected: list[str] = Field(default_factory=list)
    ml_models_affected: list[str] = Field(default_factory=list)

    @property
    def affected(self) -> list[ImpactedAsset]:
        return [a for a in self.assets if a.state is ImpactState.AFFECTED]

    @property
    def unaffected(self) -> list[ImpactedAsset]:
        return [a for a in self.assets if a.state is ImpactState.UNAFFECTED]


class RootCauseHypothesis(StrictModel):
    """One ranked candidate explanation for the observed failure."""

    summary: str
    suspected_urn: str
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str
    supporting_evidence: list[str] = Field(default_factory=list)


class RemediationAction(StrictModel):
    """A single proposed corrective step.

    ``rollback`` is required for anything not classified ``SAFE``: the
    Remediation Agent may not propose a step it cannot describe undoing.
    """

    action_id: str
    title: str
    description: str
    risk: ActionRisk
    target_urn: str
    reversible: bool
    rollback: str | None = None
    requires_approval: bool

    @model_validator(mode="after")
    def _reversible_actions_declare_rollback(self) -> RemediationAction:
        """A step that changes state must say how to undo it.

        Model-level rather than field-level: ``rollback`` is optional, and a
        field validator would not run at all when the caller omits it — which
        is exactly the case this rule exists to catch.
        """
        if self.risk in (ActionRisk.REVERSIBLE, ActionRisk.DESTRUCTIVE) and not self.rollback:
            raise ValueError(f"{self.risk.value} action must declare a rollback procedure")
        return self


class SafetyVerdict(StrictModel):
    """The Safety Agent's ruling on a remediation plan."""

    approved_actions: list[str]
    blocked_actions: list[str]
    requires_human_approval: bool
    blocking_reasons: dict[str, str] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


class WritebackReceipt(StrictModel):
    """Proof (or honest refusal) of a DataHub metadata mutation.

    In fixture mode this is always ``SKIPPED_FIXTURE_MODE`` with an empty
    ``aspects_written``. The application never emits ``APPLIED`` unless a live
    adapter reported a real, verified mutation.
    """

    status: WritebackStatus
    target_urns: list[str] = Field(default_factory=list)
    aspects_written: list[str] = Field(default_factory=list)
    tags_added: list[str] = Field(default_factory=list)
    note: str
    datahub_urls: list[str] = Field(default_factory=list)
    source: DataSource
    attempted_at: datetime = Field(default_factory=utcnow)
    error: str | None = None


# ---------------------------------------------------------------------------
# Agent execution trace
# ---------------------------------------------------------------------------


class AgentStep(StrictModel):
    """One agent's execution record, for the UI timeline."""

    agent: AgentName
    title: str
    summary: str
    started_at: datetime
    duration_ms: float
    ok: bool = True
    evidence: list[EvidenceItem] = Field(default_factory=list)
    mcp_calls: list[McpCallRecord] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Scenario definition and diagnosis result
# ---------------------------------------------------------------------------


class ScenarioSummary(StrictModel):
    """Catalog entry for a runnable scenario."""

    scenario_id: str
    title: str
    description: str
    expected_severity: Severity


class Diagnosis(StrictModel):
    """The complete structured output of one workflow run.

    This is the object the API returns and the UI renders. It is fully
    self-describing: severity, evidence, impact, remediation, approval state,
    and the provenance of every DataHub fact it relied on.
    """

    incident_id: str
    scenario_id: str
    title: str
    severity: Severity
    confidence: float = Field(ge=0.0, le=1.0)
    confidence_explanation: str
    summary: str

    quality_checks: list[QualityCheck] = Field(default_factory=list)
    lineage: LineageGraph
    impact: ImpactAssessment
    root_causes: list[RootCauseHypothesis] = Field(default_factory=list)
    remediation: list[RemediationAction] = Field(default_factory=list)
    safety: SafetyVerdict
    approval_state: ApprovalState
    writeback: WritebackReceipt | None = None

    evidence: list[EvidenceItem] = Field(default_factory=list)
    steps: list[AgentStep] = Field(default_factory=list)
    mcp_calls: list[McpCallRecord] = Field(default_factory=list)

    context_source: DataSource
    fixture_mode_notice: str | None = None
    narration: str | None = None
    narration_provider: str = "deterministic"
    created_at: datetime = Field(default_factory=utcnow)

    @property
    def primary_owner(self) -> Owner | None:
        """The technical owner of the root-cause asset, if the graph knows one."""
        if not self.root_causes:
            return None
        asset = self.lineage.by_urn(self.root_causes[0].suspected_urn)
        if asset is None:
            return None
        technical = [o for o in asset.owners if o.type == "TECHNICAL_OWNER"]
        return technical[0] if technical else (asset.owners[0] if asset.owners else None)


class AuditEvent(StrictModel):
    """An append-only record of something the application did."""

    event_id: str
    incident_id: str | None
    kind: Literal[
        "diagnosis_started",
        "diagnosis_completed",
        "approval_requested",
        "approval_granted",
        "approval_rejected",
        "writeback_attempted",
        "writeback_applied",
        "writeback_blocked",
        "demo_reset",
    ]
    message: str
    actor: str = "system"
    occurred_at: datetime = Field(default_factory=utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)


class IntegrationStatus(StrictModel):
    """Live status of each external dependency, for the UI status bar."""

    mode: Literal["fixture", "live"]
    datahub_connected: bool
    datahub_detail: str
    mcp_connected: bool
    mcp_detail: str
    llm_provider: str
    llm_available: bool
    llm_detail: str
    fixture_mode_notice: str | None = None
