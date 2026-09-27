"""Dedicated cross-merchant isolation audit test suite for Magicpin Vera bot.

Asserts:
1. Category isolation: Merchant A can never use Merchant B's category.
2. Customer isolation: Identical customer IDs across different merchants remain strictly segregated.
3. Offer isolation: Merchant A's actions/replies never leak Merchant B's offers.
4. Performance metric isolation: Merchant A never uses Merchant B's CTR, views, or peer metrics.
5. Conversation isolation: Merchant B cannot access, view, or mutate Merchant A's conversation.
6. Trigger isolation: Triggers belonging to Merchant B cannot be used in Merchant A's conversations.
7. Suppression state isolation: Suppressing Merchant A's trigger never suppresses Merchant B's trigger.
8. Previous message isolation: Bot responses in Conv A never reference previous messages from Conv B.
9. Simultaneous multi-merchant conversations execute cleanly in parallel without cross-talk.
10. Ambiguous relationship ownership fails closed across all pathways.
"""

import concurrent.futures
import pytest
from fastapi.testclient import TestClient

from app.conversation import ConversationState, conversation_state_machine
from app.decision_engine import (
    check_suppressed,
    process_tick,
    resolve_context,
    resolve_customer,
    suppression_engine,
)
from app.main import app
from app.store import context_store, conversation_store

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_state():
    """Wipe all stores before and after each audit test."""
    context_store.clear()
    conversation_store.clear()
    suppression_engine.clear()
    conversation_state_machine.reset()
    yield
    context_store.clear()
    conversation_store.clear()
    suppression_engine.clear()
    conversation_state_machine.reset()


def seed_two_distinct_merchants():
    """Helper to seed Merchant A (Dentist) and Merchant B (Salon) with full context."""
    # Categories
    context_store.set("category", "dentists", 1, {
        "slug": "dentists",
        "display_name": "Dentists & Dental Care",
        "peer_stats": {"avg_ctr": 0.021, "avg_ticket": 1200},
    })
    context_store.set("category", "salons", 1, {
        "slug": "salons",
        "display_name": "Salons & Spas",
        "peer_stats": {"avg_ctr": 0.045, "avg_ticket": 650},
    })

    # Merchant A (Dentist)
    context_store.set("merchant", "m_dentist_01", 1, {
        "id": "m_dentist_01",
        "merchant_id": "m_dentist_01",
        "category_slug": "dentists",
        "identity": {"name": "Smile Dental Clinic", "city": "Delhi", "owner_first_name": "Dr. Rohit"},
        "performance": {"views": 1500, "ctr": 0.019},
        "offers": [{"title": "Scaling & Polishing @ ₹499", "status": "active"}],
    })

    # Merchant B (Salon)
    context_store.set("merchant", "m_salon_02", 1, {
        "id": "m_salon_02",
        "merchant_id": "m_salon_02",
        "category_slug": "salons",
        "identity": {"name": "Glow Luxury Salon", "city": "Mumbai", "owner_first_name": "Pooja"},
        "performance": {"views": 4200, "ctr": 0.048},
        "offers": [{"title": "Keratin Spa Treatment @ ₹1299", "status": "active"}],
    })


# ===========================================================================
# 1. Category Isolation & Ambiguous Category Ownership
# ===========================================================================

def test_category_isolation_fail_closed_on_mismatch():
    """Merchant A cannot use Merchant B's category; category mismatch fails closed."""
    seed_two_distinct_merchants()

    # Trigger targets Merchant A (Dentist) but attempts to inject Merchant B's category ('salons')
    context_store.set("trigger", "trg_injected_cat", 1, {
        "id": "trg_injected_cat",
        "kind": "trend",
        "merchant_id": "m_dentist_01",
        "urgency": 3,
        "payload": {"category": "salons"},  # Mismatched category
    })

    rc = resolve_context(context_store, "trg_injected_cat")
    assert rc is not None
    assert rc.metadata.get("category_mismatch") is True
    # Resolved category defaults strictly to merchant's own category ('dentists')
    assert rc.metadata.get("merchant_category") == "dentists"
    assert rc.metadata.get("trigger_category") == "salons"

    # process_tick must fail closed on category mismatch
    actions = process_tick(
        available_trigger_ids=["trg_injected_cat"],
        now="2026-04-26T10:00:00Z",
        context_store=context_store,
        conversation_store=conversation_store,
    )
    assert actions == [], "Trigger with mismatched category must fail closed"


