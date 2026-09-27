"""Tests for LLM hallucination edge cases and grounding validator fallback.

Deliberately tests that LLM hallucinations (wrong CTR, wrong price, wrong offer name,
invented customer name, two CTAs, empty body, duplicate message, generic filler, zero CTAs)
are caught by the output grounding validator and cleanly trigger Phase 3 deterministic template fallbacks.
"""

import json
import pytest
from fastapi.testclient import TestClient

from app.composer import build_compact_context, compose_message, set_custom_llm_caller
from app.decision_engine import suppression_engine
from app.main import app
from app.output_validator import build_evidence_ledger, validate_message
from app.store import context_store, conversation_store

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_stores():
    """Ensure clean isolated state before and after each test."""
    context_store.clear()
    conversation_store.clear()
    suppression_engine.clear()
    set_custom_llm_caller(None)
    yield
    context_store.clear()
    conversation_store.clear()
    suppression_engine.clear()
    set_custom_llm_caller(None)


def setup_standard_context():
    """Sets up standard category, merchant, and trigger contexts for edge-case testing using real nested schemas."""
    context_store.set(
        "category",
        "dentists",
        1,
        {
            "slug": "dentists",
            "display_name": "Dentists",
            "voice": {
                "tone": "peer_clinical",
                "vocab_allowed": ["fluoride varnish", "caries", "scaling"],
                "taboos": ["guaranteed", "100% safe", "miracle"],
            },
            "peer_stats": {
                "avg_rating": 4.4,
                "avg_reviews": 62,
                "avg_ctr": 0.030,
            },
            "digest": [
                {
                    "id": "d_2026W17_jida_fluoride",
                    "kind": "research",
                    "title": "3-month fluoride varnish recall outperforms 6-month for high-risk adult caries",
                    "source": "JIDA Oct 2026, p.14",
                    "trial_n": 2100,
                    "patient_segment": "high_risk_adults",
                    "summary": "Multi-center Indian trial shows 38% lower caries recurrence with 3-month vs 6-month recall.",
                }
            ],
        },
    )
    context_store.set(
        "merchant",
        "m_meera",
        1,
        {
            "merchant_id": "m_meera",
            "category_slug": "dentists",
            "identity": {
                "name": "Dr. Meera Dental Clinic",
                "city": "Delhi",
                "locality": "Lajpat Nagar",
                "owner_first_name": "Meera",
            },
            "performance": {
                "window_days": 30,
                "views": 2410,
                "calls": 18,
                "directions": 45,
                "ctr": 0.021,
                "delta_7d": {"views_pct": 0.18, "calls_pct": -0.05, "ctr_pct": 0.02},
            },
            "offers": [
                {"id": "o_1", "title": "Dental Cleaning @ ₹299", "status": "active"},
                {"id": "o_2", "title": "Deep Cleaning @ ₹499", "status": "expired"},
            ],
            "customer_aggregate": {
                "total_unique_ytd": 540,
                "lapsed_180d_plus": 78,
                "retention_6mo_pct": 0.38,
                "high_risk_adult_count": 124,
            },
            "signals": ["stale_posts:22d", "ctr_below_peer_median", "high_risk_adult_cohort"],
        },
    )
    context_store.set(
        "trigger",
        "trg_perf_01",
        1,
        {
            "id": "trg_perf_01",
            "scope": "merchant",
            "kind": "performance_drop",
            "merchant_id": "m_meera",
            "urgency": 3,
            "payload": {"metric": "ctr", "delta_pct": -0.30},
        },
    )


