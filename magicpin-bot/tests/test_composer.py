"""Tests for grounded LLM message composer, timeout handling, and deterministic fallback."""

import json
import pytest
from fastapi.testclient import TestClient

from app.composer import (
    CATEGORY_POLICIES,
    build_compact_context,
    compose_message,
    set_custom_llm_caller,
)
from app.main import app
from app.store import context_store, conversation_store
from app.decision_engine import suppression_engine

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_stores():
    context_store.clear()
    conversation_store.clear()
    suppression_engine.clear()
    set_custom_llm_caller(None)
    yield
    context_store.clear()
    conversation_store.clear()
    suppression_engine.clear()
    set_custom_llm_caller(None)


def test_category_policies():
    """Verify category policies exist and contain expected constraints."""
    assert "dentists" in CATEGORY_POLICIES
    assert "clinical" in CATEGORY_POLICIES["dentists"]
    assert "salons" in CATEGORY_POLICIES
    assert "visual" in CATEGORY_POLICIES["salons"]
    assert "restaurants" in CATEGORY_POLICIES
    assert "timely" in CATEGORY_POLICIES["restaurants"]
    assert "gyms" in CATEGORY_POLICIES
    assert "motivational" in CATEGORY_POLICIES["gyms"]
    assert "pharmacies" in CATEGORY_POLICIES
    assert "conservative" in CATEGORY_POLICIES["pharmacies"]


def test_build_compact_context():
    """Verify compact context only exposes allowed facts with strict boundaries."""
    merchant = {
        "merchant_id": "m_001",
        "category_slug": "dentists",
        "identity": {"name": "Dr. Meera Dental Clinic", "city": "Delhi", "owner_first_name": "Meera"},
        "performance": {"ctr": 0.025, "views": 1500},
        "offers": [{"title": "Scaling & Polishing @ ₹499", "status": "active"}],
    }
    category = {
        "slug": "dentists",
        "display_name": "Dentists & Clinics",
        "peer_stats": {"avg_ctr": 0.035},
    }
    trigger = {
        "id": "trg_001",
        "kind": "performance_drop",
        "urgency": 3,
        "payload": {"metric": "ctr", "delta_pct": -0.20},
    }

    ctx = build_compact_context(
        merchant=merchant,
        category=category,
        trigger=trigger,
        selected_signal="performance_gap",
    )

    assert "merchant" in ctx
    assert ctx["merchant"]["name"] == "Dr. Meera Dental Clinic"
    assert "category" in ctx
    assert ctx["category"]["voice_policy"] == CATEGORY_POLICIES["dentists"]
    assert "allowed_facts" in ctx
    assert any("Dr. Meera Dental Clinic" in f for f in ctx["allowed_facts"])
    assert any("2.5%" in f for f in ctx["allowed_facts"])
    assert "forbidden_claims" in ctx
    assert len(ctx["forbidden_claims"]) >= 3


def test_compose_message_success():
    """When LLM returns valid JSON matching evidence ledger, the LLM message is accepted."""
    compact_ctx = {
        "merchant": {
            "name": "Dr. Meera Clinic",
            "active_offers": ["Dental Cleaning @ ₹499"],
        },
        "trigger": {
            "details": {"delta_pct": 20},
        },
        "selected_signal": "test",
    }
    fallback_data = ("Fallback body", "template_fallback", [], "Fallback rationale")

    llm_payload = {
        "body": "Dr. Meera Clinic, your CTR dipped 20% this week. Want to highlight your Dental Cleaning @ ₹499 offer?",
        "cta": "open_ended",
        "rationale": "Directly grounded in recent CTR drop and active offer",
    }
    set_custom_llm_caller(lambda prompt, sys, timeout: json.dumps(llm_payload))

    body, tmpl, params, rationale = compose_message(compact_ctx, fallback_data)
    assert body == llm_payload["body"]
    assert tmpl == "llm_grounded_composer"
    assert rationale == llm_payload["rationale"]


def test_compose_message_timeout_fallback():
    """On LLM timeout, immediately falls back to deterministic template."""
    compact_ctx = {"merchant": {"name": "Dr. Meera Clinic"}}
    fallback_data = ("Deterministic fallback body", "template_perf_v1", ["param1"], "Fallback reason")

    def timeout_mock(prompt, sys, timeout):
        raise TimeoutError("LLM call exceeded 8.0s timeout")

    set_custom_llm_caller(timeout_mock)

    body, tmpl, params, rationale = compose_message(compact_ctx, fallback_data)
    assert body == "Deterministic fallback body"
    assert tmpl == "template_perf_v1"
    assert rationale == "Fallback reason"


def test_compose_message_malformed_json_fallback():
    """When LLM returns non-JSON or malformed output, falls back cleanly."""
    compact_ctx = {"merchant": {"name": "Dr. Meera Clinic"}}
    fallback_data = ("Deterministic fallback body", "template_perf_v1", [], "Fallback reason")

    set_custom_llm_caller(lambda prompt, sys, timeout: "I cannot generate a response right now.")

    body, tmpl, params, rationale = compose_message(compact_ctx, fallback_data)
    assert body == "Deterministic fallback body"
    assert tmpl == "template_perf_v1"


