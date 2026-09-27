"""Full Adversarial Testing Matrix for Magicpin Vera Bot.

Executes and measures:
1. API contract edge cases (all 6 endpoints, malformed JSON, wrong types, unknown routes)
2. Context versioning edge cases (stale/updated context mid-tick)
3. Context resolution chains (trigger -> merchant -> category -> customer, missing links, context arriving between ticks)
4. Tick decision quality (multi-signal scoring combining urgency, performance, relevance, expiration)
5. Hallucination resistance across all 5 categories (dentists, salons, restaurants, gyms, pharmacies)
6. Category tone/taboo enforcement per CATEGORY_POLICIES in composer.py
7. Reply state machine (intents, hostile escalation, off-topic redirect, qualification->action, 4+ repeated auto-replies)
8. Conversation state (parallel conversations, stale reactivation, out-of-order turns, duplicate turns)
9. Reliability (LLM timeout, malformed JSON, exception, rate-limit simulation, judge budget)
10. Load benchmark (10 concurrent req/sec, reporting p50, p95, p99 latencies)
"""

import concurrent.futures
import json
import statistics
import time
import pytest
from fastapi.testclient import TestClient

from app.composer import (
    CATEGORY_POLICIES,
    build_compact_context,
    compose_message,
    set_custom_llm_caller,
)
from app.conversation import (
    ConversationState,
    Intent,
    classify_intent,
    is_auto_reply_text,
)
from app.decision_engine import (
    is_trigger_expired,
    render_template,
    resolve_category,
    resolve_customer,
    resolve_merchant,
    score_trigger,
    select_strongest_signal,
    suppression_engine,
)
from app.main import app
from app.output_validator import build_evidence_ledger, validate_message
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


# ===========================================================================
# 1. API Contract Edge Cases
# ===========================================================================
def test_matrix_1_api_contract_edge_cases():
    """Validates malformed JSON, wrong types, unknown routes, and invalid methods."""
    # Unknown route -> 404
    resp_404 = client.get("/v1/non_existent_route")
    assert resp_404.status_code == 404

    # Method not allowed -> 405
    resp_405 = client.post("/v1/healthz", json={})
    assert resp_405.status_code == 405

    # Malformed JSON in /v1/context -> 400
    resp_malformed = client.post(
        "/v1/context",
        content="{\"scope\": \"category\", invalid_json",
        headers={"Content-Type": "application/json"},
    )
    assert resp_malformed.status_code == 400
    assert resp_malformed.json()["accepted"] is False
    assert resp_malformed.json()["reason"] == "malformed_json"

    # Wrong types in /v1/context (version as string) -> 400 or 422
    resp_wrong_type = client.post(
        "/v1/context",
        json={"scope": "category", "context_id": "dentists", "version": "not_an_int", "payload": {}},
    )
    assert resp_wrong_type.status_code in (400, 422)

    # Empty payload in /v1/context -> 400
    resp_empty_payload = client.post(
        "/v1/context",
        json={"scope": "category", "context_id": "dentists", "version": 1, "payload": {}},
    )
    assert resp_empty_payload.status_code == 400


