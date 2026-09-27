"""Adversarial and boundary tests for output_validator.py hardening.

Tests rejection of:
- Hallucinated numbers (4, 7.2%, ₹99, etc.)
- Invented customer and doctor names
- Inactive/expired offers
- Unsupported dates, months, and years
- Unsupported locations/cities
- Prohibited claims and taboos
- Unauthorized numeric structures (4-step, top 5)

Tests acceptance of:
- Valid grounded messages adhering strictly to context
- Structurally justified tokens (1-page, 3-step, top 3, 2-min, 1 question)
- Legitimate grounded numbers when explicitly present in evidence
"""

import json
import pytest
from fastapi.testclient import TestClient

from app.composer import set_custom_llm_caller
from app.main import app
from app.output_validator import build_evidence_ledger, validate_message
from app.store import context_store, conversation_store

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_state():
    """Reset store state before and after each test."""
    context_store.clear()
    conversation_store.clear()
    set_custom_llm_caller(None)
    yield
    context_store.clear()
    conversation_store.clear()
    set_custom_llm_caller(None)


def setup_test_context():
    """Seed standard test merchant, category, and trigger context."""
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
                "taboos": ["guaranteed", "100% safe", "miracle", "permanent cure"],
            },
            "peer_stats": {
                "avg_rating": 4.5,
                "avg_reviews": 60,
                "avg_ctr": 0.030,
            },
            "digest": [
                {
                    "id": "d_jida_oct_2026",
                    "title": "3-month fluoride varnish recall outperforms 6-month for high-risk adult caries",
                    "source": "JIDA Oct 2026, p.14",
                    "trial_n": 2100,
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
                "ctr": 0.021,
                "views": 2400,
                "delta_7d": {"views_pct": 0.18, "calls_pct": -0.05, "ctr_pct": 0.02},
            },
            "offers": [
                {"id": "o_1", "title": "Dental Cleaning @ ₹299", "status": "active"},
                {"id": "o_2", "title": "Deep Cleaning @ ₹499", "status": "expired"},
            ],
            "customer_aggregate": {
                "high_risk_adult_count": 124,
            },
        },
    )
    context_store.set(
        "trigger",
        "trg_perf_01",
        1,
        {
            "id": "trg_perf_01",
            "kind": "performance_drop",
            "merchant_id": "m_meera",
            "urgency": 3,
            "payload": {"metric": "ctr", "delta_pct": -0.30},
        },
    )


# ---------------------------------------------------------------------------
# 1. Hallucinated Numbers (4, 7.2%, ₹99)
# ---------------------------------------------------------------------------

def test_reject_hallucinated_number_4():
    """Arbitrary number 4 must be rejected when not in context and not structural."""
    setup_test_context()
    merchant = context_store.get("merchant", "m_meera")["payload"]
    category = context_store.get("category", "dentists")["payload"]
    trigger = context_store.get("trigger", "trg_perf_01")["payload"]

    compact_ctx = {
        "merchant": merchant,
        "category": category,
        "trigger": trigger,
    }
    ledger = build_evidence_ledger(compact_ctx)

    # Hallucinating 4 patient inquiries
    hallucinated_body = "Dr. Meera Dental Clinic, you have 4 new patient inquiries this week. Want to highlight Dental Cleaning @ ₹299?"
    is_valid, reason = validate_message(hallucinated_body, ledger)
    assert not is_valid
    assert "Unverified numeric claim '4'" in reason


def test_reject_hallucinated_percentage_7_point_2():
    """Hallucinated percentage 7.2% must be rejected when not in evidence."""
    setup_test_context()
    merchant = context_store.get("merchant", "m_meera")["payload"]
    category = context_store.get("category", "dentists")["payload"]
    trigger = context_store.get("trigger", "trg_perf_01")["payload"]

    compact_ctx = {"merchant": merchant, "category": category, "trigger": trigger}
    ledger = build_evidence_ledger(compact_ctx)

    hallucinated_body = "Dr. Meera Dental Clinic, your CTR jumped 7.2% this week. Want to highlight Dental Cleaning @ ₹299?"
    is_valid, reason = validate_message(hallucinated_body, ledger)
    assert not is_valid
    assert "Unverified numeric claim '7.2'" in reason