def test_compose_message_exception_fallback():
    """When LLM raises any unexpected exception, falls back immediately."""
    compact_ctx = {"merchant": {"name": "Dr. Meera Clinic"}}
    fallback_data = ("Deterministic fallback body", "template_perf_v1", [], "Fallback reason")

    def exception_mock(prompt, sys, timeout):
        raise ConnectionResetError("Remote host closed connection")

    set_custom_llm_caller(exception_mock)

    body, tmpl, params, rationale = compose_message(compact_ctx, fallback_data)
    assert body == "Deterministic fallback body"
    assert tmpl == "template_perf_v1"


# ---------------------------------------------------------------------------
# End-to-End Tests via POST /v1/tick
# ---------------------------------------------------------------------------

def test_tick_with_llm_success():
    """POST /v1/tick uses LLM-composed message when available."""
    context_store.set("category", "dentists", 1, {"slug": "dentists", "display_name": "Dentists"})
    context_store.set("merchant", "m_001", 1, {
        "merchant_id": "m_001",
        "category_slug": "dentists",
        "identity": {"name": "Meera Dental"},
        "performance": {"ctr": 0.02},
    })
    context_store.set("trigger", "trg_01", 1, {
        "id": "trg_01",
        "kind": "compliance_alert",
        "merchant_id": "m_001",
        "urgency": 4,
    })

    llm_payload = {
        "body": "Meera Dental, urgent compliance update for Dentists: new guidelines in effect. Want me to draft a summary?",
        "cta": "open_ended",
        "rationale": "High priority compliance",
    }
    set_custom_llm_caller(lambda prompt, sys, timeout: json.dumps(llm_payload))

    response = client.post("/v1/tick", json={
        "now": "2026-04-26T10:00:00Z",
        "available_triggers": ["trg_01"],
    })
    assert response.status_code == 200
    actions = response.json().get("actions", [])
    assert len(actions) == 1
    assert actions[0]["body"] == llm_payload["body"]
    assert actions[0]["template_name"] == "llm_grounded_composer"


def test_tick_with_llm_timeout_fallback():
    """POST /v1/tick falls back to deterministic template when LLM times out."""
    context_store.set("category", "dentists", 1, {"slug": "dentists", "display_name": "Dentists", "peer_stats": {"avg_ctr": 0.03}})
    context_store.set("merchant", "m_001", 1, {
        "merchant_id": "m_001",
        "category_slug": "dentists",
        "identity": {"name": "Meera Dental"},
        "performance": {"ctr": 0.015},
    })
    context_store.set("trigger", "trg_01", 1, {
        "id": "trg_01",
        "kind": "performance_drop",
        "merchant_id": "m_001",
        "urgency": 3,
    })

    def timeout_mock(prompt, sys, timeout):
        raise TimeoutError("LLM call timed out")

    set_custom_llm_caller(timeout_mock)

    response = client.post("/v1/tick", json={
        "now": "2026-04-26T10:00:00Z",
        "available_triggers": ["trg_01"],
    })
    assert response.status_code == 200
    actions = response.json().get("actions", [])
    assert len(actions) == 1
    assert "Meera Dental" in actions[0]["body"]
    assert "CTR is 1.5% vs 3.0%" in actions[0]["body"]
    assert actions[0]["template_name"] == "template_performance_drop_v1"


def test_tick_with_llm_malformed_json_fallback():
    """POST /v1/tick falls back to deterministic template when LLM returns invalid JSON."""
    context_store.set("category", "salons", 1, {"slug": "salons", "display_name": "Salons"})
    context_store.set("merchant", "m_002", 1, {
        "merchant_id": "m_002",
        "category_slug": "salons",
        "identity": {"name": "Studio 11 Salon"},
    })
    context_store.set("trigger", "trg_02", 1, {
        "id": "trg_02",
        "kind": "curious_ask",
        "merchant_id": "m_002",
        "urgency": 1,
    })

    set_custom_llm_caller(lambda p, s, t: "Here is your response: {not valid json")

    response = client.post("/v1/tick", json={
        "now": "2026-04-26T10:00:00Z",
        "available_triggers": ["trg_02"],
    })
    assert response.status_code == 200
    actions = response.json().get("actions", [])
    assert len(actions) == 1
    assert "Studio 11 Salon" in actions[0]["body"]
    assert actions[0]["template_name"] == "template_curious_ask_v1"


def test_tick_with_llm_unavailable_fallback():
    """POST /v1/tick falls back to deterministic template when LLM service is unavailable."""
    context_store.set("category", "dentists", 1, {"slug": "dentists", "display_name": "Dentists"})
    context_store.set("merchant", "m_003", 1, {
        "merchant_id": "m_003",
        "category_slug": "dentists",
        "identity": {"name": "Apex Clinic"},
    })
    context_store.set("trigger", "trg_03", 1, {
        "id": "trg_03",
        "kind": "compliance_alert",
        "merchant_id": "m_003",
        "urgency": 4,
    })

    def error_mock(p, s, t):
        raise ConnectionError("503 Service Unavailable")

    set_custom_llm_caller(error_mock)

    response = client.post("/v1/tick", json={
        "now": "2026-04-26T10:00:00Z",
        "available_triggers": ["trg_03"],
    })
    assert response.status_code == 200
    actions = response.json().get("actions", [])
    assert len(actions) == 1
    assert "Apex Clinic" in actions[0]["body"]
    assert actions[0]["template_name"] == "template_compliance_alert_v1"