# ---------------------------------------------------------------------------
# 1. Hallucinated / Wrong CTR
# ---------------------------------------------------------------------------
def test_hallucinated_wrong_ctr_triggers_fallback():
    """LLM reports an ungrounded CTR (e.g. 5.8% when reality is 2.1%). Validator must reject and fallback fires."""
    setup_standard_context()

    # Direct ledger validation test
    compact_ctx = {
        "merchant": {
            "name": "Dr. Meera Dental Clinic",
            "performance": {"ctr": 0.021},
            "active_offers": ["Dental Cleaning @ ₹299"],
        },
        "category": {"name": "Dentists", "peer_avg_ctr": 0.030},
        "trigger": {"kind": "performance_drop", "details": {"delta_pct": -0.30}},
    }
    ledger = build_evidence_ledger(compact_ctx)
    hallucinated_body = "Dr. Meera Dental Clinic, your CTR is 5.8% this week. Want to highlight Dental Cleaning @ ₹299?"
    is_valid, reason = validate_message(hallucinated_body, ledger)
    assert not is_valid
    assert "Unverified numeric claim '5.8'" in reason

    # End-to-end /v1/tick test
    llm_payload = {
        "body": hallucinated_body,
        "cta": "open_ended",
        "rationale": "Hallucinating wrong CTR number",
    }
    set_custom_llm_caller(lambda p, s, t: json.dumps(llm_payload))

    resp = client.post("/v1/tick", json={"now": "2026-04-26T10:00:00Z", "available_triggers": ["trg_perf_01"]})
    assert resp.status_code == 200
    actions = resp.json().get("actions", [])
    assert len(actions) == 1
    # Fallback template fired
    assert actions[0]["template_name"] == "template_performance_drop_v1"
    assert "2.1%" in actions[0]["body"]
    assert "5.8%" not in actions[0]["body"]


# ---------------------------------------------------------------------------
# 2. Hallucinated / Wrong Price
# ---------------------------------------------------------------------------
def test_hallucinated_wrong_price_triggers_fallback():
    """LLM substitutes an invented discount price (₹99 instead of active ₹299). Validator must reject and fallback fires."""
    setup_standard_context()

    compact_ctx = {
        "merchant": {
            "name": "Dr. Meera Dental Clinic",
            "performance": {"ctr": 0.021},
            "active_offers": ["Dental Cleaning @ ₹299"],
        },
        "category": {"name": "Dentists", "peer_avg_ctr": 0.030},
    }
    ledger = build_evidence_ledger(compact_ctx)
    hallucinated_body = "Dr. Meera Dental Clinic, your CTR is 2.1%. Want to highlight Dental Cleaning @ ₹99?"
    is_valid, reason = validate_message(hallucinated_body, ledger)
    assert not is_valid
    assert "Unverified numeric claim '99'" in reason

    # End-to-end /v1/tick test
    llm_payload = {
        "body": hallucinated_body,
        "cta": "open_ended",
        "rationale": "Inventing wrong price ₹99",
    }
    set_custom_llm_caller(lambda p, s, t: json.dumps(llm_payload))

    resp = client.post("/v1/tick", json={"now": "2026-04-26T10:00:00Z", "available_triggers": ["trg_perf_01"]})
    assert resp.status_code == 200
    actions = resp.json().get("actions", [])
    assert len(actions) == 1
    assert actions[0]["template_name"] == "template_performance_drop_v1"
    assert "₹299" in actions[0]["body"]
    assert "₹99" not in actions[0]["body"]


# ---------------------------------------------------------------------------
# 3. Hallucinated / Wrong Offer Name
# ---------------------------------------------------------------------------
def test_hallucinated_wrong_offer_name_triggers_fallback():
    """LLM invents an offer service ('Teeth Whitening' / 'Root Canal') not present in active_offers. Validator rejects and fallback fires."""
    setup_standard_context()

    compact_ctx = {
        "merchant": {
            "name": "Dr. Meera Dental Clinic",
            "performance": {"ctr": 0.021},
            "active_offers": ["Dental Cleaning @ ₹299"],
        },
        "category": {"name": "Dentists"},
    }
    ledger = build_evidence_ledger(compact_ctx)

    # Invented procedure "Teeth Whitening"
    hallucinated_body = "Dr. Meera Dental Clinic, your CTR is 2.1%. Want to promote our Teeth Whitening package for ₹299?"
    is_valid, reason = validate_message(hallucinated_body, ledger)
    assert not is_valid
    assert "references an offer or service not present in active_offers" in reason

    # End-to-end /v1/tick test
    llm_payload = {
        "body": hallucinated_body,
        "cta": "open_ended",
        "rationale": "Substituted wrong offer Teeth Whitening",
    }
    set_custom_llm_caller(lambda p, s, t: json.dumps(llm_payload))

    resp = client.post("/v1/tick", json={"now": "2026-04-26T10:00:00Z", "available_triggers": ["trg_perf_01"]})
    assert resp.status_code == 200
    actions = resp.json().get("actions", [])
    assert len(actions) == 1
    assert actions[0]["template_name"] == "template_performance_drop_v1"
    assert "Dental Cleaning @ ₹299" in actions[0]["body"]
    assert "Whitening" not in actions[0]["body"]