def test_reject_hallucinated_price_99():
    """Hallucinated price ₹99 must be rejected when active offer is ₹299."""
    setup_test_context()
    merchant = context_store.get("merchant", "m_meera")["payload"]
    category = context_store.get("category", "dentists")["payload"]
    trigger = context_store.get("trigger", "trg_perf_01")["payload"]

    compact_ctx = {"merchant": merchant, "category": category, "trigger": trigger}
    ledger = build_evidence_ledger(compact_ctx)

    hallucinated_body = "Dr. Meera Dental Clinic, your CTR is 2.1%. Want to highlight Dental Cleaning @ ₹99?"
    is_valid, reason = validate_message(hallucinated_body, ledger)
    assert not is_valid
    assert "Unverified numeric claim '99'" in reason


# ---------------------------------------------------------------------------
# 2. Invented Customer and Doctor Names
# ---------------------------------------------------------------------------

def test_reject_invented_salutation_and_doctor_names():
    """Invented names in salutations or body text must be rejected."""
    setup_test_context()
    merchant = context_store.get("merchant", "m_meera")["payload"]
    category = context_store.get("category", "dentists")["payload"]

    compact_ctx = {"merchant": merchant, "category": category, "customer": None}
    ledger = build_evidence_ledger(compact_ctx)

    # 1. Salutation with invented customer name
    body_cust = "Hi Rahul, Dr. Meera Dental Clinic noticed your CTR is 2.1%. Want to book Dental Cleaning @ ₹299?"
    is_valid, reason = validate_message(body_cust, ledger)
    assert not is_valid
    assert "Invented customer or third-party name 'rahul'" in reason

    # 2. Salutation addressing wrong doctor
    body_doc = "Dear Dr. Sharma, your CTR is 2.1%. Want to highlight Dental Cleaning @ ₹299?"
    is_valid, reason = validate_message(body_doc, ledger)
    assert not is_valid
    assert "sharma" in reason.lower()

    # 3. Third-party doctor invented in body text
    body_third_party = "Dr. Meera Dental Clinic, Dr. Gupta recommended reviewing your 2.1% CTR. Want to promote Dental Cleaning @ ₹299?"
    is_valid, reason = validate_message(body_third_party, ledger)
    assert not is_valid
    assert "gupta" in reason.lower()


# ---------------------------------------------------------------------------
# 3. Inactive and Expired Offers
# ---------------------------------------------------------------------------

def test_reject_inactive_and_expired_offers():
    """Messages referencing inactive/expired offers must be rejected."""
    setup_test_context()
    merchant = context_store.get("merchant", "m_meera")["payload"]
    category = context_store.get("category", "dentists")["payload"]

    compact_ctx = {"merchant": merchant, "category": category}
    ledger = build_evidence_ledger(compact_ctx)

    # Offer o_2 'Deep Cleaning @ ₹499' is expired in merchant context
    body_inactive = "Dr. Meera Dental Clinic, your CTR is 2.1%. Want to highlight Deep Cleaning @ ₹499?"
    is_valid, reason = validate_message(body_inactive, ledger)
    assert not is_valid
    assert "inactive or expired offer" in reason

    # Even without the price, mentioning the expired offer title is rejected
    body_inactive_no_price = "Dr. Meera Dental Clinic, your CTR is 2.1%. Want to highlight our Deep Cleaning package?"
    is_valid, reason = validate_message(body_inactive_no_price, ledger)
    assert not is_valid
    assert "inactive or expired offer" in reason


# ---------------------------------------------------------------------------
# 4. Unsupported Dates, Months, and Years
# ---------------------------------------------------------------------------

def test_reject_unsupported_dates_and_months():
    """Hallucinated months or unsupported years must be rejected."""
    setup_test_context()
    merchant = context_store.get("merchant", "m_meera")["payload"]
    category = context_store.get("category", "dentists")["payload"]

    compact_ctx = {"merchant": merchant, "category": category}
    ledger = build_evidence_ledger(compact_ctx)

    # Hallucinating December expiration date
    body_month = "Dr. Meera Dental Clinic, your offer expires on December 25th. Want to promote Dental Cleaning @ ₹299?"
    is_valid, reason = validate_message(body_month, ledger)
    assert not is_valid
    assert "Unsupported date or month claim 'december'" in reason

    # Hallucinating year 2029
    body_year = "Dr. Meera Dental Clinic, published in JIDA Oct 2029: 3-month fluoride recall cuts caries. Want to promote Dental Cleaning @ ₹299?"
    is_valid, reason = validate_message(body_year, ledger)
    assert not is_valid
    assert "Unsupported year '2029'" in reason


