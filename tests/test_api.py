"""HTTP API tests, including the approval gate at the transport boundary."""

from __future__ import annotations

from fastapi.testclient import TestClient


def _diagnose(client: TestClient, scenario_id: str) -> dict:
    response = client.post("/diagnose", json={"scenario_id": scenario_id})
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def test_health(client: TestClient) -> None:
    body = client.get("/health").json()

    assert body["status"] == "ok"
    assert body["mode"] == "fixture"
    assert body["database_present"] is True


def test_datahub_status_admits_it_is_not_connected(client: TestClient) -> None:
    body = client.get("/status/datahub").json()

    assert body["connected"] is False
    assert "Demo Fixture Mode" in body["fixture_mode_notice"]
    # Presence only -- the token value must never appear in a response.
    assert body["token_configured"] is False
    assert "token" not in str(body).lower().replace("token_configured", "")


def test_mcp_status_lists_tools(client: TestClient) -> None:
    body = client.get("/status/mcp").json()

    assert body["source"] == "fixture"
    assert set(body["tools"]) == {"search", "get_dataset", "get_lineage"}
    assert body["connected"] is False


def test_integration_status_reports_all_three_dependencies(client: TestClient) -> None:
    body = client.get("/status/integrations").json()

    assert body["mode"] == "fixture"
    assert body["datahub_connected"] is False
    assert body["mcp_connected"] is False
    # The deterministic narrator is always available, with no model required.
    assert body["llm_available"] is True
    assert body["llm_provider"] == "deterministic"


def test_scenarios_are_listed_worst_first(client: TestClient) -> None:
    body = client.get("/scenarios").json()

    assert len(body) == 3
    assert body[0]["expected_severity"] == "critical"
    assert body[-1]["expected_severity"] == "healthy"


# ---------------------------------------------------------------------------
# Diagnosis
# ---------------------------------------------------------------------------


def test_diagnose_critical_returns_full_structure(client: TestClient) -> None:
    body = _diagnose(client, "critical-age-corruption")

    assert body["severity"] == "critical"
    assert body["context_source"] == "fixture"
    assert "Demo Fixture Mode" in body["fixture_mode_notice"]
    assert body["impact"]["affected_count"] == 5
    assert body["impact"]["unaffected_count"] == 2
    assert body["approval_state"] == "pending"
    assert len(body["steps"]) == 6
    assert body["mcp_calls"]
    assert body["narration"]


def test_diagnose_unknown_scenario_is_404(client: TestClient) -> None:
    response = client.post("/diagnose", json={"scenario_id": "nope"})
    assert response.status_code == 404


def test_incident_is_retrievable_after_diagnosis(client: TestClient) -> None:
    incident_id = _diagnose(client, "critical-age-corruption")["incident_id"]

    fetched = client.get(f"/incidents/{incident_id}")
    assert fetched.status_code == 200
    assert fetched.json()["incident_id"] == incident_id

    listed = client.get("/incidents").json()
    assert incident_id in [i["incident_id"] for i in listed]


def test_unknown_incident_is_404(client: TestClient) -> None:
    assert client.get("/incidents/LM-NOPE").status_code == 404


def test_evidence_endpoint_exposes_the_mcp_trace(client: TestClient) -> None:
    incident_id = _diagnose(client, "critical-age-corruption")["incident_id"]
    body = client.get(f"/incidents/{incident_id}/evidence").json()

    assert body["context_source"] == "fixture"
    assert body["evidence"]
    assert body["mcp_calls"]
    for call in body["mcp_calls"]:
        assert call["source"] == "fixture"
        assert call["tool"]


# ---------------------------------------------------------------------------
# Approval gate
# ---------------------------------------------------------------------------


def test_writeback_is_refused_before_approval(client: TestClient) -> None:
    """The gate must hold at the HTTP boundary, not only inside the agent."""
    incident_id = _diagnose(client, "critical-age-corruption")["incident_id"]

    response = client.post(f"/incidents/{incident_id}/writeback")

    assert response.status_code == 403
    assert "requires approval" in response.json()["detail"]


def test_writeback_after_approval_reports_fixture_mode_honestly(
    client: TestClient,
) -> None:
    incident_id = _diagnose(client, "critical-age-corruption")["incident_id"]

    approval = client.post(
        f"/incidents/{incident_id}/approve",
        json={"approved": True, "approver": "test-operator", "note": "reviewed"},
    )
    assert approval.status_code == 200
    assert approval.json()["approval_state"] == "approved"

    receipt = client.post(f"/incidents/{incident_id}/writeback")
    assert receipt.status_code == 200
    body = receipt.json()

    # Approved, but still no DataHub: the receipt must say exactly that.
    assert body["status"] == "skipped_fixture_mode"
    assert body["status"] != "applied"
    assert body["aspects_written"] == []
    assert body["source"] == "fixture"


def test_rejection_blocks_writeback(client: TestClient) -> None:
    incident_id = _diagnose(client, "critical-age-corruption")["incident_id"]

    client.post(
        f"/incidents/{incident_id}/approve",
        json={"approved": False, "approver": "test-operator"},
    )

    response = client.post(f"/incidents/{incident_id}/writeback")
    assert response.status_code == 403


def test_healthy_incident_needs_no_approval(client: TestClient) -> None:
    incident_id = _diagnose(client, "healthy-billing-branch")["incident_id"]

    body = client.post(
        f"/incidents/{incident_id}/approve", json={"approved": True}
    ).json()

    assert body["approval_state"] == "not_required"
    assert "nothing" in body["message"].lower()


# ---------------------------------------------------------------------------
# Audit and reset
# ---------------------------------------------------------------------------


def test_audit_log_records_the_approval_sequence(client: TestClient) -> None:
    incident_id = _diagnose(client, "critical-age-corruption")["incident_id"]
    client.post(f"/incidents/{incident_id}/writeback")  # blocked
    client.post(f"/incidents/{incident_id}/approve", json={"approved": True})
    client.post(f"/incidents/{incident_id}/writeback")  # allowed

    kinds = [e["kind"] for e in client.get(f"/audit?incident_id={incident_id}").json()]

    assert "diagnosis_completed" in kinds
    assert "approval_requested" in kinds
    assert "writeback_blocked" in kinds
    assert "approval_granted" in kinds


def test_demo_reset_clears_only_lineagemedic_state(client: TestClient) -> None:
    _diagnose(client, "critical-age-corruption")
    assert client.get("/incidents").json()

    body = client.post("/demo/reset").json()

    assert body["cleared_incidents"] >= 1
    assert "No DataHub metadata" in body["message"]
    assert client.get("/incidents").json() == []
    # The warehouse is untouched, so a new diagnosis still works.
    assert client.get("/health").json()["database_present"] is True


def test_root_banner_redacts_configuration(client: TestClient) -> None:
    body = client.get("/").json()

    assert body["service"] == "LineageMedic"
    assert body["config"]["datahub_token_configured"] is False
    assert "datahub_token" not in body["config"]


def test_openapi_schema_is_generated(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()

    assert schema["info"]["title"] == "LineageMedic API"
    assert "/diagnose" in schema["paths"]
    assert "/incidents/{incident_id}/writeback" in schema["paths"]