# ---------------------------------------------------------------------------
# 4. Invented Customer Name
# ---------------------------------------------------------------------------
def test_invented_customer_name_triggers_fallback():
    """LLM introduces an ungrounded third-party / customer name ('Rahul') when none exists in context. Validator rejects and fallback fires."""
    setup_standard_context()

    compact_ctx = {
        "merchant": {
            "name": "Dr. Meera Dental Clinic",
            "performance": {"ctr": 0.021},
            "active_offers": ["Dental Cleaning @ ₹299"],
        },
        "category": {"name": "Dentists"},
        "customer": None,  # No customer in context
    }
    ledger = build_evidence_ledger(compact_ctx)
    hallucinated_body = "Hi Rahul, Dr. Meera Dental Clinic noticed your CTR is 2.1%. Want to book Dental Cleaning @ ₹299?"
    is_valid, reason = validate_message(hallucinated_body, ledger)
    assert not is_valid
    assert "Invented customer or third-party name 'rahul'" in reason

    # End-to-end /v1/tick test
    llm_payload = {
        "body": hallucinated_body,
        "cta": "open_ended",
        "rationale": "Inventing customer name Rahul",
    }
    set_custom_llm_caller(lambda p, s, t: json.dumps(llm_payload))

    resp = client.post("/v1/tick", json={"now": "2026-04-26T10:00:00Z", "available_triggers": ["trg_perf_01"]})
    assert resp.status_code == 200
    actions = resp.json().get("actions", [])
    assert len(actions) == 1
    assert actions[0]["template_name"] == "template_performance_drop_v1"
    assert "Rahul" not in actions[0]["body"]


# ---------------------------------------------------------------------------
# 5. Multiple CTAs (Two Calls to Action)
# ---------------------------------------------------------------------------
def test_multiple_ctas_triggers_fallback():
    """LLM includes more than one CTA in the body (e.g. 2 questions). Validator enforces exactly one CTA and fallback fires."""
    setup_standard_context()

    compact_ctx = {
        "merchant": {
            "name": "Dr. Meera Dental Clinic",
            "performance": {"ctr": 0.021},
            "active_offers": ["Dental Cleaning @ ₹299"],
        },
        "category": {"name": "Dentists"},
    }
    ledger = build_evidence_ledger(compact_ctx)
    multi_cta_body = "Dr. Meera Dental Clinic, your CTR is 2.1%. Want to promote Dental Cleaning @ ₹299? Can we schedule a quick call?"
    is_valid, reason = validate_message(multi_cta_body, ledger)
    assert not is_valid
    assert "found 2" in reason

    # End-to-end /v1/tick test
    llm_payload = {
        "body": multi_cta_body,
        "cta": "open_ended",
        "rationale": "Hallucinating two questions / CTAs",
    }
    set_custom_llm_caller(lambda p, s, t: json.dumps(llm_payload))

    resp = client.post("/v1/tick", json={"now": "2026-04-26T10:00:00Z", "available_triggers": ["trg_perf_01"]})
    assert resp.status_code == 200
    actions = resp.json().get("actions", [])
    assert len(actions) == 1
    assert actions[0]["template_name"] == "template_performance_drop_v1"


# ---------------------------------------------------------------------------
# 6. Empty Body
# ---------------------------------------------------------------------------
def test_empty_body_triggers_fallback():
    """LLM returns an empty string or whitespace body. Validator catches it and fallback fires."""
    setup_standard_context()

    compact_ctx = {"merchant": {"name": "Dr. Meera Dental Clinic"}}
    ledger = build_evidence_ledger(compact_ctx)
    is_valid, reason = validate_message("   ", ledger)
    assert not is_valid
    assert "cannot be empty" in reason

    # End-to-end /v1/tick test
    llm_payload = {
        "body": "",
        "cta": "open_ended",
        "rationale": "Empty message body",
    }
    set_custom_llm_caller(lambda p, s, t: json.dumps(llm_payload))

    resp = client.post("/v1/tick", json={"now": "2026-04-26T10:00:00Z", "available_triggers": ["trg_perf_01"]})
    assert resp.status_code == 200
    actions = resp.json().get("actions", [])
    assert len(actions) == 1
    assert actions[0]["template_name"] == "template_performance_drop_v1"
    assert len(actions[0]["body"]) > 0