# ---------------------------------------------------------------------------
# 5. Unsupported Locations and Cities
# ---------------------------------------------------------------------------

def test_reject_unsupported_locations():
    """Hallucinated cities not in merchant context must be rejected."""
    setup_test_context()
    merchant = context_store.get("merchant", "m_meera")["payload"]
    category = context_store.get("category", "dentists")["payload"]

    compact_ctx = {"merchant": merchant, "category": category}
    ledger = build_evidence_ledger(compact_ctx)

    # Merchant is in Delhi / Lajpat Nagar. Hallucinating Mumbai or Pune:
    body_mumbai = "Dr. Meera Dental Clinic, heatwave alert in Mumbai today. Want to promote Dental Cleaning @ ₹299?"
    is_valid, reason = validate_message(body_mumbai, ledger)
    assert not is_valid
    assert "Unsupported location or city 'mumbai'" in reason

    body_pune = "Dr. Meera Dental Clinic, for your clinic in Pune, CTR is 2.1%. Want to promote Dental Cleaning @ ₹299?"
    is_valid, reason = validate_message(body_pune, ledger)
    assert not is_valid
    assert "Unsupported location or city 'pune'" in reason


# ---------------------------------------------------------------------------
# 6. Prohibited Claims and Taboos
# ---------------------------------------------------------------------------

def test_reject_unsupported_claims_and_taboos():
    """Deceptive claims like 'guaranteed', '100% safe', 'miracle' must be rejected."""
    setup_test_context()
    merchant = context_store.get("merchant", "m_meera")["payload"]
    category = context_store.get("category", "dentists")["payload"]

    compact_ctx = {"merchant": merchant, "category": category}
    ledger = build_evidence_ledger(compact_ctx)

    body_guaranteed = "Dr. Meera Dental Clinic, get guaranteed 100% safe Dental Cleaning @ ₹299. Want to book now?"
    is_valid, reason = validate_message(body_guaranteed, ledger)
    assert not is_valid
    assert "prohibited claim or taboo" in reason

    body_miracle = "Dr. Meera Dental Clinic, miracle cure for sensitive teeth with Dental Cleaning @ ₹299. Want to book now?"
    is_valid, reason = validate_message(body_miracle, ledger)
    assert not is_valid
    assert "prohibited claim or taboo 'miracle'" in reason


# ---------------------------------------------------------------------------
# 7. Preservation of Valid Grounded Messages with Structural Language Tokens
# ---------------------------------------------------------------------------

def test_preserve_valid_grounded_message_with_structural_tokens():
    """Structurally justified tokens (1-page, 3-step, top 3, 2-min) are accepted alongside grounded facts."""
    setup_test_context()
    merchant = context_store.get("merchant", "m_meera")["payload"]
    category = context_store.get("category", "dentists")["payload"]

    compact_ctx = {"merchant": merchant, "category": category}
    ledger = build_evidence_ledger(compact_ctx)

    # 1. '3-step' structural token
    valid_3step = (
        "Dr. Meera Dental Clinic, your CTR is 2.1% vs 3.0% for Dentists peers. "
        "You have Dental Cleaning @ ₹299 active — reply YES to get the 3-step action checklist before this dip widens."
    )
    is_valid, reason = validate_message(valid_3step, ledger)
    assert is_valid, f"Expected valid, got: {reason}"

    # 2. '1-page' structural token
    valid_1page = (
        "Dr. Meera Dental Clinic, urgent regulatory update for Dentists. "
        "Reply YES to get the 1-page compliance checklist before the deadline."
    )
    is_valid, reason = validate_message(valid_1page, ledger)
    assert is_valid, f"Expected valid, got: {reason}"

    # 3. 'top 3' structural token
    valid_top3 = (
        "Dr. Meera Dental Clinic, Vera here from magicpin. "
        "Reply YES to see your top 3 growth opportunities for Dentists this week."
    )
    is_valid, reason = validate_message(valid_top3, ledger)
    assert is_valid, f"Expected valid, got: {reason}"

    # 4. '2-min' structural token
    valid_2min = (
        "Dr. Meera, JIDA Oct 2026, p.14 published a 2,100-patient trial. "
        "Reply YES to read the 2-min abstract before the weekend."
    )
    is_valid, reason = validate_message(valid_2min, ledger)
    assert is_valid, f"Expected valid, got: {reason}"


