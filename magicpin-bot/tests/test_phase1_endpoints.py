"""Verification that all Phase 1 endpoints continue passing without regression."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.store import context_store, conversation_store

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_stores():
    context_store.clear()
    conversation_store.clear()
    yield
    context_store.clear()
    conversation_store.clear()


def test_healthz_endpoint():
    """GET /v1/healthz returns 200, uptime, and loaded context counts."""
    # Seed one context
    context_store.set("category", "c1", 1, {"name": "test"})
    context_store.set("merchant", "m1", 1, {"name": "test"})

    response = client.get("/v1/healthz")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert isinstance(data["uptime_seconds"], int)
    assert data["contexts_loaded"]["category"] == 1
    assert data["contexts_loaded"]["merchant"] == 1
    assert data["contexts_loaded"]["customer"] == 0
    assert data["contexts_loaded"]["trigger"] == 0


def test_metadata_endpoint():
    """GET /v1/metadata returns 200 with required team metadata."""
    response = client.get("/v1/metadata")
    assert response.status_code == 200
    data = response.json()
    assert "team_name" in data
    assert isinstance(data["team_members"], list)
    assert "model" in data
    assert "approach" in data
    assert "contact_email" in data
    assert data["version"] == "1.0.0"


def test_tick_endpoint():
    """POST /v1/tick returns 200 and actions list stub."""
    response = client.post("/v1/tick", json={
        "now": "2026-04-26T10:30:00Z",
        "available_triggers": ["trg_001"],
    })
    assert response.status_code == 200
    assert response.json() == {"actions": []}


def test_reply_endpoint():
    """POST /v1/reply returns 200 and wait stub."""
    response = client.post("/v1/reply", json={
        "conversation_id": "conv_001",
        "message": "Interested in promo details",
    })
    assert response.status_code == 200
    data = response.json()
    assert data["action"] in ("send", "wait", "end")


def test_teardown_endpoint():
    """POST /v1/teardown wipes state and returns 200."""
    context_store.set("category", "c1", 1, {"name": "test"})
    assert context_store.count() == 1

    response = client.post("/v1/teardown")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert context_store.count() == 0


def test_malformed_json_returns_400():
    """Sending malformed JSON returns 400 Bad Request."""
    response = client.post(
        "/v1/context",
        content="{malformed_json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "malformed_json"
