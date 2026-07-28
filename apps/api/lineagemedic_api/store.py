"""In-process incident and audit store.

Deliberately in-memory. LineageMedic owns no durable user data: incidents are
derived from the warehouse and DataHub on demand, so a restart simply
recomputes them. That keeps the demo-reset story honest - resetting clears only
state this application created and touches nothing in DataHub or the warehouse.

Thread-safe because uvicorn runs request handlers concurrently and a diagnosis
mutates shared state when it is approved.
"""

from __future__ import annotations

import builtins
import threading
import uuid
from typing import Literal

from lineagemedic.models import AuditEvent, Diagnosis


class IncidentStore:
    """Holds diagnoses and the append-only audit log for one process."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._incidents: dict[str, Diagnosis] = {}
        self._audit: list[AuditEvent] = []

    # -- incidents ----------------------------------------------------------

    def put(self, diagnosis: Diagnosis) -> Diagnosis:
        with self._lock:
            self._incidents[diagnosis.incident_id] = diagnosis
            return diagnosis

    def get(self, incident_id: str) -> Diagnosis | None:
        with self._lock:
            return self._incidents.get(incident_id)

    def list(self) -> list[Diagnosis]:
        """Newest first."""
        with self._lock:
            return sorted(self._incidents.values(), key=lambda d: d.created_at, reverse=True)

    # -- audit --------------------------------------------------------------

    def record(
        self,
        *,
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
        ],
        message: str,
        incident_id: str | None = None,
        actor: str = "system",
        metadata: dict[str, object] | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            event_id=f"EV-{uuid.uuid4().hex[:10]}",
            incident_id=incident_id,
            kind=kind,
            message=message,
            actor=actor,
            metadata=dict(metadata or {}),
        )
        with self._lock:
            self._audit.append(event)
        return event

    # ``list`` below refers to this class's own method, not the builtin, so the
    # return annotation is quoted via the builtins alias.
    def audit(self, incident_id: str | None = None) -> builtins.list[AuditEvent]:
        """Audit events, newest first, optionally filtered to one incident."""
        with self._lock:
            events = list(self._audit)
        if incident_id is not None:
            events = [e for e in events if e.incident_id == incident_id]
        return sorted(events, key=lambda e: e.occurred_at, reverse=True)

    # -- reset --------------------------------------------------------------

    def reset(self) -> int:
        """Drop all LineageMedic-owned state. Returns how many incidents were cleared.

        Scoped strictly to this store. Nothing in DataHub, and nothing in the
        warehouse, is affected.
        """
        with self._lock:
            count = len(self._incidents)
            self._incidents.clear()
            self._audit.clear()
        self.record(
            kind="demo_reset",
            message=(
                f"Cleared {count} in-memory incident(s) and the audit log. "
                "No DataHub or warehouse state was modified."
            ),
            actor="operator",
        )
        return count
