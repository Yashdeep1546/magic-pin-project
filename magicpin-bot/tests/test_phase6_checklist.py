"""Phase 6 Exit Checklist Verification Script.

Executes all 8 checklist items:
1. /v1/healthz: 200 consistently
2. /v1/metadata: Valid metadata
3. /v1/context: Newer version replaces older
4. /v1/context: Same version is idempotent
5. /v1/tick: Correct action/no-action
6. /v1/reply: Correct send/wait/end transitions
7. LLM failure: Deterministic fallback works
8. Invalid LLM output: Validator rejects/falls back
"""

import json
import pytest
from fastapi.testclient import TestClient

from app.composer import set_custom_llm_caller
from app.decision_engine import suppression_engine
from app.main import app
from app.store import context_store, conversation_store

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_state():
    context_store.clear()
    conversation_store.clear()
    suppression_engine.clear()
    set_custom_llm_caller(None)
    yield
    context_store.clear()
    conversation_store.clear()
    suppression_engine.clear()
    set_custom_llm_caller(None)


def test_check_1_healthz_consistently_200():
    """Check 1: /v1/healthz -> 200 consistently."""
    for _ in range(5):
        resp = client.get("/v1/healthz")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert isinstance(data["uptime_seconds"], int)
        assert data["uptime_seconds"] >= 0
        assert "contexts_loaded" in data
        assert isinstance(data["contexts_loaded"], dict)


def test_check_2_metadata_valid():
    """Check 2: /v1/metadata -> Valid metadata."""
    resp = client.get("/v1/metadata")
    assert resp.status_code == 200
    data = resp.json()
    required_fields = ["team_name", "team_members", "model", "approach", "contact_email", "version"]
    for field in required_fields:
        assert field in data
        assert data[field] is not None
    assert len(data["team_name"]) > 0
    assert isinstance(data["team_members"], list) and len(data["team_members"]) > 0
    assert len(data["version"]) > 0


def test_check_3_context_newer_version_replaces_older():
    """Check 3: /v1/context -> Newer version replaces older."""
    # Store v1
    resp1 = client.post(
        "/v1/context",
        json={
            "scope": "merchant",
            "context_id": "m_test_ver",
            "version": 1,
            "payload": {"name": "Old Clinic Name", "category_slug": "dentists"},
        },
    )
    assert resp1.status_code == 200
    stored1 = context_store.get("merchant", "m_test_ver")
    assert stored1["version"] == 1
    assert stored1["payload"]["name"] == "Old Clinic Name"

    # Store v2 (newer replaces older)
    resp2 = client.post(
        "/v1/context",
        json={
            "scope": "merchant",
            "context_id": "m_test_ver",
            "version": 2,
            "payload": {"name": "New Clinic Name", "category_slug": "dentists"},
        },
    )
    assert resp2.status_code == 200
    stored2 = context_store.get("merchant", "m_test_ver")
    assert stored2["version"] == 2
    assert stored2["payload"]["name"] == "New Clinic Name"

    # Stale version (v1 < v2) rejected with 409
    resp_stale = client.post(
        "/v1/context",
        json={
            "scope": "merchant",
            "context_id": "m_test_ver",
            "version": 1,
            "payload": {"name": "Stale Attempt", "category_slug": "dentists"},
        },
    )
    assert resp_stale.status_code == 409
    assert resp_stale.json()["error"] == "stale_version"


def test_check_4_context_same_version_idempotent():
    """Check 4: /v1/context -> Same version is idempotent."""
    # Initial store v1
    resp1 = client.post(
        "/v1/context",
        json={
            "scope": "category",
            "context_id": "salons",
            "version": 1,
            "payload": {"slug": "salons", "display_name": "Salons & Spas"},
        },
    )
    assert resp1.status_code == 200

    # Repeat exact same version v1
    resp2 = client.post(
        "/v1/context",
        json={
            "scope": "category",
            "context_id": "salons",
            "version": 1,
            "payload": {"slug": "salons", "display_name": "Salons & Spas"},
        },
    )
    assert resp2.status_code == 200
    assert resp2.json()["accepted"] is True
    # Context remains intact
    stored = context_store.get("category", "salons")
    assert stored["version"] == 1