# ---------------------------------------------------------------------------
# 7. Duplicate of a Previous Message (SHA-256 Check)
# ---------------------------------------------------------------------------
def test_duplicate_message_triggers_fallback():
    """LLM attempts to send a message identical to one previously sent in this conversation. Validator detects sha256 hash match and fallback fires."""
    setup_standard_context()

    conv_id = "conv_test_dup"
    original_body = "Dr. Meera Dental Clinic, your CTR is 2.1%. Want to highlight Dental Cleaning @ ₹299?"
    conversation_store.create_or_update(
        conversation_id=conv_id,
        merchant_id="m_meera",
        trigger_id="trg_perf_01",
    )
    conversation_store.add_sent_message(conv_id, {"body": original_body, "template_name": "llm_grounded_composer"})

    compact_ctx = {
        "merchant": {
            "name": "Dr. Meera Dental Clinic",
            "performance": {"ctr": 0.021},
            "active_offers": ["Dental Cleaning @ ₹299"],
        },
        "category": {"name": "Dentists"},
    }
    ledger = build_evidence_ledger(compact_ctx)

    # Identical message with slightly altered whitespace/casing
    duplicate_candidate = "  dr.  meera dental clinic, your ctr is 2.1%. want to highlight dental cleaning @ ₹299?  "
    is_valid, reason = validate_message(
        duplicate_candidate,
        ledger,
        conversation_store=conversation_store,
        conversation_id=conv_id,
    )
    assert not is_valid
    assert "duplicate of a previously sent message" in reason

    # Test via compose_message
    fallback_tuple = ("Deterministic fallback", "template_performance_drop_v1", [], "Fallback reason")
    llm_payload = {
        "body": original_body,
        "cta": "open_ended",
        "rationale": "Duplicate replay",
    }
    set_custom_llm_caller(lambda p, s, t: json.dumps(llm_payload))

    body, tmpl, params, rationale = compose_message(
        compact_context=compact_ctx,
        fallback_data=fallback_tuple,
        conversation_store=conversation_store,
        conversation_id=conv_id,
    )
    assert body == "Deterministic fallback"
    assert tmpl == "template_performance_drop_v1"


# ---------------------------------------------------------------------------
# 8. Generic Filler Without Facts
# ---------------------------------------------------------------------------
def test_generic_filler_triggers_fallback():
    """LLM returns generic filler with no grounded facts from the ledger. Validator rejects and fallback fires."""
    setup_standard_context()

    compact_ctx = {
        "merchant": {
            "name": "Dr. Meera Dental Clinic",
            "performance": {"ctr": 0.021},
            "active_offers": ["Dental Cleaning @ ₹299"],
        },
        "category": {"name": "Dentists"},
    }
    ledger = build_evidence_ledger(compact_ctx)
    generic_body = "Hello! We noticed some recent trends on your account. Would you like to review new opportunities?"
    is_valid, reason = validate_message(generic_body, ledger)
    assert not is_valid
    assert "generic filler" in reason

    # End-to-end /v1/tick test
    llm_payload = {
        "body": generic_body,
        "cta": "open_ended",
        "rationale": "Pure generic filler",
    }
    set_custom_llm_caller(lambda p, s, t: json.dumps(llm_payload))

    resp = client.post("/v1/tick", json={"now": "2026-04-26T10:00:00Z", "available_triggers": ["trg_perf_01"]})
    assert resp.status_code == 200
    actions = resp.json().get("actions", [])
    assert len(actions) == 1
    assert actions[0]["template_name"] == "template_performance_drop_v1"