# ===========================================================================
# 2. Identical Customer IDs in Different Merchant Contexts
# ===========================================================================

def test_identical_customer_ids_in_different_merchant_contexts():
    """Identical customer IDs (e.g. 'c_vip') for multiple merchants remain strictly segregated."""
    seed_two_distinct_merchants()

    # Customer 'c_vip' for Merchant A (Dentist)
    context_store.set("customer", "m_dentist_01:c_vip", 1, {
        "customer_id": "c_vip",
        "merchant_id": "m_dentist_01",
        "identity": {"name": "Priya Sharma (Patient)", "language_pref": "english"},
        "relationship": {"services_received": ["root_canal", "dental_cleaning"], "lifetime_value": 3500},
    })

    # Customer 'c_vip' for Merchant B (Salon)
    context_store.set("customer", "m_salon_02:c_vip", 1, {
        "customer_id": "c_vip",
        "merchant_id": "m_salon_02",
        "identity": {"name": "Priya Patel (Salon Client)", "language_pref": "hindi"},
        "relationship": {"services_received": ["hair_color", "bridal_makeup"], "lifetime_value": 8900},
    })

    # Resolve customer for Merchant A
    cust_a = resolve_customer(context_store, "c_vip", merchant_id="m_dentist_01")
    assert cust_a is not None
    assert cust_a["identity"]["name"] == "Priya Sharma (Patient)"
    assert "root_canal" in cust_a["relationship"]["services_received"]
    assert "bridal_makeup" not in cust_a["relationship"]["services_received"]

    # Resolve customer for Merchant B
    cust_b = resolve_customer(context_store, "c_vip", merchant_id="m_salon_02")
    assert cust_b is not None
    assert cust_b["identity"]["name"] == "Priya Patel (Salon Client)"
    assert "bridal_makeup" in cust_b["relationship"]["services_received"]
    assert "root_canal" not in cust_b["relationship"]["services_received"]

    # Attempting to resolve Merchant A's customer with Merchant B's ID fails closed
    context_store.set("customer", "c_exclusive_a", 1, {
        "customer_id": "c_exclusive_a",
        "merchant_id": "m_dentist_01",
        "identity": {"name": "Exclusive Patient"},
    })
    # Merchant B tries to resolve Merchant A's customer
    cust_cross = resolve_customer(context_store, "c_exclusive_a", merchant_id="m_salon_02")
    assert cust_cross is None, "Customer belonging to Merchant A must fail closed for Merchant B"


def test_cross_merchant_customer_mismatch_fails_closed():
    """Trigger referencing a customer belonging to a different merchant fails closed."""
    seed_two_distinct_merchants()

    # Customer belongs to Merchant A
    context_store.set("customer", "c_dentist_cust", 1, {
        "customer_id": "c_dentist_cust",
        "merchant_id": "m_dentist_01",
        "identity": {"name": "Amit"},
    })

    # Trigger is for Merchant B (Salon), but references Merchant A's customer
    context_store.set("trigger", "trg_cross_cust", 1, {
        "id": "trg_cross_cust",
        "kind": "recall_due",
        "merchant_id": "m_salon_02",
        "customer_id": "c_dentist_cust",
        "urgency": 3,
        "payload": {},
    })

    rc = resolve_context(context_store, "trg_cross_cust")
    assert rc is not None
    assert rc.metadata.get("cross_merchant_customer_mismatch") is True

    # process_tick must fail closed
    actions = process_tick(
        available_trigger_ids=["trg_cross_cust"],
        now="2026-04-26T10:00:00Z",
        context_store=context_store,
        conversation_store=conversation_store,
    )
    assert actions == [], "Trigger with cross-merchant customer mismatch must fail closed"

    # /v1/reply referencing mismatched customer must also fail closed
    conv_id = "conv_cust_mismatch_test"
    conversation_store.create_or_update(
        conversation_id=conv_id,
        merchant_id="m_salon_02",
        state="waiting_for_reply",
    )
    resp = client.post("/v1/reply", json={
        "conversation_id": conv_id,
        "merchant_id": "m_salon_02",
        "customer_id": "c_dentist_cust",  # belongs to m_dentist_01!
        "message": "When is my appointment?",
        "turn_number": 2,
    })
    assert resp.status_code == 200
    assert resp.json()["action"] == "end"
    assert "Cross-merchant conversation access rejected" in resp.json()["rationale"]