# ---------------------------------------------------------------------------
# 8. Unauthorized Numeric Structures
# ---------------------------------------------------------------------------

def test_reject_unauthorized_numeric_structures():
    """Phrasing mimicking structural tokens but with unverified numbers must be rejected."""
    setup_test_context()
    merchant = context_store.get("merchant", "m_meera")["payload"]
    category = context_store.get("category", "dentists")["payload"]

    compact_ctx = {"merchant": merchant, "category": category}
    ledger = build_evidence_ledger(compact_ctx)

    # '4-step' is not a permitted structural token (only 3-step is)
    body_4step = (
        "Dr. Meera Dental Clinic, your CTR is 2.1%. "
        "Reply YES to get the 4-step action plan before the weekend."
    )
    is_valid, reason = validate_message(body_4step, ledger)
    assert not is_valid
    assert "Unverified numeric claim '4'" in reason

    # 'top 8' is not a permitted structural token (only top 3 is) and 8 is ungrounded
    body_top8 = (
        "Dr. Meera Dental Clinic, your CTR is 2.1%. "
        "Reply YES to see your top 8 growth opportunities this week."
    )
    is_valid, reason = validate_message(body_top8, ledger)
    assert not is_valid
    assert "Unverified numeric claim '8'" in reason


# ---------------------------------------------------------------------------
# 9. Grounded Numbers 1-5 Are Permitted When Explicitly in Evidence
# ---------------------------------------------------------------------------

def test_allow_grounded_numbers_when_present_in_context():
    """Numbers like 4 are permitted when explicitly present in evidence context."""
    setup_test_context()
    merchant = context_store.get("merchant", "m_meera")["payload"]
    category = context_store.get("category", "dentists")["payload"]

    # Trigger explicitly specifies occurrences_30d = 4 reviews
    trigger = {
        "id": "trg_reviews_01",
        "kind": "review_theme_emerged",
        "merchant_id": "m_meera",
        "urgency": 2,
        "payload": {"occurrences_30d": 4, "theme": "service_speed"},
    }

    compact_ctx = {
        "merchant": merchant,
        "category": category,
        "trigger": trigger,
    }
    ledger = build_evidence_ledger(compact_ctx)
    assert "4" in ledger.allowed_numbers

    # Message citing real 4 reviews from evidence is valid
    body_4 = (
        "Dr. Meera Dental Clinic, we noticed 4 recent customer reviews mentioning service speed. "
        "Reply YES to see the 3-step action plan to address this review feedback."
    )
    is_valid, reason = validate_message(body_4, ledger)
    assert is_valid, f"Expected valid when 4 is in evidence, got: {reason}"


# ---------------------------------------------------------------------------
# 10. End-to-End Tick Integration: Fallback Fires on Hardened Rejections
# ---------------------------------------------------------------------------

def test_end_to_end_tick_fallback_on_hardened_rejections():
    """End-to-end /v1/tick triggers fallback when LLM generates hallucinated content."""
    setup_test_context()

    # LLM hallucinates 7.2% and invented name Rahul
    hallucinated_output = {
        "body": "Hi Rahul, Dr. Meera Dental Clinic noticed your CTR jumped 7.2%. Want to highlight Dental Cleaning @ ₹299?",
        "cta": "open_ended",
        "rationale": "Hallucinating wrong CTR and name",
    }
    set_custom_llm_caller(lambda p, s, t: json.dumps(hallucinated_output))

    resp = client.post("/v1/tick", json={
        "now": "2026-04-26T10:00:00Z",
        "available_triggers": ["trg_perf_01"],
    })
    assert resp.status_code == 200
    actions = resp.json().get("actions", [])
    assert len(actions) == 1

    # Confirmed fallback to deterministic template
    act = actions[0]
    assert act["template_name"] == "template_performance_drop_v1"
    assert "Rahul" not in act["body"]
    assert "7.2%" not in act["body"]
    assert "2.1%" in act["body"]