# ---------------------------------------------------------------------------
# 9. Zero CTAs
# ---------------------------------------------------------------------------
def test_zero_ctas_triggers_fallback():
    """LLM returns a statement with zero CTAs. Validator rejects and fallback fires."""
    setup_standard_context()

    compact_ctx = {
        "merchant": {
            "name": "Dr. Meera Dental Clinic",
            "performance": {"ctr": 0.021},
            "active_offers": ["Dental Cleaning @ ₹299"],
        },
        "category": {"name": "Dentists"},
    }
    ledger = build_evidence_ledger(compact_ctx)
    no_cta_body = "Dr. Meera Dental Clinic, your CTR is 2.1% this week compared to category peers."
    is_valid, reason = validate_message(no_cta_body, ledger)
    assert not is_valid
    assert "none found" in reason

    # End-to-end /v1/tick test
    llm_payload = {
        "body": no_cta_body,
        "cta": "statement",
        "rationale": "Missing CTA",
    }
    set_custom_llm_caller(lambda p, s, t: json.dumps(llm_payload))

    resp = client.post("/v1/tick", json={"now": "2026-04-26T10:00:00Z", "available_triggers": ["trg_perf_01"]})
    assert resp.status_code == 200
    actions = resp.json().get("actions", [])
    assert len(actions) == 1
    assert actions[0]["template_name"] == "template_performance_drop_v1"


# ---------------------------------------------------------------------------
# 10. Valid Fully-Grounded Output Accepted
# ---------------------------------------------------------------------------
def test_valid_fully_grounded_message_accepted():
    """When LLM adheres strictly to the evidence ledger, output is accepted and no fallback occurs."""
    setup_standard_context()

    valid_body = "Dr. Meera Dental Clinic, your CTR is 2.1% vs 3.0% for Dentists peers. Want to highlight your Dental Cleaning @ ₹299 offer?"

    compact_ctx = {
        "merchant": {
            "name": "Dr. Meera Dental Clinic",
            "performance": {"ctr": 0.021},
            "active_offers": ["Dental Cleaning @ ₹299"],
        },
        "category": {"name": "Dentists", "peer_avg_ctr": 0.030},
        "allowed_facts": ["Dr. Meera Dental Clinic", "CTR is 2.1%"],
    }
    ledger = build_evidence_ledger(compact_ctx)
    is_valid, reason = validate_message(valid_body, ledger)
    assert is_valid
    assert reason is None

    # End-to-end /v1/tick test
    llm_payload = {
        "body": valid_body,
        "cta": "open_ended",
        "rationale": "Grounded in CTR and active offer",
    }
    set_custom_llm_caller(lambda p, s, t: json.dumps(llm_payload))

    resp = client.post("/v1/tick", json={"now": "2026-04-26T10:00:00Z", "available_triggers": ["trg_perf_01"]})
    assert resp.status_code == 200
    actions = resp.json().get("actions", [])
    assert len(actions) == 1
    assert actions[0]["template_name"] == "llm_grounded_composer"
    assert actions[0]["body"] == valid_body


