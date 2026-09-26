"""Tests for deterministic decision engine and /v1/tick endpoint."""

import pytest
from fastapi.testclient import TestClient

from app.decision_engine import (
    TRIGGER_PRIORITY,
    check_suppressed,
    mark_suppressed,
    resolve_category,
    resolve_customer,
    resolve_merchant,
    score_trigger,
    select_strongest_signal,
    suppression_engine,
)
from app.main import app
from app.store import context_store, conversation_store

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_state():
    """Reset all in-memory state before and after each test."""
    context_store.clear()
    conversation_store.clear()
    suppression_engine.clear()
    yield
    context_store.clear()
    conversation_store.clear()
    suppression_engine.clear()


# Helper to seed sample context
def seed_merchant_and_category(
    mid: str = "m_001_drmeera",
    cat_slug: str = "dentists",
    name: str = "Dr. Meera Dental",
):
    context_store.set(
        "category",
        cat_slug,
        1,
        {
            "slug": cat_slug,
            "display_name": "Dentists",
            "peer_stats": {"avg_ctr": 0.030},
        },
    )
    context_store.set(
        "merchant",
        mid,
        1,
        {
            "merchant_id": mid,
            "category_slug": cat_slug,
            "identity": {"name": name, "owner_first_name": "Meera"},
            "subscription": {"status": "active", "plan": "Pro"},
            "performance": {"ctr": 0.015, "views": 1000},
            "offers": [{"id": "off_1", "title": "Free Dental Checkup", "status": "active"}],
        },
    )


# ---------------------------------------------------------------------------
# Tests for Context Resolvers & Decision Helpers
# ---------------------------------------------------------------------------

def test_resolvers():
    seed_merchant_and_category("m_res_1", "dentists")
    context_store.set("customer", "c_res_1", 1, {"customer_id": "c_res_1", "identity": {"name": "Priya"}})

    m = resolve_merchant(context_store, "m_res_1")
    assert m is not None
    assert m["merchant_id"] == "m_res_1"

    c = resolve_category(context_store, "dentists")
    assert c is not None
    assert c["slug"] == "dentists"

    cust = resolve_customer(context_store, "c_res_1")
    assert cust is not None
    assert cust["identity"]["name"] == "Priya"

    assert resolve_merchant(context_store, "non_existent") is None
    assert resolve_category(context_store, "non_existent") is None
    assert resolve_customer(context_store, "non_existent") is None


def test_trigger_priorities():
    assert TRIGGER_PRIORITY["compliance_alert"] == 100
    assert TRIGGER_PRIORITY["recall_due"] == 95
    assert TRIGGER_PRIORITY["performance_drop"] == 90
    assert TRIGGER_PRIORITY["customer_winback"] == 80
    assert TRIGGER_PRIORITY["research_digest"] == 70
    assert TRIGGER_PRIORITY["festival"] == 60
    assert TRIGGER_PRIORITY["curious_ask"] == 50
    assert TRIGGER_PRIORITY["seasonal"] == 40


def test_select_strongest_signal_unit():
    # 1. Zero triggers
    assert select_strongest_signal([]) is None

    # 2. Expired trigger
    exp_trg = {
        "id": "t_exp",
        "kind": "festival",
        "expires_at": "2026-01-01T00:00:00Z",
        "suppression_key": "k_exp",
    }
    assert select_strongest_signal([exp_trg], now="2026-04-26T10:00:00Z") is None

    # 3. Suppressed trigger
    mark_suppressed("k_sup")
    sup_trg = {"id": "t_sup", "kind": "festival", "suppression_key": "k_sup"}
    assert select_strongest_signal([sup_trg]) is None

    # 4. Competing signals: compliance_alert vs festival
    trg_comp = {"id": "t_comp", "kind": "compliance_alert", "urgency": 4}
    trg_fest = {"id": "t_fest", "kind": "festival", "urgency": 1}
    best = select_strongest_signal([trg_fest, trg_comp])
    assert best is not None
    assert best["id"] == "t_comp"


# ---------------------------------------------------------------------------
# Required Tests for /v1/tick
# ---------------------------------------------------------------------------