def test_check_5_tick_correct_action_and_no_action():
    """Check 5: /v1/tick -> Correct action and no-action."""
    # Setup contexts
    context_store.set("category", "dentists", 1, {"slug": "dentists", "display_name": "Dentists", "peer_stats": {"avg_ctr": 0.030}})
    context_store.set(
        "merchant",
        "m_01",
        1,
        {
            "merchant_id": "m_01",
            "category_slug": "dentists",
            "identity": {"name": "Apex Dental"},
            "performance": {"ctr": 0.015},
            "offers": [{"title": "Scaling @ ₹399", "status": "active"}],
        },
    )
    context_store.set("trigger", "trg_01", 1, {"id": "trg_01", "kind": "performance_drop", "merchant_id": "m_01", "urgency": 3})

    # Case A: Action generated for valid trigger
    resp_act = client.post("/v1/tick", json={"now": "2026-04-26T10:00:00Z", "available_triggers": ["trg_01"]})
    assert resp_act.status_code == 200
    actions = resp_act.json().get("actions", [])
    assert len(actions) == 1
    assert actions[0]["merchant_id"] == "m_01"
    assert "Apex Dental" in actions[0]["body"]

    # Case B: No action when no triggers available
    resp_no_act = client.post("/v1/tick", json={"now": "2026-04-26T10:00:00Z", "available_triggers": []})
    assert resp_no_act.status_code == 200
    assert resp_no_act.json().get("actions") == []

    # Case C: No action when trigger context is missing
    resp_miss = client.post("/v1/tick", json={"now": "2026-04-26T10:00:00Z", "available_triggers": ["non_existent_trg"]})
    assert resp_miss.status_code == 200
    assert resp_miss.json().get("actions") == []


def test_check_6_reply_correct_state_transitions():
    """Check 6: /v1/reply -> Correct send/wait/end transitions."""
    conv_id = "conv_state_test"
    conversation_store.create_or_update(
        conversation_id=conv_id,
        merchant_id="m_01",
        state="waiting_for_reply",
    )

    # 1. Delay intent -> WAIT
    resp_delay = client.post(
        "/v1/reply",
        json={
            "conversation_id": conv_id,
            "merchant_id": "m_01",
            "from_role": "merchant",
            "message": "Call me tomorrow, I am busy right now.",
            "turn_number": 2,
        },
    )
    assert resp_delay.status_code == 200
    assert resp_delay.json()["action"] == "wait"
    assert conversation_store.get(conv_id)["state"] == "WAIT"

    # 2. Hostile / Negative intent -> END
    resp_end = client.post(
        "/v1/reply",
        json={
            "conversation_id": conv_id,
            "merchant_id": "m_01",
            "from_role": "merchant",
            "message": "No, not interested, stop messaging me.",
            "turn_number": 3,
        },
    )
    assert resp_end.status_code == 200
    assert resp_end.json()["action"] == "end"
    assert conversation_store.get(conv_id)["state"] == "END"

    # 3. Positive intent -> SEND (action transition)
    conv_id2 = "conv_pos_test"
    conversation_store.create_or_update(
        conversation_id=conv_id2,
        merchant_id="m_01",
        state="waiting_for_reply",
    )
    resp_send = client.post(
        "/v1/reply",
        json={
            "conversation_id": conv_id2,
            "merchant_id": "m_01",
            "from_role": "merchant",
            "message": "Sure, let's do it! Go ahead and launch it.",
            "turn_number": 2,
        },
    )
    assert resp_send.status_code == 200
    assert resp_send.json()["action"] == "send"
    assert conversation_store.get(conv_id2)["state"] == "SEND"