# ---------------------------------------------------------------------------
# 11. Unicode and Hindi/English Mixed Text
# ---------------------------------------------------------------------------
def test_unicode_and_hindi_english_mixed_text():
    """
    Unicode and Hindi/English mixed text in merchant name, customer name, and reply message
    -> confirm no crash, no mangled output, no false-positive grounding rejection.
    """
    m_name = "डॉ. मेहरा Dental Care"
    cust_name = "राहुल शर्मा"
    offer_name = "दांतों की सफाई @ ₹299"

    context_store.set(
        "category",
        "dentists",
        1,
        {"slug": "dentists", "display_name": "दंत चिकित्सक (Dentists)"},
    )
    context_store.set(
        "merchant",
        "m_hindi_01",
        1,
        {
            "merchant_id": "m_hindi_01",
            "category_slug": "dentists",
            "identity": {"name": m_name, "city": "नई दिल्ली (New Delhi)"},
            "performance": {"ctr": 0.021},
            "offers": [{"title": offer_name, "status": "active"}],
        },
    )
    context_store.set(
        "customer",
        "c_hindi_01",
        1,
        {
            "customer_id": "c_hindi_01",
            "identity": {"name": cust_name},
        },
    )
    context_store.set(
        "trigger",
        "trg_hindi_01",
        1,
        {
            "id": "trg_hindi_01",
            "kind": "recall_due",
            "merchant_id": "m_hindi_01",
            "customer_id": "c_hindi_01",
            "urgency": 3,
            "payload": {"service_due": "दांतों की सफाई"},
        },
    )

    # 1. Tick test with Hindi-English mixed text
    resp_tick = client.post(
        "/v1/tick",
        json={"now": "2026-04-26T10:00:00Z", "available_triggers": ["trg_hindi_01"]},
    )
    assert resp_tick.status_code == 200
    actions = resp_tick.json().get("actions", [])
    assert len(actions) == 1
    action_body = actions[0]["body"]

    # Verify no mangled characters and names preserved
    assert m_name in action_body
    assert cust_name in action_body
    assert "दांतों की सफाई" in action_body

    # 2. Reply test with Hindi-English mixed reply
    conv_id = actions[0]["conversation_id"]
    resp_reply = client.post(
        "/v1/reply",
        json={
            "conversation_id": conv_id,
            "merchant_id": "m_hindi_01",
            "customer_id": "c_hindi_01",
            "from_role": "merchant",
            "message": "हाँजी बिल्कुल! Please send appointment slots.",
            "turn_number": 2,
        },
    )
    assert resp_reply.status_code == 200
    assert resp_reply.json()["action"] == "send"
    assert conversation_store.get(conv_id)["state"] == "SEND"


# ---------------------------------------------------------------------------
# 12. Very Long Text Fields Near Payload Limits
# ---------------------------------------------------------------------------
def test_very_long_text_fields_near_payload_limit():
    """
    Very long text fields (near payload limit)
    -> confirm graceful handling, not an unhandled 500 error.
    """
    # 20KB long description string
    long_desc = "Excellent premium service offering with high quality clinical care. " * 300
    long_merchant = {
        "merchant_id": "m_long_01",
        "category_slug": "dentists",
        "identity": {"name": "Apex Long Name Hospital", "description": long_desc},
        "performance": {"ctr": 0.021},
        "offers": [{"title": f"Offer {i} @ ₹{299 + i}", "status": "active"} for i in range(50)],
    }

    resp = client.post(
        "/v1/context",
        json={
            "scope": "merchant",
            "context_id": "m_long_01",
            "version": 1,
            "payload": long_merchant,
        },
    )
    assert resp.status_code == 200
    assert resp.json()["accepted"] is True

    # Long reply message (5,000 characters)
    long_reply = "Yes we are interested in expanding this offer to our customers. " * 80
    conv_id = "conv_long_reply"
    conversation_store.create_or_update(conv_id, merchant_id="m_long_01", state="waiting_for_reply")

    resp_reply = client.post(
        "/v1/reply",
        json={
            "conversation_id": conv_id,
            "merchant_id": "m_long_01",
            "from_role": "merchant",
            "message": long_reply,
            "turn_number": 2,
        },
    )
    assert resp_reply.status_code == 200
    assert resp_reply.json()["action"] == "send"


# ---------------------------------------------------------------------------
# 13. Null and Missing Optional Fields Across Endpoints
# ---------------------------------------------------------------------------
def test_null_and_missing_optional_fields_across_endpoints():
    """
    Null/missing optional fields across /v1/context, /v1/tick, /v1/reply
    -> confirm no crash.
    """
    # /v1/context: delivered_at omitted
    resp_ctx = client.post(
        "/v1/context",
        json={
            "scope": "category",
            "context_id": "dentists",
            "version": 1,
            "payload": {"slug": "dentists", "display_name": "Dentists"},
            # delivered_at omitted
        },
    )
    assert resp_ctx.status_code == 200

    # /v1/tick: now omitted (defaults to UTC current time)
    resp_tick = client.post(
        "/v1/tick",
        json={
            "available_triggers": [],
            # now omitted
        },
    )
    assert resp_tick.status_code == 200
    assert resp_tick.json()["actions"] == []

    # /v1/reply: customer_id is null / omitted, received_at omitted
    conv_id = "conv_optional_test"
    conversation_store.create_or_update(conv_id, merchant_id="m_001", state="waiting_for_reply")
    resp_reply = client.post(
        "/v1/reply",
        json={
            "conversation_id": conv_id,
            "merchant_id": "m_001",
            "customer_id": None,
            "from_role": "merchant",
            "message": "Yes please do.",
            "turn_number": 2,
            # received_at omitted
        },
    )
    assert resp_reply.status_code == 200
    assert resp_reply.json()["action"] == "send"