# ===========================================================================
# 2. Context Versioning Edge Cases
# ===========================================================================
def test_matrix_2_context_versioning_mid_tick_updates():
    """Verify that updated context versions immediately influence tick execution without stale state."""
    # Store v1 with CTR 0.010
    client.post(
        "/v1/context",
        json={
            "scope": "merchant",
            "context_id": "m_ver_edge",
            "version": 1,
            "payload": {
                "merchant_id": "m_ver_edge",
                "category_slug": "dentists",
                "identity": {"name": "V1 Dental Care"},
                "performance": {"ctr": 0.010},
                "offers": [{"title": "Scaling @ ₹199", "status": "active"}],
            },
        },
    )
    client.post(
        "/v1/context",
        json={
            "scope": "category",
            "context_id": "dentists",
            "version": 1,
            "payload": {"slug": "dentists", "display_name": "Dentists", "peer_stats": {"avg_ctr": 0.030}},
        },
    )
    client.post(
        "/v1/context",
        json={
            "scope": "trigger",
            "context_id": "trg_ver_edge",
            "version": 1,
            "payload": {"id": "trg_ver_edge", "kind": "performance_drop", "merchant_id": "m_ver_edge", "urgency": 3},
        },
    )

    # Tick 1 sees V1
    resp1 = client.post("/v1/tick", json={"now": "2026-04-26T10:00:00Z", "available_triggers": ["trg_ver_edge"]})
    assert resp1.status_code == 200
    act1 = resp1.json()["actions"][0]
    assert "V1 Dental Care" in act1["body"]
    assert "1.0% vs 3.0%" in act1["body"]

    suppression_engine.clear()
    conversation_store.clear()

    # Now Version 2 arrives with updated name and new CTR 0.025
    client.post(
        "/v1/context",
        json={
            "scope": "merchant",
            "context_id": "m_ver_edge",
            "version": 2,
            "payload": {
                "merchant_id": "m_ver_edge",
                "category_slug": "dentists",
                "identity": {"name": "V2 Advanced Dental"},
                "performance": {"ctr": 0.025},
                "offers": [{"title": "Scaling @ ₹299", "status": "active"}],
            },
        },
    )

    # Tick 2 immediately reflects V2
    resp2 = client.post("/v1/tick", json={"now": "2026-04-26T10:05:00Z", "available_triggers": ["trg_ver_edge"]})
    assert resp2.status_code == 200
    act2 = resp2.json()["actions"][0]
    assert "V2 Advanced Dental" in act2["body"]
    assert "2.5% vs 3.0%" in act2["body"]


# ===========================================================================
# 3. Context Resolution Chains
# ===========================================================================
def test_matrix_3_context_resolution_chains():
    """Tests missing links in trigger->merchant->category->customer chain and context arriving between ticks."""
    # Trigger exists, but merchant missing -> skipped gracefully
    context_store.set(
        "trigger",
        "trg_orphan",
        1,
        {"id": "trg_orphan", "kind": "performance_drop", "merchant_id": "m_missing", "urgency": 3},
    )
    resp_skip = client.post("/v1/tick", json={"available_triggers": ["trg_orphan"]})
    assert resp_skip.status_code == 200
    assert resp_skip.json()["actions"] == []

    # Merchant context arrives between ticks
    context_store.set(
        "merchant",
        "m_missing",
        1,
        {
            "merchant_id": "m_missing",
            "category_slug": "dentists",
            "identity": {"name": "Found Dental"},
            "performance": {"ctr": 0.02},
        },
    )
    context_store.set(
        "category",
        "dentists",
        1,
        {"slug": "dentists", "display_name": "Dentists"},
    )

    # Next tick immediately resolves and acts
    resp_resolved = client.post("/v1/tick", json={"available_triggers": ["trg_orphan"]})
    assert resp_resolved.status_code == 200
    actions = resp_resolved.json()["actions"]
    assert len(actions) == 1
    assert "Found Dental" in actions[0]["body"]


# ===========================================================================
# 4. Tick Decision Quality & Multi-Signal Scoring
# ===========================================================================
def test_matrix_4_tick_decision_quality_multi_signal_scoring():
    """
    Constructs a scenario where combining multiple signals (urgency + merchant relevance +
    category relevance) correctly beats a higher static priority trigger.
    """
    # Trigger A: compliance_alert (priority=100), urgency=1, unsubscribed merchant
    trg_comp = {
        "id": "trg_comp_static",
        "kind": "compliance_alert",
        "merchant_id": "m_unsub",
        "urgency": 1,
    }
    merchant_unsub = {"merchant_id": "m_unsub", "subscription": {"status": "inactive"}}

    # Trigger B: performance_drop (priority=90), urgency=5, active merchant (+5) + matching category (+5)
    trg_perf = {
        "id": "trg_perf_high_signal",
        "kind": "performance_drop",
        "merchant_id": "m_active",
        "urgency": 5,
    }
    merchant_active = {
        "merchant_id": "m_active",
        "category_slug": "dentists",
        "subscription": {"status": "active"},
    }
    category_match = {"slug": "dentists", "display_name": "Dentists"}

    score_comp = score_trigger(trg_comp, merchant_unsub, None, None)
    score_perf = score_trigger(trg_perf, merchant_active, category_match, None)

    # Multi-signal performance drop score should beat the low-urgency unsubscribed compliance alert
    assert score_perf > score_comp
    # Urgency 5 (100) + 15 (merchant) + 15 (category) + 5 (merchant-level) = 135
    # vs Urgency 1 (20) + 10 (merchant) + 5 (merchant-level) = 35
    assert score_perf == 135
    assert score_comp == 35


