"""Tests for deterministic decision engine and /v1/tick endpoint."""

import pytest
from fastapi.testclient import TestClient

from app.decision_engine import (
    TRIGGER_PRIORITY,
    check_suppressed,
    get_suppression_record,
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


def test_data_driven_urgency_scoring():
    """Verify that score_trigger reads urgency (1-5) and combines with context relevance."""
    # Base urgency tests without context
    trg_u1 = {"id": "t1", "kind": "festival_upcoming", "urgency": 1}
    trg_u3 = {"id": "t3", "kind": "festival_upcoming", "urgency": 3}
    trg_u5 = {"id": "t5", "kind": "festival_upcoming", "urgency": 5}
    # No context: merchant_relevance=0, category_relevance=0, merchant-level customer_relevance=5
    assert score_trigger(trg_u1) == 20 + 5
    assert score_trigger(trg_u3) == 60 + 5
    assert score_trigger(trg_u5) == 100 + 5

    # Urgency clamping: <=1 clamped to 1, >=5 clamped to 5, invalid string to 1
    assert score_trigger({"id": "t0", "urgency": 0}) == 20 + 5
    assert score_trigger({"id": "t9", "urgency": 99}) == 100 + 5
    assert score_trigger({"id": "tnone", "urgency": None}) == 20 + 5
    assert score_trigger({"id": "tbad", "urgency": "invalid"}) == 20 + 5

    # Relevance bonuses
    merchant = {
        "merchant_id": "m1",
        "category_slug": "dentists",
        "subscription": {"status": "active"},
    }
    category = {"slug": "dentists"}
    customer = {"customer_id": "c1", "preferences": {"reminder_opt_in": True}}

    # Active merchant (+15), matching category (+15), merchant-level (+5)
    score_full_m = score_trigger({"id": "t_m", "urgency": 4}, merchant, category, None)
    assert score_full_m == 80 + 15 + 15 + 5

    # Customer trigger with customer present (+15)
    trg_cust = {"id": "t_c", "customer_id": "c1", "urgency": 4}
    score_cust = score_trigger(trg_cust, merchant, category, customer)
    assert score_cust == 80 + 15 + 15 + 15

    # Customer trigger with missing customer (-10)
    score_missing_cust = score_trigger(trg_cust, merchant, category, None)
    assert score_missing_cust == 80 + 15 + 15 - 10

    # Deprecated TRIGGER_PRIORITY dict remains accessible for legacy consumers
    assert TRIGGER_PRIORITY["compliance_alert"] == 100


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


# ---------------------------------------------------------------------------
# Real Taxonomy Coverage (§4.3 and engagement loops)
# ---------------------------------------------------------------------------

def test_real_external_triggers():
    """Verify all real external trigger kinds from challenge-brief §4.3 render properly."""
    seed_merchant_and_category("m_ext", "dentists", "Dr. Meera Dental")

    # 1. festival_upcoming
    context_store.set("trigger", "trg_fest", 1, {
        "id": "trg_fest",
        "kind": "festival_upcoming",
        "merchant_id": "m_ext",
        "urgency": 3,
        "payload": {"festival_name": "Diwali", "days_until": 4},
    })
    resp1 = client.post("/v1/tick", json={"now": "2026-04-26T10:00:00Z", "available_triggers": ["trg_fest"]})
    assert resp1.status_code == 200
    a1 = resp1.json()["actions"][0]
    assert "Diwali is in 4 days" in a1["body"]
    assert "Dr. Meera Dental" in a1["body"]
    assert a1["template_name"] == "template_opportunity_event_v1"

    # 2. weather_heatwave
    context_store.set("trigger", "trg_heat", 1, {
        "id": "trg_heat",
        "kind": "weather_heatwave",
        "merchant_id": "m_ext",
        "urgency": 4,
        "payload": {"temperature": "42°C", "city": "Delhi"},
    })
    resp2 = client.post("/v1/tick", json={"now": "2026-04-26T10:00:00Z", "available_triggers": ["trg_heat"]})
    assert resp2.status_code == 200
    a2 = resp2.json()["actions"][0]
    assert "heatwave alert (42°C) in Delhi" in a2["body"]
    assert a2["template_name"] == "template_opportunity_event_v1"

    # 3. local_news_event
    context_store.set("trigger", "trg_news", 1, {
        "id": "trg_news",
        "kind": "local_news_event",
        "merchant_id": "m_ext",
        "urgency": 3,
        "payload": {"event": "Expressway closed for maintenance"},
    })
    resp3 = client.post("/v1/tick", json={"now": "2026-04-26T10:00:00Z", "available_triggers": ["trg_news"]})
    assert resp3.status_code == 200
    a3 = resp3.json()["actions"][0]
    assert "Expressway closed for maintenance" in a3["body"]
    assert a3["template_name"] == "template_opportunity_event_v1"

    # 4. category_research_digest_release & research_digest
    context_store.set("trigger", "trg_digest", 1, {
        "id": "trg_digest",
        "kind": "category_research_digest_release",
        "merchant_id": "m_ext",
        "urgency": 3,
        "payload": {"title": "3-mo fluoride recall cuts caries recurrence 38%"},
    })
    resp4 = client.post("/v1/tick", json={"now": "2026-04-26T10:00:00Z", "available_triggers": ["trg_digest"]})
    assert resp4.status_code == 200
    a4 = resp4.json()["actions"][0]
    assert "fluoride recall" in a4["body"]
    assert a4["template_name"] == "template_research_knowledge_v1"

    # Also research_digest (alias / dataset naming)
    context_store.set("trigger", "trg_rd", 1, {
        "id": "trg_rd",
        "kind": "research_digest",
        "merchant_id": "m_ext",
        "urgency": 3,
        "payload": {"top_item": {"title": "New clinical guidelines on sealants"}},
    })
    resp4b = client.post("/v1/tick", json={"now": "2026-04-26T10:00:00Z", "available_triggers": ["trg_rd"]})
    assert resp4b.status_code == 200
    a4b = resp4b.json()["actions"][0]
    assert "sealants" in a4b["body"]
    assert a4b["template_name"] == "template_research_digest_v1"

    # 5. regulation_change
    context_store.set("trigger", "trg_reg", 1, {
        "id": "trg_reg",
        "kind": "regulation_change",
        "merchant_id": "m_ext",
        "urgency": 4,
        "payload": {"topic": "DCI radiograph dose limit revised", "deadline_iso": "2026-12-15"},
    })
    resp5 = client.post("/v1/tick", json={"now": "2026-04-26T10:00:00Z", "available_triggers": ["trg_reg"]})
    assert resp5.status_code == 200
    a5 = resp5.json()["actions"][0]
    assert "DCI radiograph dose limit revised" in a5["body"]
    assert a5["template_name"] == "template_research_knowledge_v1"

    # 6. competitor_opened
    context_store.set("trigger", "trg_comp_open", 1, {
        "id": "trg_comp_open",
        "kind": "competitor_opened",
        "merchant_id": "m_ext",
        "urgency": 3,
        "payload": {"competitor_name": "Smile Studio", "distance_km": 1.3},
    })
    resp6 = client.post("/v1/tick", json={"now": "2026-04-26T10:00:00Z", "available_triggers": ["trg_comp_open"]})
    assert resp6.status_code == 200
    a6 = resp6.json()["actions"][0]
    assert "Smile Studio" in a6["body"]
    assert "1.3km away" in a6["body"]
    assert a6["template_name"] == "template_opportunity_event_v1"

    # 7. category_trend_movement
    context_store.set("trigger", "trg_trend", 1, {
        "id": "trg_trend",
        "kind": "category_trend_movement",
        "merchant_id": "m_ext",
        "urgency": 3,
        "payload": {"trend": "clear aligners Delhi", "delta_pct": 0.62},
    })
    resp7 = client.post("/v1/tick", json={"now": "2026-04-26T10:00:00Z", "available_triggers": ["trg_trend"]})
    assert resp7.status_code == 200
    a7 = resp7.json()["actions"][0]
    assert "clear aligners Delhi" in a7["body"]
    assert "+62%" in a7["body"]
    assert a7["template_name"] == "template_research_knowledge_v1"


def test_real_internal_triggers():
    """Verify all real internal trigger kinds from challenge-brief §4.3 render properly."""
    seed_merchant_and_category("m_int", "dentists", "Dr. Meera Dental")

    # 1. perf_spike
    context_store.set("trigger", "trg_spike", 1, {
        "id": "trg_spike",
        "kind": "perf_spike",
        "merchant_id": "m_int",
        "urgency": 3,
        "payload": {"metric": "views", "delta_pct": 0.28, "likely_driver": "instagram_reel"},
    })
    resp1 = client.post("/v1/tick", json={"now": "2026-04-26T10:00:00Z", "available_triggers": ["trg_spike"]})
    assert resp1.status_code == 200
    a1 = resp1.json()["actions"][0]
    assert "views jumped +28%" in a1["body"]
    assert "instagram reel" in a1["body"]
    assert a1["template_name"] == "template_performance_v1"

    # 2. perf_dip
    context_store.set("trigger", "trg_dip", 1, {
        "id": "trg_dip",
        "kind": "perf_dip",
        "merchant_id": "m_int",
        "urgency": 4,
        "payload": {"metric": "calls", "delta_pct": -0.40},
    })
    resp2 = client.post("/v1/tick", json={"now": "2026-04-26T10:00:00Z", "available_triggers": ["trg_dip"]})
    assert resp2.status_code == 200
    a2 = resp2.json()["actions"][0]
    assert "calls dipped 40%" in a2["body"]
    assert a2["template_name"] == "template_performance_drop_v1"

    # 3. milestone_reached
    context_store.set("trigger", "trg_mile", 1, {
        "id": "trg_mile",
        "kind": "milestone_reached",
        "merchant_id": "m_int",
        "urgency": 2,
        "payload": {"metric": "reviews", "milestone_value": 100, "value_now": 95, "is_imminent": True},
    })
    resp3 = client.post("/v1/tick", json={"now": "2026-04-26T10:00:00Z", "available_triggers": ["trg_mile"]})
    assert resp3.status_code == 200
    a3 = resp3.json()["actions"][0]
    assert "95 reviews" in a3["body"]
    assert "just 5 away" in a3["body"]
    assert a3["template_name"] == "template_milestone_v1"

    # 4. dormant_with_vera
    context_store.set("trigger", "trg_dorm", 1, {
        "id": "trg_dorm",
        "kind": "dormant_with_vera",
        "merchant_id": "m_int",
        "urgency": 2,
        "payload": {"days_since_last_merchant_message": 14},
    })
    resp4 = client.post("/v1/tick", json={"now": "2026-04-26T10:00:00Z", "available_triggers": ["trg_dorm"]})
    assert resp4.status_code == 200
    a4 = resp4.json()["actions"][0]
    assert "14 days since our last chat" in a4["body"]
    assert a4["template_name"] == "template_relationship_v1"

    # 5. review_theme_emerged
    context_store.set("trigger", "trg_rev", 1, {
        "id": "trg_rev",
        "kind": "review_theme_emerged",
        "merchant_id": "m_int",
        "urgency": 3,
        "payload": {"theme": "wait_time", "occurrences_30d": 3},
    })
    resp5 = client.post("/v1/tick", json={"now": "2026-04-26T10:00:00Z", "available_triggers": ["trg_rev"]})
    assert resp5.status_code == 200
    a5 = resp5.json()["actions"][0]
    assert "3 recent customer reviews mentioning 'wait time'" in a5["body"]
    assert a5["template_name"] == "template_relationship_v1"

    # 6. scheduled_recurring
    context_store.set("trigger", "trg_recur", 1, {
        "id": "trg_recur",
        "kind": "scheduled_recurring",
        "merchant_id": "m_int",
        "urgency": 1,
    })
    resp6 = client.post("/v1/tick", json={"now": "2026-04-26T10:00:00Z", "available_triggers": ["trg_recur"]})
    assert resp6.status_code == 200
    a6 = resp6.json()["actions"][0]
    assert "highest customer demand" in a6["body"]
    assert a6["template_name"] == "template_curious_ask_v1"


def test_real_customer_scoped_triggers():
    """Verify customer-scoped triggers (recall_due, customer_lapsed_soft, appointment_tomorrow, unplanned_slot_open)."""
    seed_merchant_and_category("m_cust", "dentists", "Dr. Meera Dental")
    context_store.set("customer", "c_priya", 1, {
        "customer_id": "c_priya",
        "identity": {"name": "Priya", "phone": "+919876543210"},
        "preferences": {"reminder_opt_in": True},
    })

    # 1. recall_due
    context_store.set("trigger", "trg_rec", 1, {
        "id": "trg_rec",
        "scope": "customer",
        "kind": "recall_due",
        "merchant_id": "m_cust",
        "customer_id": "c_priya",
        "urgency": 4,
        "payload": {"service_due": "teeth cleaning"},
    })
    resp1 = client.post("/v1/tick", json={"now": "2026-04-26T10:00:00Z", "available_triggers": ["trg_rec"]})
    assert resp1.status_code == 200
    a1 = resp1.json()["actions"][0]
    assert a1["customer_id"] == "c_priya"
    assert "Priya is due for teeth cleaning" in a1["body"]
    assert a1["template_name"] == "template_recall_due_v1"

    # 2. customer_lapsed_soft
    context_store.set("trigger", "trg_lapse", 1, {
        "id": "trg_lapse",
        "scope": "customer",
        "kind": "customer_lapsed_soft",
        "merchant_id": "m_cust",
        "customer_id": "c_priya",
        "urgency": 3,
    })
    resp2 = client.post("/v1/tick", json={"now": "2026-04-26T10:00:00Z", "available_triggers": ["trg_lapse"]})
    assert resp2.status_code == 200
    a2 = resp2.json()["actions"][0]
    assert "Priya hasn't visited in over 60 days" in a2["body"]
    assert a2["template_name"] == "template_recall_lapse_v1"

    # 3. appointment_tomorrow
    context_store.set("trigger", "trg_appt", 1, {
        "id": "trg_appt",
        "scope": "customer",
        "kind": "appointment_tomorrow",
        "merchant_id": "m_cust",
        "customer_id": "c_priya",
        "urgency": 4,
        "payload": {"time": "11:00 AM tomorrow"},
    })
    resp3 = client.post("/v1/tick", json={"now": "2026-04-26T10:00:00Z", "available_triggers": ["trg_appt"]})
    assert resp3.status_code == 200
    a3 = resp3.json()["actions"][0]
    assert "Priya has an appointment scheduled for 11:00 AM tomorrow" in a3["body"]
    assert a3["template_name"] == "template_recall_lapse_v1"

    # 4. unplanned_slot_open (capacity optimizer from engagement loops)
    context_store.set("trigger", "trg_slot", 1, {
        "id": "trg_slot",
        "scope": "customer",
        "kind": "unplanned_slot_open",
        "merchant_id": "m_cust",
        "urgency": 3,
        "payload": {"slot": "3:00 PM tomorrow"},
    })
    resp4 = client.post("/v1/tick", json={"now": "2026-04-26T10:00:00Z", "available_triggers": ["trg_slot"]})
    assert resp4.status_code == 200
    a4 = resp4.json()["actions"][0]
    assert "you have 3:00 PM tomorrow open" in a4["body"]
    assert a4["template_name"] == "template_recall_lapse_v1"


def test_unhandled_trigger_fallback_and_warning_logged(caplog):
    """Ensure any trigger kind not explicitly handled gets safe deterministic fallback and logs warning."""
    import logging
    seed_merchant_and_category("m_fallback", "dentists", "Dr. Meera Dental")
    context_store.set("trigger", "trg_unknown", 1, {
        "id": "trg_unknown",
        "kind": "unmapped_experimental_event",
        "merchant_id": "m_fallback",
        "urgency": 2,
    })

    with caplog.at_level(logging.WARNING):
        resp = client.post("/v1/tick", json={"now": "2026-04-26T10:00:00Z", "available_triggers": ["trg_unknown"]})

    assert resp.status_code == 200
    actions = resp.json()["actions"]
    assert len(actions) == 1
    act = actions[0]
    assert act["template_name"] == "template_default_v1"
    assert "Dr. Meera Dental" in act["body"]
    assert "Free Dental Checkup" in act["body"]
    assert "Unmatched trigger kind 'unmapped_experimental_event' falling back to generic family" in caplog.text


def test_expiry_uses_tick_now_parameter_not_server_wallclock():
    """
    Expiry checks must compare strictly against the 'now' parameter in the /v1/tick
    request body, never against the server's real wall-clock time.
    """
    seed_merchant_and_category("m_time_test", "dentists", "Time Test Clinic")

    fake_now = "2024-06-01T12:00:00Z"

    # Trigger A: expires in the future relative to fake_now (2024-06-02),
    # but in the past relative to real 2026 server wall clock
    context_store.set("trigger", "trg_rel_future", 1, {
        "id": "trg_rel_future",
        "kind": "performance_drop",
        "merchant_id": "m_time_test",
        "urgency": 4,
        "expires_at": "2024-06-02T00:00:00Z",
        "suppression_key": "time_test:future",
    })

    # Trigger B: expires in the past relative to fake_now (2024-05-31)
    context_store.set("trigger", "trg_rel_past", 1, {
        "id": "trg_rel_past",
        "kind": "performance_drop",
        "merchant_id": "m_time_test",
        "urgency": 5,
        "expires_at": "2024-05-31T00:00:00Z",
        "suppression_key": "time_test:past",
    })

    # Test 1: Relative to fake_now (2024-06-01):
    # trg_rel_past is filtered out as expired relative to fake_now
    # trg_rel_future is NOT filtered out because it expires after fake_now
    resp = client.post("/v1/tick", json={
        "now": fake_now,
        "available_triggers": ["trg_rel_future", "trg_rel_past"],
    })
    assert resp.status_code == 200
    actions = resp.json()["actions"]
    assert len(actions) == 1
    assert actions[0]["trigger_id"] == "trg_rel_future"

    # Test 2: If fake_now is shifted past 2024-06-02, trg_rel_future IS filtered out as expired
    resp_future_now = client.post("/v1/tick", json={
        "now": "2024-06-03T00:00:00Z",
        "available_triggers": ["trg_rel_future", "trg_rel_past"],
    })
    assert resp_future_now.status_code == 200
    assert len(resp_future_now.json()["actions"]) == 0

    # Test 3: If fake_now is earlier than 2024-05-31, neither trigger is expired
    resp_earlier_now = client.post("/v1/tick", json={
        "now": "2024-05-01T00:00:00Z",
        "available_triggers": ["trg_rel_future", "trg_rel_past"],
    })
    assert resp_earlier_now.status_code == 200
    # Higher urgency trigger (trg_rel_past has urgency 5) is selected
    assert len(resp_earlier_now.json()["actions"]) == 1
    assert resp_earlier_now.json()["actions"][0]["trigger_id"] == "trg_rel_past"


# ---------------------------------------------------------------------------
# Deterministic Tests for Time-Aware Suppression Model
# ---------------------------------------------------------------------------

def test_suppression_immediate_replay():
    """Repeated ticks at the exact same 'now' do not resend the same campaign."""
    seed_merchant_and_category("m_rep_01", "dentists", "Immediate Replay Dental")
    context_store.set("trigger", "trg_replay_1", 1, {
        "id": "trg_replay_1",
        "kind": "research_digest",
        "merchant_id": "m_rep_01",
        "urgency": 3,
        "suppression_key": "research:dentists:2026-W17",
        "expires_at": "2026-05-15T00:00:00Z",
    })

    tick_now = "2026-04-26T10:00:00Z"

    # First tick succeeds and sends action
    resp1 = client.post("/v1/tick", json={
        "now": tick_now,
        "available_triggers": ["trg_replay_1"],
    })
    assert resp1.status_code == 200
    actions1 = resp1.json()["actions"]
    assert len(actions1) == 1
    assert actions1[0]["trigger_id"] == "trg_replay_1"
    assert actions1[0]["suppression_key"] == "research:dentists:2026-W17"

    # Verify key is actively suppressed at tick_now
    assert check_suppressed("research:dentists:2026-W17", now=tick_now) is True

    # Immediate replay with same timestamp must yield 0 actions
    resp2 = client.post("/v1/tick", json={
        "now": tick_now,
        "available_triggers": ["trg_replay_1"],
    })
    assert resp2.status_code == 200
    assert resp2.json()["actions"] == []


def test_suppression_boundary_timestamps():
    """Verifies strict boundary semantics: [sent_at, expires_at).
    At now < expires_at: True (suppressed)
    At now == expires_at: False (expired / allowed)
    At now > expires_at: False (expired / allowed)
    """
    # 1. Test inferred ISO week window (7 days = 604,800s)
    sup_key_week = "research:dentists:2026-W17"
    sent_time = "2026-04-26T10:00:00Z"
    mark_suppressed(sup_key_week, sent_at=sent_time)

    rec = get_suppression_record(sup_key_week)
    assert rec is not None
    assert rec["window_seconds"] == 7 * 86400
    assert rec["expires_at"].isoformat() == "2026-05-03T10:00:00+00:00"

    # Immediately at send time -> suppressed
    assert check_suppressed(sup_key_week, now="2026-04-26T10:00:00Z") is True
    # 1 second before expiry boundary -> suppressed
    assert check_suppressed(sup_key_week, now="2026-05-03T09:59:59Z") is True
    # Exact expiry boundary -> expired (not suppressed)
    assert check_suppressed(sup_key_week, now="2026-05-03T10:00:00Z") is False
    # 1 second after boundary -> expired
    assert check_suppressed(sup_key_week, now="2026-05-03T10:00:01Z") is False

    # 2. Test explicit window_seconds parameter (e.g., 3600 seconds = 1 hour)
    sup_key_custom = "custom:hourly:alert"
    mark_suppressed(sup_key_custom, sent_at="2026-04-26T12:00:00Z", window_seconds=3600)
    assert check_suppressed(sup_key_custom, now="2026-04-26T12:30:00Z") is True
    assert check_suppressed(sup_key_custom, now="2026-04-26T12:59:59Z") is True
    assert check_suppressed(sup_key_custom, now="2026-04-26T13:00:00Z") is False
    assert check_suppressed(sup_key_custom, now="2026-04-26T13:00:01Z") is False

    # 3. Test explicit expires_at parameter
    sup_key_fixed = "fixed:cutoff:notice"
    mark_suppressed(sup_key_fixed, sent_at="2026-04-26T00:00:00Z", expires_at="2026-04-28T18:00:00Z")
    assert check_suppressed(sup_key_fixed, now="2026-04-28T17:59:59Z") is True
    assert check_suppressed(sup_key_fixed, now="2026-04-28T18:00:00Z") is False


def test_suppression_expired_suppression_allows_resend():
    """Trigger can be resent once its frequency window has elapsed."""
    seed_merchant_and_category("m_resend_01", "dentists", "Resend Test Dental")
    # Date bucket suppression key (:YYYY-MM-DD -> 1 day = 86,400s)
    sup_key = "daily_pulse:m_resend_01:2026-04-26"
    context_store.set("trigger", "trg_daily_1", 1, {
        "id": "trg_daily_1",
        "kind": "curious_ask",
        "merchant_id": "m_resend_01",
        "urgency": 2,
        "suppression_key": sup_key,
        "expires_at": "2026-05-30T00:00:00Z",  # trigger offer valid for a month
    })

    t0 = "2026-04-26T10:00:00Z"
    t_during = "2026-04-26T22:00:00Z"
    t_after = "2026-04-27T10:00:00Z"  # exactly 24 hours later

    # Tick 1: Sent at t0
    resp1 = client.post("/v1/tick", json={
        "now": t0,
        "available_triggers": ["trg_daily_1"],
    })
    assert resp1.status_code == 200
    assert len(resp1.json()["actions"]) == 1

    # Tick 2: During 24h window at t_during -> suppressed
    resp2 = client.post("/v1/tick", json={
        "now": t_during,
        "available_triggers": ["trg_daily_1"],
    })
    assert resp2.status_code == 200
    assert resp2.json()["actions"] == []

    # Tick 3: At t_after (24 hours elapsed) -> suppression expired, action sent again!
    resp3 = client.post("/v1/tick", json={
        "now": t_after,
        "available_triggers": ["trg_daily_1"],
    })
    assert resp3.status_code == 200
    assert len(resp3.json()["actions"]) == 1
    assert resp3.json()["actions"][0]["trigger_id"] == "trg_daily_1"


def test_suppression_different_merchants_isolation():
    """Suppression of a key for merchant A does not suppress merchant B."""
    seed_merchant_and_category("m_iso_01", "dentists", "Clinic Alpha")
    seed_merchant_and_category("m_iso_02", "dentists", "Clinic Beta")

    context_store.set("trigger", "trg_alpha", 1, {
        "id": "trg_alpha",
        "kind": "compliance_alert",
        "merchant_id": "m_iso_01",
        "urgency": 4,
        "suppression_key": "compliance:m_iso_01:2026-W17",
    })
    context_store.set("trigger", "trg_beta", 1, {
        "id": "trg_beta",
        "kind": "compliance_alert",
        "merchant_id": "m_iso_02",
        "urgency": 4,
        "suppression_key": "compliance:m_iso_02:2026-W17",
    })

    now_iso = "2026-04-26T10:00:00Z"

    # Send trigger only for m_iso_01
    resp1 = client.post("/v1/tick", json={
        "now": now_iso,
        "available_triggers": ["trg_alpha"],
    })
    assert resp1.status_code == 200
    assert len(resp1.json()["actions"]) == 1
    assert resp1.json()["actions"][0]["merchant_id"] == "m_iso_01"

    # m_iso_01 suppression key is active, but m_iso_02 is unsuppressed
    assert check_suppressed("compliance:m_iso_01:2026-W17", now=now_iso) is True
    assert check_suppressed("compliance:m_iso_02:2026-W17", now=now_iso) is False

    # Tick both triggers: m_iso_01 should be skipped, m_iso_02 should execute
    resp2 = client.post("/v1/tick", json={
        "now": now_iso,
        "available_triggers": ["trg_alpha", "trg_beta"],
    })
    assert resp2.status_code == 200
    actions = resp2.json()["actions"]
    assert len(actions) == 1
    assert actions[0]["merchant_id"] == "m_iso_02"
    assert actions[0]["trigger_id"] == "trg_beta"


def test_suppression_different_triggers_same_merchant():
    """Suppressing trigger 1 does not suppress trigger 2 for the same merchant."""
    seed_merchant_and_category("m_multi_trg", "dentists", "Multi Trigger Clinic")

    context_store.set("trigger", "trg_research_digest", 1, {
        "id": "trg_research_digest",
        "kind": "research_digest",
        "merchant_id": "m_multi_trg",
        "urgency": 4,
        "suppression_key": "research:m_multi_trg:2026-W17",
    })
    context_store.set("trigger", "trg_festival_promo", 1, {
        "id": "trg_festival_promo",
        "kind": "festival",
        "merchant_id": "m_multi_trg",
        "urgency": 3,
        "suppression_key": "festival:m_multi_trg:akshaya_tritiya",
    })

    now_iso = "2026-04-26T10:00:00Z"

    # Send research digest first
    resp1 = client.post("/v1/tick", json={
        "now": now_iso,
        "available_triggers": ["trg_research_digest"],
    })
    assert resp1.status_code == 200
    assert len(resp1.json()["actions"]) == 1
    assert resp1.json()["actions"][0]["trigger_id"] == "trg_research_digest"

    assert check_suppressed("research:m_multi_trg:2026-W17", now=now_iso) is True
    assert check_suppressed("festival:m_multi_trg:akshaya_tritiya", now=now_iso) is False

    # Now tick with festival promo: should execute without interference
    resp2 = client.post("/v1/tick", json={
        "now": now_iso,
        "available_triggers": ["trg_research_digest", "trg_festival_promo"],
    })
    assert resp2.status_code == 200
    actions = resp2.json()["actions"]
    assert len(actions) == 1
    assert actions[0]["trigger_id"] == "trg_festival_promo"


def test_suppression_identical_triggers_different_time_buckets():
    """Identical trigger kind/content with a new time bucket sends again."""
    seed_merchant_and_category("m_bucket_01", "dentists", "Time Bucket Clinic")

    # Week 17 trigger
    context_store.set("trigger", "trg_week_17", 1, {
        "id": "trg_week_17",
        "kind": "research_digest",
        "merchant_id": "m_bucket_01",
        "urgency": 3,
        "suppression_key": "research:dentists:2026-W17",
    })
    # Week 18 trigger (new time bucket)
    context_store.set("trigger", "trg_week_18", 1, {
        "id": "trg_week_18",
        "kind": "research_digest",
        "merchant_id": "m_bucket_01",
        "urgency": 3,
        "suppression_key": "research:dentists:2026-W18",
    })

    now_w17 = "2026-04-26T10:00:00Z"
    now_w18 = "2026-05-03T10:00:00Z"

    # Tick in Week 17 fires Week 17 trigger
    resp1 = client.post("/v1/tick", json={
        "now": now_w17,
        "available_triggers": ["trg_week_17"],
    })
    assert resp1.status_code == 200
    assert len(resp1.json()["actions"]) == 1
    assert resp1.json()["actions"][0]["suppression_key"] == "research:dentists:2026-W17"

    # In Week 18, Week 18 trigger is fresh and not suppressed
    assert check_suppressed("research:dentists:2026-W18", now=now_w18) is False

    resp2 = client.post("/v1/tick", json={
        "now": now_w18,
        "available_triggers": ["trg_week_18"],
    })
    assert resp2.status_code == 200
    assert len(resp2.json()["actions"]) == 1
    assert resp2.json()["actions"][0]["trigger_id"] == "trg_week_18"
    assert resp2.json()["actions"][0]["suppression_key"] == "research:dentists:2026-W18"