def test_one_valid_trigger():
    """One valid trigger returns 1 action rendered with deterministic fallback and marks suppression."""
    seed_merchant_and_category("m_001", "dentists")
    trigger_payload = {
        "id": "trg_001",
        "kind": "performance_drop",
        "merchant_id": "m_001",
        "urgency": 3,
        "suppression_key": "perf_drop:m_001:2026-W17",
        "expires_at": "2026-05-10T00:00:00Z",
    }
    context_store.set("trigger", "trg_001", 1, trigger_payload)

    response = client.post("/v1/tick", json={
        "now": "2026-04-26T10:00:00Z",
        "available_triggers": ["trg_001"],
    })
    assert response.status_code == 200
    data = response.json()
    actions = data.get("actions", [])
    assert len(actions) == 1

    act = actions[0]
    assert act["merchant_id"] == "m_001"
    assert act["trigger_id"] == "trg_001"
    assert act["send_as"] == "vera"
    assert act["cta"] == "open_ended"
    assert act["template_name"] == "template_performance_drop_v1"
    assert "Dr. Meera Dental" in act["body"]
    assert "CTR is 1.5% vs 3.0%" in act["body"]
    assert "Free Dental Checkup" in act["body"]

    # Verify suppression key is now marked active
    assert check_suppressed("perf_drop:m_001:2026-W17") is True


def test_multiple_triggers():
    """Multiple available triggers are resolved, scored, and ranked."""
    seed_merchant_and_category("m_001", "dentists", "Dr. Meera Clinic")
    seed_merchant_and_category("m_002", "dentists", "Bharat Dental")

    context_store.set("trigger", "trg_low", 1, {
        "id": "trg_low",
        "kind": "curious_ask",
        "merchant_id": "m_001",
        "urgency": 1,
        "suppression_key": "curious:m_001",
    })
    context_store.set("trigger", "trg_high", 1, {
        "id": "trg_high",
        "kind": "compliance_alert",
        "merchant_id": "m_002",
        "urgency": 4,
        "suppression_key": "comp:m_002",
    })

    response = client.post("/v1/tick", json={
        "now": "2026-04-26T10:00:00Z",
        "available_triggers": ["trg_low", "trg_high"],
    })
    assert response.status_code == 200
    actions = response.json()["actions"]
    assert len(actions) == 2
    # trg_high has priority 100 vs 50 for trg_low
    assert actions[0]["trigger_id"] == "trg_high"
    assert actions[1]["trigger_id"] == "trg_low"


def test_zero_triggers():
    """Zero triggers in request returns empty actions list."""
    response = client.post("/v1/tick", json={
        "now": "2026-04-26T10:00:00Z",
        "available_triggers": [],
    })
    assert response.status_code == 200
    assert response.json() == {"actions": []}


def test_expired_trigger():
    """Expired triggers are skipped and not acted upon."""
    seed_merchant_and_category("m_001", "dentists")
    context_store.set("trigger", "trg_exp", 1, {
        "id": "trg_exp",
        "kind": "festival",
        "merchant_id": "m_001",
        "urgency": 2,
        "suppression_key": "fest:m_001",
        "expires_at": "2026-04-01T00:00:00Z",
    })

    response = client.post("/v1/tick", json={
        "now": "2026-04-26T10:00:00Z",
        "available_triggers": ["trg_exp"],
    })
    assert response.status_code == 200
    assert response.json()["actions"] == []


def test_suppressed_trigger():
    """Triggers with active suppression keys are ignored."""
    seed_merchant_and_category("m_001", "dentists")
    sup_key = "research:dentists:2026-W17"
    mark_suppressed(sup_key)

    context_store.set("trigger", "trg_sup", 1, {
        "id": "trg_sup",
        "kind": "research_digest",
        "merchant_id": "m_001",
        "urgency": 3,
        "suppression_key": sup_key,
    })

    response = client.post("/v1/tick", json={
        "now": "2026-04-26T10:00:00Z",
        "available_triggers": ["trg_sup"],
    })
    assert response.status_code == 200
    assert response.json()["actions"] == []


def test_duplicate_trigger():
    """Duplicate trigger IDs in available_triggers are deduplicated."""
    seed_merchant_and_category("m_001", "dentists")
    context_store.set("trigger", "trg_dup", 1, {
        "id": "trg_dup",
        "kind": "curious_ask",
        "merchant_id": "m_001",
        "urgency": 1,
        "suppression_key": "ask:m_001",
    })

    response = client.post("/v1/tick", json={
        "now": "2026-04-26T10:00:00Z",
        "available_triggers": ["trg_dup", "trg_dup", "trg_dup"],
    })
    assert response.status_code == 200
    assert len(response.json()["actions"]) == 1