# ===========================================================================
# 3. Offer & Performance Metric Isolation
# ===========================================================================

def test_offer_and_metric_isolation_across_tick_and_reply():
    """Merchant A's compositions never use Merchant B's offers, prices, or performance metrics."""
    seed_two_distinct_merchants()

    context_store.set("trigger", "trg_dentist_perf", 1, {
        "id": "trg_dentist_perf",
        "kind": "performance_drop",
        "merchant_id": "m_dentist_01",
        "urgency": 3,
        "suppression_key": "perf:m_dentist_01:2026-W17",
    })
    context_store.set("trigger", "trg_salon_perf", 1, {
        "id": "trg_salon_perf",
        "kind": "performance_drop",
        "merchant_id": "m_salon_02",
        "urgency": 3,
        "suppression_key": "perf:m_salon_02:2026-W17",
    })

    resp = client.post("/v1/tick", json={
        "now": "2026-04-26T10:00:00Z",
        "available_triggers": ["trg_dentist_perf", "trg_salon_perf"],
    })
    assert resp.status_code == 200
    actions = resp.json().get("actions", [])
    assert len(actions) == 2

    act_dentist = next(a for a in actions if a["merchant_id"] == "m_dentist_01")
    act_salon = next(a for a in actions if a["merchant_id"] == "m_salon_02")

    # Assert Dentist message isolation
    dentist_body = act_dentist["body"]
    assert "Smile Dental" in dentist_body or "Dr. Rohit" in dentist_body
    assert "Glow Luxury" not in dentist_body
    assert "Keratin" not in dentist_body
    assert "₹1299" not in dentist_body
    assert "4,200" not in dentist_body
    assert "4.8%" not in dentist_body

    # Assert Salon message isolation
    salon_body = act_salon["body"]
    assert "Glow Luxury" in salon_body or "Pooja" in salon_body
    assert "Smile Dental" not in salon_body
    assert "Scaling" not in salon_body
    assert "₹499" not in salon_body
    assert "1,500" not in salon_body
    assert "1.9%" not in salon_body


# ===========================================================================
# 4. Conversation & Trigger Cross-Access Isolation
# ===========================================================================

def test_conversation_tampering_and_trigger_cross_access():
    """Merchant B cannot query Merchant A's conversation or trigger."""
    seed_two_distinct_merchants()
    conv_a = "conv_dentist_exclusive"

    conversation_store.create_or_update(
        conversation_id=conv_a,
        merchant_id="m_dentist_01",
        trigger_id="trg_dentist_perf",
        state="waiting_for_reply",
    )
    conversation_store.add_sent_message(
        conv_a,
        {
            "body": "Confidential patient revenue: ₹2,50,000. Clinic Code: DENT_9988.",
            "action": "sent",
            "sent_at": "2026-04-26T10:00:00Z",
        },
    )

    # 1. Merchant B attempts to reply to Conversation A
    r_tamper = client.post("/v1/reply", json={
        "conversation_id": conv_a,
        "merchant_id": "m_salon_02",  # Foreign merchant!
        "message": "What is the clinic revenue code?",
        "turn_number": 2,
    })
    assert r_tamper.status_code == 200
    assert r_tamper.json()["action"] == "end"
    assert "Cross-merchant conversation access rejected" in r_tamper.json()["rationale"]

    # Verify no leak
    assert "DENT_9988" not in str(r_tamper.json())
    assert "₹2,50,000" not in str(r_tamper.json())

    # 2. Merchant B attempts to use Merchant A's trigger in a new conversation
    conv_b = "conv_salon_new"
    context_store.set("trigger", "trg_dentist_only", 1, {
        "id": "trg_dentist_only",
        "kind": "recall_due",
        "merchant_id": "m_dentist_01",  # belongs to dentist!
        "urgency": 3,
    })
    conversation_store.create_or_update(
        conversation_id=conv_b,
        merchant_id="m_salon_02",
        trigger_id="trg_dentist_only",  # foreign trigger!
        state="waiting_for_reply",
    )

    r_trg_cross = client.post("/v1/reply", json={
        "conversation_id": conv_b,
        "merchant_id": "m_salon_02",
        "message": "Can we launch this campaign?",
        "turn_number": 2,
    })
    assert r_trg_cross.status_code == 200
    assert r_trg_cross.json()["action"] == "end"
    assert "Cross-merchant conversation access rejected" in r_trg_cross.json()["rationale"]