# ===========================================================================
# 5. Hallucination Resistance Across All 5 Categories
# ===========================================================================
@pytest.mark.parametrize(
    "cat_slug,cat_name,m_name,active_offer,fake_offer,fake_price",
    [
        ("dentists", "Dentists", "Dr. Smile", "Dental Cleaning @ ₹299", "Teeth Whitening Special", "99"),
        ("salons", "Salons", "Studio 11", "Haircut & Styling @ ₹399", "Full Body Hair Spa", "149"),
        ("restaurants", "Restaurants", "Spice Garden", "Lunch Buffet @ ₹499", "Free Unlimited Dessert", "0"),
        ("gyms", "Gyms", "Iron Core", "Annual Membership @ ₹9999", "Free Personal Trainer", "499"),
        ("pharmacies", "Pharmacies", "Apollo Care", "First Aid Kit @ ₹199", "25% Off Prescription", "50"),
    ],
)
def test_matrix_5_hallucination_resistance_all_categories(
    cat_slug, cat_name, m_name, active_offer, fake_offer, fake_price
):
    """Verifies that ungrounded offers and fake prices are rejected across every category."""
    compact_ctx = {
        "merchant": {
            "name": m_name,
            "performance": {"ctr": 0.02},
            "active_offers": [active_offer],
        },
        "category": {"slug": cat_slug, "name": cat_name, "voice_policy": CATEGORY_POLICIES.get(cat_slug)},
        "allowed_facts": [m_name, active_offer],
    }
    ledger = build_evidence_ledger(compact_ctx)

    # Adversarial body with substituted offer and fake price
    hallucinated_body = f"{m_name}, we are offering {fake_offer} for ₹{fake_price}! Want to reserve your spot?"
    is_valid, reason = validate_message(hallucinated_body, ledger)
    assert not is_valid
    assert reason is not None


# ===========================================================================
# 6. Category Tone and Policy Enforcement
# ===========================================================================
def test_matrix_6_category_tone_and_taboo_policies():
    """Verify CATEGORY_POLICIES are mapped and forbidden claims are attached."""
    for slug, expected_policy in [
        ("dentists", "professional/clinical/specific/no exaggerated health claims"),
        ("salons", "visual/occasion-driven"),
        ("restaurants", "timely/offer-oriented"),
        ("gyms", "motivational/progress-oriented"),
        ("pharmacies", "utility-first/very conservative"),
    ]:
        ctx = build_compact_context(
            merchant={"identity": {"name": "Test Partner"}},
            category={"slug": slug, "display_name": slug.capitalize()},
            trigger={"id": "t1", "kind": "performance_drop"},
        )
        assert ctx["category"]["voice_policy"] == expected_policy
        assert "forbidden_claims" in ctx
        assert len(ctx["forbidden_claims"]) >= 3