def test_missing_trigger_context():
    """Triggers referencing missing merchants or missing from store are skipped."""
    # 1. Trigger not in ContextStore at all
    response1 = client.post("/v1/tick", json={
        "now": "2026-04-26T10:00:00Z",
        "available_triggers": ["trg_unloaded"],
    })
    assert response1.status_code == 200
    assert response1.json()["actions"] == []

    # 2. Trigger in ContextStore but references unseeded merchant
    context_store.set("trigger", "trg_ghost_merchant", 1, {
        "id": "trg_ghost_merchant",
        "kind": "performance_drop",
        "merchant_id": "m_nonexistent",
        "urgency": 2,
    })
    response2 = client.post("/v1/tick", json={
        "now": "2026-04-26T10:00:00Z",
        "available_triggers": ["trg_ghost_merchant"],
    })
    assert response2.status_code == 200
    assert response2.json()["actions"] == []


def test_more_than_20_triggers():
    """More than 20 available triggers are capped at exactly 20 actions."""
    # Seed 25 merchants with 1 trigger each
    trg_ids = []
    for i in range(1, 26):
        mid = f"m_cap_{i:02d}"
        tid = f"trg_cap_{i:02d}"
        seed_merchant_and_category(mid, "dentists", f"Clinic {i}")
        context_store.set("trigger", tid, 1, {
            "id": tid,
            "kind": "compliance_alert",
            "merchant_id": mid,
            "urgency": 3,
            "suppression_key": f"sup_cap_{i}",
        })
        trg_ids.append(tid)

    response = client.post("/v1/tick", json={
        "now": "2026-04-26T10:00:00Z",
        "available_triggers": trg_ids,
    })
    assert response.status_code == 200
    actions = response.json()["actions"]
    assert len(actions) == 20


def test_multiple_merchants():
    """Triggers across different merchants generate separate actions for each."""
    seed_merchant_and_category("m_alpha", "dentists", "Alpha Dental")
    seed_merchant_and_category("m_beta", "salons", "Beta Salon")

    context_store.set("trigger", "trg_alpha", 1, {
        "id": "trg_alpha",
        "kind": "recall_due",
        "merchant_id": "m_alpha",
        "urgency": 3,
        "suppression_key": "sup_alpha",
    })
    context_store.set("trigger", "trg_beta", 1, {
        "id": "trg_beta",
        "kind": "festival",
        "merchant_id": "m_beta",
        "urgency": 2,
        "suppression_key": "sup_beta",
    })

    response = client.post("/v1/tick", json={
        "now": "2026-04-26T10:00:00Z",
        "available_triggers": ["trg_alpha", "trg_beta"],
    })
    assert response.status_code == 200
    actions = response.json()["actions"]
    assert len(actions) == 2
    merchant_ids = {a["merchant_id"] for a in actions}
    assert merchant_ids == {"m_alpha", "m_beta"}


def test_same_merchant_with_multiple_triggers():
    """When a merchant has multiple active triggers, the single strongest signal is selected."""
    seed_merchant_and_category("m_solo", "dentists", "Solo Clinic")

    # Priority 100
    context_store.set("trigger", "trg_comp", 1, {
        "id": "trg_comp",
        "kind": "compliance_alert",
        "merchant_id": "m_solo",
        "urgency": 4,
        "suppression_key": "sup_comp",
    })
    # Priority 70
    context_store.set("trigger", "trg_res", 1, {
        "id": "trg_res",
        "kind": "research_digest",
        "merchant_id": "m_solo",
        "urgency": 2,
        "suppression_key": "sup_res",
    })
    # Priority 50
    context_store.set("trigger", "trg_cur", 1, {
        "id": "trg_cur",
        "kind": "curious_ask",
        "merchant_id": "m_solo",
        "urgency": 1,
        "suppression_key": "sup_cur",
    })

    response = client.post("/v1/tick", json={
        "now": "2026-04-26T10:00:00Z",
        "available_triggers": ["trg_res", "trg_comp", "trg_cur"],
    })
    assert response.status_code == 200
    actions = response.json()["actions"]
    # Only ONE action for m_solo, picking the highest-priority compliance_alert
    assert len(actions) == 1
    assert actions[0]["merchant_id"] == "m_solo"
    assert actions[0]["trigger_id"] == "trg_comp"
    assert actions[0]["template_name"] == "template_compliance_alert_v1"


def test_empty_actions_case():
    """When triggers are invalid or not qualified, tick cleanly returns empty actions."""
    response = client.post("/v1/tick", json={
        "now": "2026-04-26T10:00:00Z",
        "available_triggers": ["non_existent_1", "non_existent_2"],
    })
    assert response.status_code == 200
    assert response.json() == {"actions": []}