# ===========================================================================
# 5. Suppression State Isolation
# ===========================================================================

def test_suppression_state_isolation_between_merchants():
    """Suppressing a trigger for Merchant A does not suppress a trigger for Merchant B."""
    seed_two_distinct_merchants()

    # Triggers with same ID or trigger kind across different merchants
    context_store.set("trigger", "trg_shared_id_a", 1, {
        "id": "trg_shared_id_a",
        "kind": "compliance_alert",
        "merchant_id": "m_dentist_01",
        "urgency": 4,
    })
    context_store.set("trigger", "trg_shared_id_b", 1, {
        "id": "trg_shared_id_b",
        "kind": "compliance_alert",
        "merchant_id": "m_salon_02",
        "urgency": 4,
    })

    now = "2026-04-26T10:00:00Z"

    # Step 1: Tick Merchant A's trigger
    actions1 = process_tick(
        available_trigger_ids=["trg_shared_id_a"],
        now=now,
        context_store=context_store,
        conversation_store=conversation_store,
    )
    assert len(actions1) == 1
    assert actions1[0].merchant_id == "m_dentist_01"

    # Verify Merchant A's trigger is suppressed on replay
    actions1_replay = process_tick(
        available_trigger_ids=["trg_shared_id_a"],
        now=now,
        context_store=context_store,
        conversation_store=conversation_store,
    )
    assert actions1_replay == [], "Merchant A's trigger must be suppressed on immediate replay"

    # Step 2: Tick Merchant B's trigger at the same timestamp
    # Must NOT be suppressed by Merchant A's execution
    actions2 = process_tick(
        available_trigger_ids=["trg_shared_id_b"],
        now=now,
        context_store=context_store,
        conversation_store=conversation_store,
    )
    assert len(actions2) == 1, "Merchant B's trigger must NOT be suppressed by Merchant A"
    assert actions2[0].merchant_id == "m_salon_02"


# ===========================================================================
# 6. Previous Message Isolation
# ===========================================================================

def test_previous_message_isolation_between_merchants():
    """Grounded question answers only pull from the same conversation's previous messages."""
    seed_two_distinct_merchants()
    conv_a = "conv_prev_a"
    conv_b = "conv_prev_b"

    # Conversation A contains previous message about scaling offer
    conversation_store.create_or_update(
        conversation_id=conv_a,
        merchant_id="m_dentist_01",
        state="waiting_for_reply",
    )
    conversation_store.add_sent_message(
        conv_a,
        {
            "body": "Dr. Rohit, we have 4 slots open for Scaling & Polishing @ ₹499 this Friday.",
            "action": "sent",
            "sent_at": "2026-04-26T09:00:00Z",
        },
    )

    # Conversation B contains previous message about keratin offer
    conversation_store.create_or_update(
        conversation_id=conv_b,
        merchant_id="m_salon_02",
        state="waiting_for_reply",
    )
    conversation_store.add_sent_message(
        conv_b,
        {
            "body": "Pooja, Keratin Spa Treatment @ ₹1299 is scheduled for weekend promotion.",
            "action": "sent",
            "sent_at": "2026-04-26T09:00:00Z",
        },
    )

    # Question in Conv A
    resp_a = client.post("/v1/reply", json={
        "conversation_id": conv_a,
        "merchant_id": "m_dentist_01",
        "message": "What did you say earlier about the offer?",
        "turn_number": 2,
    })
    assert resp_a.status_code == 200
    body_a = resp_a.json().get("body", "")
    assert "Scaling" in body_a or "₹499" in body_a or "Smile Dental" in body_a
    assert "Keratin" not in body_a
    assert "₹1299" not in body_a
    assert "Pooja" not in body_a

    # Question in Conv B
    resp_b = client.post("/v1/reply", json={
        "conversation_id": conv_b,
        "merchant_id": "m_salon_02",
        "message": "What did you say earlier about the offer?",
        "turn_number": 2,
    })
    assert resp_b.status_code == 200
    body_b = resp_b.json().get("body", "")
    assert "Keratin" in body_b or "₹1299" in body_b or "Glow Luxury" in body_b
    assert "Scaling" not in body_b
    assert "₹499" not in body_b
    assert "Dr. Rohit" not in body_b