def test_check_7_llm_failure_deterministic_fallback():
    """Check 7: LLM failure -> Deterministic fallback works."""
    context_store.set("category", "dentists", 1, {"slug": "dentists", "display_name": "Dentists", "peer_stats": {"avg_ctr": 0.030}})
    context_store.set(
        "merchant",
        "m_fb",
        1,
        {
            "merchant_id": "m_fb",
            "category_slug": "dentists",
            "identity": {"name": "Smile Clinic"},
            "performance": {"ctr": 0.018},
            "offers": [{"title": "Checkup @ ₹199", "status": "active"}],
        },
    )
    context_store.set("trigger", "trg_fb", 1, {"id": "trg_fb", "kind": "performance_drop", "merchant_id": "m_fb", "urgency": 3})

    # Case A: LLM Timeout triggers fallback
    def timeout_mock(p, s, t):
        raise TimeoutError("8s deadline exceeded")

    set_custom_llm_caller(timeout_mock)
    resp1 = client.post("/v1/tick", json={"now": "2026-04-26T10:00:00Z", "available_triggers": ["trg_fb"]})
    assert resp1.status_code == 200
    actions1 = resp1.json().get("actions", [])
    assert len(actions1) == 1
    assert actions1[0]["template_name"] == "template_performance_drop_v1"
    assert "Smile Clinic" in actions1[0]["body"]

    # Clear suppression to test exception mock
    suppression_engine.clear()

    # Case B: LLM Exception / 503 triggers fallback
    def error_mock(p, s, t):
        raise ConnectionError("503 Service Unavailable")

    set_custom_llm_caller(error_mock)
    resp2 = client.post("/v1/tick", json={"now": "2026-04-26T10:00:00Z", "available_triggers": ["trg_fb"]})
    assert resp2.status_code == 200
    actions2 = resp2.json().get("actions", [])
    assert len(actions2) == 1
    assert actions2[0]["template_name"] == "template_performance_drop_v1"


def test_check_8_invalid_llm_output_validator_rejects_and_falls_back():
    """Check 8: Invalid LLM output -> Validator rejects/falls back."""
    context_store.set("category", "dentists", 1, {"slug": "dentists", "display_name": "Dentists", "peer_stats": {"avg_ctr": 0.030}})
    context_store.set(
        "merchant",
        "m_val",
        1,
        {
            "merchant_id": "m_val",
            "category_slug": "dentists",
            "identity": {"name": "Dr. Vera Dental"},
            "performance": {"ctr": 0.021},
            "offers": [{"title": "Dental Cleaning @ ₹299", "status": "active"}],
        },
    )
    context_store.set("trigger", "trg_val", 1, {"id": "trg_val", "kind": "performance_drop", "merchant_id": "m_val", "urgency": 3})

    # Case A: Wrong CTR hallucinated (5.8% instead of 2.1%)
    set_custom_llm_caller(
        lambda p, s, t: json.dumps({
            "body": "Dr. Vera Dental, your CTR is 5.8% vs 3.0%. Want to promote Dental Cleaning @ ₹299?",
            "cta": "open_ended",
        })
    )
    resp_ctr = client.post("/v1/tick", json={"now": "2026-04-26T10:00:00Z", "available_triggers": ["trg_val"]})
    assert resp_ctr.status_code == 200
    act_ctr = resp_ctr.json().get("actions", [])
    assert len(act_ctr) == 1
    assert act_ctr[0]["template_name"] == "template_performance_drop_v1"
    assert "2.1%" in act_ctr[0]["body"]

    suppression_engine.clear()

    # Case B: Wrong price hallucinated (₹99 instead of ₹299)
    set_custom_llm_caller(
        lambda p, s, t: json.dumps({
            "body": "Dr. Vera Dental, your CTR is 2.1%. Want to promote Dental Cleaning @ ₹99?",
            "cta": "open_ended",
        })
    )
    resp_price = client.post("/v1/tick", json={"now": "2026-04-26T10:00:00Z", "available_triggers": ["trg_val"]})
    assert resp_price.status_code == 200
    act_price = resp_price.json().get("actions", [])
    assert len(act_price) == 1
    assert act_price[0]["template_name"] == "template_performance_drop_v1"
    assert "₹299" in act_price[0]["body"]

    suppression_engine.clear()

    # Case C: Multiple CTAs (2 questions)
    set_custom_llm_caller(
        lambda p, s, t: json.dumps({
            "body": "Dr. Vera Dental, your CTR is 2.1%. Want to promote Dental Cleaning @ ₹299? Can we call today?",
            "cta": "open_ended",
        })
    )
    resp_cta = client.post("/v1/tick", json={"now": "2026-04-26T10:00:00Z", "available_triggers": ["trg_val"]})
    assert resp_cta.status_code == 200
    act_cta = resp_cta.json().get("actions", [])
    assert len(act_cta) == 1
    assert act_cta[0]["template_name"] == "template_performance_drop_v1"
