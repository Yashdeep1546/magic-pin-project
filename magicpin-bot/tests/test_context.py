"""Tests for /v1/context endpoint and version-gating logic."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.store import context_store

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_store():
    """Ensure context_store is cleared before and after each test."""
    context_store.clear()
    yield
    context_store.clear()


def test_new_context():
    """Posting a new context entity stores it and returns 200 with an acknowledgment."""
    payload = {
        "scope": "category",
        "context_id": "dentists",
        "version": 1,
        "payload": {"slug": "dentists", "display_name": "Dentists & Dental Clinics"},
        "delivered_at": "2026-04-26T10:00:00Z",
    }
    response = client.post("/v1/context", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["accepted"] is True
    assert "ack_id" in data and data["ack_id"].startswith("ack_")
    assert "stored_at" in data

    stored = context_store.get("category", "dentists")
    assert stored is not None
    assert stored["version"] == 1
    assert stored["payload"] == {"slug": "dentists", "display_name": "Dentists & Dental Clinics"}


def test_same_version():
    """Posting the same version again is an idempotent no-op and returns 200."""
    initial_payload = {
        "scope": "merchant",
        "context_id": "m_001_drmeera",
        "version": 1,
        "payload": {"name": "Dr. Meera Clinic", "category": "dentists"},
    }
    r1 = client.post("/v1/context", json=initial_payload)
    assert r1.status_code == 200

    r2 = client.post("/v1/context", json=initial_payload)
    assert r2.status_code == 200
    data2 = r2.json()
    assert data2["accepted"] is True
    assert data2["ack_id"].startswith("ack_")
    assert "stored_at" in data2

    stored = context_store.get("merchant", "m_001_drmeera")
    assert stored["version"] == 1


def test_older_version():
    """Posting an older version than currently stored returns 409 with stale_version error."""
    # First, store version 2
    r1 = client.post("/v1/context", json={
        "scope": "customer",
        "context_id": "c_001_priya",
        "version": 2,
        "payload": {"name": "Priya", "tier": "gold"},
    })
    assert r1.status_code == 200

    # Attempt to post version 1 (older than current 2)
    r2 = client.post("/v1/context", json={
        "scope": "customer",
        "context_id": "c_001_priya",
        "version": 1,
        "payload": {"name": "Priya", "tier": "silver"},
    })
    assert r2.status_code == 409
    data = r2.json()
    assert data["accepted"] is False
    assert data["reason"] == "stale_version"
    assert data["current_version"] == 2

    # Ensure stored data was NOT overwritten
    stored = context_store.get("customer", "c_001_priya")
    assert stored["version"] == 2
    assert stored["payload"]["tier"] == "gold"


def test_newer_version():
    """Posting a newer version replaces prior context atomically and returns 200."""
    # Store initial version 1
    r1 = client.post("/v1/context", json={
        "scope": "trigger",
        "context_id": "trg_digest",
        "version": 1,
        "payload": {"headline": "Initial research note"},
    })
    assert r1.status_code == 200

    # Post version 2
    r2 = client.post("/v1/context", json={
        "scope": "trigger",
        "context_id": "trg_digest",
        "version": 2,
        "payload": {"headline": "Updated research digest with citations"},
    })
    assert r2.status_code == 200
    data = r2.json()
    assert data["accepted"] is True

    stored = context_store.get("trigger", "trg_digest")
    assert stored["version"] == 2
    assert stored["payload"]["headline"] == "Updated research digest with citations"


def test_missing_scope():
    """Missing scope returns 400 with a clear error message."""
    payload = {
        "context_id": "dentists",
        "version": 1,
        "payload": {"slug": "dentists"},
    }
    response = client.post("/v1/context", json=payload)
    assert response.status_code == 400
    data = response.json()
    assert data["accepted"] is False
    assert data["reason"] in ("missing_scope", "invalid_scope")
    assert "scope" in data["details"].lower()


def test_invalid_scope():
    """Invalid scope returns 400 with an error listing valid scopes."""
    payload = {
        "scope": "unsupported_scope",
        "context_id": "dentists",
        "version": 1,
        "payload": {"slug": "dentists"},
    }
    response = client.post("/v1/context", json=payload)
    assert response.status_code == 400
    data = response.json()
    assert data["accepted"] is False
    assert data["reason"] == "invalid_scope"
    assert "scope" in data["details"].lower()


def test_missing_context_id():
    """Missing or empty context_id returns 400."""
    payload = {
        "scope": "category",
        "context_id": "",
        "version": 1,
        "payload": {"slug": "dentists"},
    }
    response = client.post("/v1/context", json=payload)
    assert response.status_code == 400
    data = response.json()
    assert data["accepted"] is False
    assert data["reason"] == "missing_context_id"
    assert "context_id" in data["details"].lower()


def test_negative_version():
    """Negative or zero version returns 400."""
    payload = {
        "scope": "category",
        "context_id": "dentists",
        "version": -1,
        "payload": {"slug": "dentists"},
    }
    response = client.post("/v1/context", json=payload)
    assert response.status_code == 400
    data = response.json()
    assert data["accepted"] is False
    assert data["reason"] == "invalid_version"
    assert "version" in data["details"].lower()


def test_empty_payload():
    """Empty payload dictionary returns 400."""
    payload = {
        "scope": "category",
        "context_id": "dentists",
        "version": 1,
        "payload": {},
    }
    response = client.post("/v1/context", json=payload)
    assert response.status_code == 400
    data = response.json()
    assert data["accepted"] is False
    assert data["reason"] == "empty_payload"
    assert "payload" in data["details"].lower()


def test_duplicate_request():
    """Duplicate requests with identical payload return 200 and store exactly one entry."""
    payload = {
        "scope": "trigger",
        "context_id": "trg_2026_04_26_digest",
        "version": 1,
        "payload": {"topic": "dental_recall"},
    }
    r1 = client.post("/v1/context", json=payload)
    r2 = client.post("/v1/context", json=payload)

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["accepted"] is True
    assert r2.json()["accepted"] is True
    assert context_store.count() == 1