# ---------------------------------------------------------------------------
# Real Nested Schema Fact Grounding Tests
# ---------------------------------------------------------------------------

def test_nested_performance_delta_7d_grounding():
    """Verify delta_7d numbers (+18.0% views) are accepted, but invented numbers are rejected."""
    setup_standard_context()
    merchant = context_store.get("merchant", "m_meera")
    category = context_store.get("category", "dentists")
    trigger = context_store.get("trigger", "trg_perf_01")

    compact_ctx = build_compact_context(merchant, category, trigger)
    ledger = build_evidence_ledger(compact_ctx)

    # Valid grounded message citing real 18% views growth
    grounded_msg = "Dr. Meera Dental Clinic, your 7-day views are up 18%. Want to promote Dental Cleaning @ ₹299?"
    valid, reason = validate_message(grounded_msg, ledger)
    assert valid, f"Expected valid, got: {reason}"

    # Ungrounded message citing invented 73% views growth
    hallucinated_msg = "Dr. Meera Dental Clinic, your 7-day views jumped 73%. Want to promote Dental Cleaning @ ₹299?"
    valid, reason = validate_message(hallucinated_msg, ledger)
    assert not valid
    assert "Unverified numeric claim '73'" in reason


def test_nested_customer_aggregate_grounding():
    """Verify customer_aggregate numbers (124 high-risk patients) are grounded and invented counts rejected."""
    setup_standard_context()
    merchant = context_store.get("merchant", "m_meera")
    category = context_store.get("category", "dentists")
    trigger = context_store.get("trigger", "trg_perf_01")

    compact_ctx = build_compact_context(merchant, category, trigger)
    ledger = build_evidence_ledger(compact_ctx)

    # Valid: 124 patients in cohort
    grounded_msg = "Dr. Meera Dental Clinic, you have 124 high-risk adult patients. Should we review your recall schedule?"
    valid, reason = validate_message(grounded_msg, ledger)
    assert valid, f"Expected valid, got: {reason}"

    # Invalid: fabricated 450 patients
    hallucinated_msg = "Dr. Meera Dental Clinic, you have 450 high-risk adult patients. Should we review your recall schedule?"
    valid, reason = validate_message(hallucinated_msg, ledger)
    assert not valid
    assert "Unverified numeric claim '450'" in reason


def test_nested_digest_trial_n_and_source_grounding():
    """Verify digest citations (2,100 patients, JIDA Oct 2026, p.14, 38%) are grounded."""
    setup_standard_context()
    merchant = context_store.get("merchant", "m_meera")
    category = context_store.get("category", "dentists")
    trigger = {
        "id": "trg_digest_01",
        "scope": "merchant",
        "kind": "research_digest",
        "merchant_id": "m_meera",
        "urgency": 2,
        "payload": {"category": "dentists", "top_item_id": "d_2026W17_jida_fluoride"},
    }

    compact_ctx = build_compact_context(merchant, category, trigger)
    ledger = build_evidence_ledger(compact_ctx)

    # Valid message using real trial size and source citation
    grounded_digest_msg = (
        "Dr. Meera, JIDA Oct 2026, p.14 published a 2,100-patient trial showing 3-month fluoride recall "
        "cuts caries 38% better. Want me to draft a patient-ed WhatsApp you can share?"
    )
    valid, reason = validate_message(grounded_digest_msg, ledger)
    assert valid, f"Expected valid, got: {reason}"

    # Hallucinated trial size (e.g. 8,500 patients)
    fake_trial_msg = (
        "Dr. Meera, JIDA Oct 2026, p.14 published an 8,500-patient trial showing 3-month fluoride recall "
        "cuts caries 38% better. Want me to draft a patient-ed WhatsApp you can share?"
    )
    valid, reason = validate_message(fake_trial_msg, ledger)
    assert not valid
    assert "Unverified numeric claim '8,500'" in reason or "Unverified numeric claim '8500'" in reason