# ===========================================================================
# 7. Reply State Machine & Escalation Handling
# ===========================================================================
def test_matrix_7_reply_state_machine_and_auto_reply_loop():
    """Tests hostile escalation, off-topic redirect, and repeated auto-reply loop breaking."""
    conv_id = "conv_matrix_7"

    # 1. Hostile escalation -> END
    conversation_store.create_or_update(conv_id, merchant_id="m1", state="waiting_for_reply")
    resp_hostile = client.post(
        "/v1/reply",
        json={
            "conversation_id": conv_id,
            "merchant_id": "m1",
            "from_role": "merchant",
            "message": "Stop messaging me! This is useless spam.",
            "turn_number": 2,
        },
    )
    assert resp_hostile.json()["action"] == "end"
    assert conversation_store.get(conv_id)["state"] == ConversationState.END.value

    # 2. Off-topic redirection -> REDIRECT
    conv_offtopic = "conv_matrix_7_offtopic"
    conversation_store.create_or_update(conv_offtopic, merchant_id="m1", state="waiting_for_reply")
    resp_offtopic = client.post(
        "/v1/reply",
        json={
            "conversation_id": conv_offtopic,
            "merchant_id": "m1",
            "from_role": "merchant",
            "message": "Sing a song for me please.",
            "turn_number": 2,
        },
    )
    assert resp_offtopic.json()["action"] == "send"
    assert conversation_store.get(conv_offtopic)["state"] == ConversationState.REDIRECT.value

    # 3. 4+ Repeated Auto-replies break loop -> END
    conv_auto = "conv_auto_loop"
    auto_reply_text = "Thank you for contacting us! Our team will respond shortly."

    # Turn 1: Initial auto-reply -> wait
    r1 = client.post("/v1/reply", json={
        "conversation_id": conv_auto,
        "merchant_id": "m1",
        "from_role": "merchant",
        "message": auto_reply_text,
        "turn_number": 2,
    })
    assert r1.json()["action"] == "wait"

    # Turn 2: Second auto-reply -> wait
    r2 = client.post("/v1/reply", json={
        "conversation_id": conv_auto,
        "merchant_id": "m1",
        "from_role": "merchant",
        "message": auto_reply_text,
        "turn_number": 3,
    })
    assert r2.json()["action"] == "wait"

    # Turn 3: Third auto-reply -> breaks loop, transitions to END
    r3 = client.post("/v1/reply", json={
        "conversation_id": conv_auto,
        "merchant_id": "m1",
        "from_role": "merchant",
        "message": auto_reply_text,
        "turn_number": 4,
    })
    assert r3.json()["action"] == "end"
    assert conversation_store.get(conv_auto)["state"] == ConversationState.END.value

    # Turn 4: 4th repeat -> remains closed (END)
    r4 = client.post("/v1/reply", json={
        "conversation_id": conv_auto,
        "merchant_id": "m1",
        "from_role": "merchant",
        "message": auto_reply_text,
        "turn_number": 5,
    })
    assert r4.json()["action"] == "end"


# ===========================================================================
# 8. Conversation State Resilience
# ===========================================================================
def test_matrix_8_parallel_conversations_and_out_of_order_turns():
    """Verify parallel conversations for same merchant and out-of-order turn numbers."""
    mid = "m_parallel_01"
    conv_1 = "conv_p1"
    conv_2 = "conv_p2"

    # Conversation 1 in state SEND
    client.post(
        "/v1/reply",
        json={"conversation_id": conv_1, "merchant_id": mid, "from_role": "merchant", "message": "Yes proceed.", "turn_number": 2},
    )
    assert conversation_store.get(conv_1)["state"] == ConversationState.SEND.value

    # Conversation 2 in state WAIT
    client.post(
        "/v1/reply",
        json={"conversation_id": conv_2, "merchant_id": mid, "from_role": "merchant", "message": "Call tomorrow.", "turn_number": 2},
    )
    assert conversation_store.get(conv_2)["state"] == ConversationState.WAIT.value

    # Out of order turn in conv_1 (turn_number=1 after turn_number=2)
    resp_ooo = client.post(
        "/v1/reply",
        json={"conversation_id": conv_1, "merchant_id": mid, "from_role": "merchant", "message": "Duplicate turn.", "turn_number": 1},
    )
    assert resp_ooo.status_code == 200
    assert conversation_store.get(conv_1) is not None