# ===========================================================================
# 7. Simultaneous Multi-Merchant Parallel Conversations
# ===========================================================================

def test_simultaneous_multi_merchant_parallel_conversations():
    """Five distinct merchants running simultaneous multi-turn conversations maintain 100% isolation."""
    merchants = [
        ("m_par_dentist", "dentists", "Dentist Clinic", "Cleaning @ ₹399"),
        ("m_par_salon", "salons", "Luxe Salon", "Blowdry @ ₹299"),
        ("m_par_gym", "gyms", "Iron Gym", "Monthly @ ₹999"),
        ("m_par_rest", "restaurants", "Spice Diner", "Buffet @ ₹599"),
        ("m_par_pharm", "pharmacies", "Health Pharmacy", "Vitamins @ ₹199"),
    ]

    for mid, cat, name, offer in merchants:
        context_store.set("category", cat, 1, {"slug": cat, "display_name": cat.title()})
        context_store.set("merchant", mid, 1, {
            "id": mid,
            "merchant_id": mid,
            "category_slug": cat,
            "identity": {"name": name},
            "offers": [{"title": offer, "status": "active"}],
        })

    def run_merchant_flow(mid, cat, name, offer):
        conv_id = f"conv_par_{mid}"
        conversation_store.create_or_update(conv_id, merchant_id=mid, state="waiting_for_reply")

        # Turn 1: Question
        r1 = client.post("/v1/reply", json={
            "conversation_id": conv_id,
            "merchant_id": mid,
            "message": "What offer do you have for my business?",
            "turn_number": 2,
        })
        assert r1.status_code == 200
        b1 = r1.json()["body"]
        assert offer in b1 or name in b1

        # Turn 2: Positive
        r2 = client.post("/v1/reply", json={
            "conversation_id": conv_id,
            "merchant_id": mid,
            "message": "Yes, proceed with this.",
            "turn_number": 3,
        })
        assert r2.status_code == 200
        assert r2.json()["action"] == "send"
        assert conversation_store.get(conv_id)["state"] == ConversationState.SEND.value

        return conv_id, mid, name, offer, b1

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(run_merchant_flow, *m) for m in merchants]
        results = [f.result() for f in futures]

    for conv_id, mid, name, offer, body in results:
        # Verify no foreign merchant offers or names leaked into this merchant's message
        for other_mid, other_cat, other_name, other_offer in merchants:
            if other_mid != mid:
                assert other_offer not in body, f"Leaked {other_offer} into {mid}"
                assert other_name not in body, f"Leaked {other_name} into {mid}"

        # Verify conversation store integrity
        c_data = conversation_store.get(conv_id)
        assert c_data["merchant_id"] == mid
        assert c_data["state"] == ConversationState.SEND.value
        assert len(c_data["turns"]) == 2


# ===========================================================================
# 8. Ambiguous Relationship Ownership Fails Closed
# ===========================================================================

def test_ambiguous_relationship_ownership_fails_closed():
    """Ambiguous trigger, merchant, or customer ownership must fail closed immediately."""
    # 1. Trigger without merchant_id fails closed in resolve_context
    context_store.set("trigger", "trg_no_merchant", 1, {
        "id": "trg_no_merchant",
        "kind": "recall_due",
        # merchant_id omitted entirely
    })
    rc_no_m = resolve_context(context_store, "trg_no_merchant")
    assert rc_no_m is None, "Trigger without merchant_id must fail closed"

    # 2. Trigger with non-existent merchant_id fails closed
    context_store.set("trigger", "trg_ghost_m", 1, {
        "id": "trg_ghost_m",
        "kind": "recall_due",
        "merchant_id": "m_does_not_exist_in_store",
    })
    rc_ghost = resolve_context(context_store, "trg_ghost_m")
    assert rc_ghost is None, "Trigger with ghost merchant must fail closed"

    # 3. Customer with non-matching merchant_id fails closed
    context_store.set("customer", "c_orphaned", 1, {
        "customer_id": "c_orphaned",
        "merchant_id": "m_original_owner",
        "identity": {"name": "Orphaned Client"},
    })
    resolved_cust = resolve_customer(context_store, "c_orphaned", merchant_id="m_foreign_requester")
    assert resolved_cust is None, "Customer requested by foreign merchant must fail closed"