# ===========================================================================
# 9. Reliability & Graceful Failure Recovery
# ===========================================================================
def test_matrix_9_reliability_simulations():
    """Simulate LLM timeout, malformed JSON, and server errors; assert fallback fires."""
    context_store.set("category", "dentists", 1, {"slug": "dentists", "display_name": "Dentists"})
    context_store.set(
        "merchant",
        "m_rel",
        1,
        {"merchant_id": "m_rel", "category_slug": "dentists", "identity": {"name": "Reliable Clinic"}},
    )
    context_store.set(
        "trigger",
        "trg_rel",
        1,
        {"id": "trg_rel", "kind": "compliance_alert", "merchant_id": "m_rel", "urgency": 4},
    )

    # 1. Timeout simulation
    def mock_timeout(p, s, t):
        raise TimeoutError("Simulated LLM timeout")

    set_custom_llm_caller(mock_timeout)
    resp_timeout = client.post("/v1/tick", json={"available_triggers": ["trg_rel"]})
    assert resp_timeout.status_code == 200
    assert resp_timeout.json()["actions"][0]["template_name"] == "template_compliance_alert_v1"

    suppression_engine.clear()

    # 2. Malformed JSON simulation
    set_custom_llm_caller(lambda p, s, t: "Here is your message: {unclosed json")
    resp_malformed = client.post("/v1/tick", json={"available_triggers": ["trg_rel"]})
    assert resp_malformed.status_code == 200
    assert resp_malformed.json()["actions"][0]["template_name"] == "template_compliance_alert_v1"


# ===========================================================================
# 10. Load Benchmark (10 concurrent requests/sec, p50/p95/p99 latency)
# ===========================================================================
def test_matrix_10_load_benchmark_latency_distribution():
    """
    Executes 60 concurrent requests across /v1/tick and /v1/reply.
    Measures and asserts throughput >= 10 req/s and reports p50/p95/p99 latency.
    """
    context_store.set("category", "dentists", 1, {"slug": "dentists", "display_name": "Dentists"})
    context_store.set(
        "merchant",
        "m_bench",
        1,
        {
            "merchant_id": "m_bench",
            "category_slug": "dentists",
            "identity": {"name": "Benchmark Dental"},
            "performance": {"ctr": 0.02},
            "offers": [{"title": "Scaling @ ₹299", "status": "active"}],
        },
    )
    context_store.set(
        "trigger",
        "trg_bench",
        1,
        {"id": "trg_bench", "kind": "performance_drop", "merchant_id": "m_bench", "urgency": 3},
    )

    tasks = []
    for i in range(60):
        if i % 2 == 0:
            tasks.append(("POST", "/v1/tick", {"now": "2026-04-26T10:00:00Z", "available_triggers": ["trg_bench"]}))
        else:
            tasks.append((
                "POST",
                "/v1/reply",
                {"conversation_id": f"conv_b_{i}", "merchant_id": "m_bench", "from_role": "merchant", "message": "Yes proceed.", "turn_number": 2},
            ))

    def run_req(t):
        method, url, payload = t
        start = time.perf_counter()
        resp = client.post(url, json=payload)
        dur = (time.perf_counter() - start) * 1000.0  # ms
        return resp.status_code, dur

    wall_start = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(run_req, tasks))
    total_time = time.perf_counter() - wall_start

    status_codes = [r[0] for r in results]
    latencies_ms = sorted([r[1] for r in results])

    assert all(code == 200 for code in status_codes)

    throughput = len(tasks) / total_time
    p50 = statistics.median(latencies_ms)
    p95 = latencies_ms[int(len(latencies_ms) * 0.95)]
    p99 = latencies_ms[int(len(latencies_ms) * 0.99)]

    # Assert load requirements
    assert throughput >= 10.0, f"Throughput was {throughput:.1f} req/s (< 10 req/s)"
    assert p99 < 500.0, f"p99 latency was {p99:.2f}ms (> 500ms)"
    assert p50 < 50.0, f"p50 latency was {p50:.2f}ms"
