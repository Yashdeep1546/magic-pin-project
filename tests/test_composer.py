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
    """Verify category policies baseline exists and contains expected constraints."""
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


def test_build_compact_context_nested_schema():
    """Verify compact context walks real nested payloads (testing-brief.md §3)."""
    category = {
        "slug": "dentists",
        "display_name": "Dentists",
        "voice": {
            "tone": "peer_clinical",
            "register": "respectful_collegial",
            "code_mix": "hindi_english_natural",
            "vocab_allowed": ["fluoride varnish", "scaling", "caries"],
            "vocab_taboo": ["guaranteed", "100% safe", "miracle"],
        },
        "peer_stats": {
            "avg_rating": 4.4,
            "avg_reviews": 62,
            "avg_ctr": 0.030,
            "scope": "metro_solo_practices_2026",
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
                "actionable": "Reassess recall interval for adults flagged high-risk in your charting",
            }
        ],
    }

    merchant = {
        "merchant_id": "m_001_drmeera_dentist_delhi",
        "category_slug": "dentists",
        "identity": {
            "name": "Dr. Meera Dental Clinic",
            "city": "Delhi",
            "locality": "Lajpat Nagar",
            "place_id": "ChIJ_LAJPATNAGAR_DENTIST_001",
            "verified": True,
            "languages": ["en", "hi"],
            "owner_first_name": "Meera",
        },
        "subscription": {"status": "active", "plan": "Pro", "days_remaining": 82},
        "performance": {
            "window_days": 30,
            "views": 2410,
            "calls": 18,
            "directions": 45,
            "ctr": 0.021,
            "delta_7d": {"views_pct": 0.18, "calls_pct": -0.05, "ctr_pct": 0.02},
        },
        "offers": [
            {"id": "o_meera_001", "title": "Dental Cleaning @ ₹299", "status": "active"},
            {"id": "o_meera_002", "title": "Deep Cleaning @ ₹499", "status": "expired"},
        ],
        "customer_aggregate": {
            "total_unique_ytd": 540,
            "lapsed_180d_plus": 78,
            "retention_6mo_pct": 0.38,
            "high_risk_adult_count": 124,
        },
        "signals": ["stale_posts:22d", "ctr_below_peer_median", "high_risk_adult_cohort"],
    }

    customer = {
        "customer_id": "c_001_priya",
        "merchant_id": "m_001_drmeera_dentist_delhi",
        "identity": {"name": "Priya", "phone_redacted": "+91 98*** 12345", "language_pref": "hi-en mix"},
        "relationship": {
            "first_visit": "2025-11-04",
            "last_visit": "2026-05-12",
            "visits_total": 4,
            "services_received": ["cleaning", "cleaning", "whitening", "cleaning"],
        },
        "state": "lapsed_soft",
        "preferences": {"preferred_slots": "weekday_evening", "channel": "whatsapp"},
    }

    trigger = {
        "id": "trg_001_research_digest_dentists",
        "scope": "merchant",
        "kind": "research_digest",
        "source": "external",
        "merchant_id": "m_001_drmeera_dentist_delhi",
        "customer_id": None,
        "payload": {
            "category": "dentists",
            "top_item_id": "d_2026W17_jida_fluoride",
        },
        "urgency": 2,
    }

    ctx = build_compact_context(
        merchant=merchant,
        category=category,
        trigger=trigger,
        customer=customer,
        selected_signal="high_risk_adult_cohort",
    )

    # 1. Merchant nested fields
    assert ctx["merchant"]["name"] == "Dr. Meera Dental Clinic"
    assert ctx["merchant"]["owner"] == "Meera"
    assert ctx["merchant"]["city"] == "Delhi"
    assert ctx["merchant"]["locality"] == "Lajpat Nagar"
    assert ctx["merchant"]["performance"]["ctr"] == 0.021
    assert ctx["merchant"]["performance"]["delta_7d"]["views_pct"] == 0.18
    assert ctx["merchant"]["customer_aggregate"]["high_risk_adult_count"] == 124
    assert ctx["merchant"]["active_offers"] == ["Dental Cleaning @ ₹299"]

    # 2. Category dynamic voice
    assert ctx["category"]["voice"]["tone"] == "peer_clinical"
    assert "fluoride varnish" in ctx["category"]["voice"]["vocab_allowed"]
    assert "guaranteed" in ctx["category"]["voice"]["taboos"]

    # 3. Matched Digest
    assert ctx["digest"] is not None
    assert ctx["digest"]["source"] == "JIDA Oct 2026, p.14"
    assert ctx["digest"]["trial_n"] == 2100
    assert ctx["digest"]["patient_segment"] == "high_risk_adults"

    # 4. Customer
    assert ctx["customer"]["name"] == "Priya"
    assert ctx["customer"]["language_pref"] == "hi-en mix"

    # 5. Allowed facts verification
    facts = ctx["allowed_facts"]
    assert any("Dr. Meera Dental Clinic" in f for f in facts)
    assert any("2.1%" in f for f in facts)
    assert any("+18.0%" in f for f in facts)
    assert any("124 patients" in f for f in facts)
    assert any("JIDA Oct 2026, p.14" in f for f in facts)
    assert any("2,100 patients" in f for f in facts)
    assert any("Dental Cleaning @ ₹299" in f for f in facts)


def test_compose_message_with_digest_citations():
    """When LLM composes research digest message citing real trial numbers, evidence ledger validates it."""
    category = {
        "slug": "dentists",
        "display_name": "Dentists",
        "voice": {
            "tone": "peer_clinical",
            "vocab_allowed": ["fluoride varnish", "caries"],
        },
        "digest": [
            {
                "id": "d_01",
                "title": "3-month fluoride recall cuts caries 38% better than 6-month",
                "source": "JIDA Oct 2026, p.14",
                "trial_n": 2100,
                "patient_segment": "high_risk_adults",
                "summary": "Trial shows 38% lower caries recurrence with 3-month recall.",
            }
        ],
    }
    merchant = {
        "identity": {"name": "Dr. Meera Dental Clinic", "owner_first_name": "Meera"},
        "performance": {"ctr": 0.021},
        "customer_aggregate": {"high_risk_adult_count": 124},
        "offers": [{"title": "Dental Cleaning @ ₹299", "status": "active"}],
    }
    trigger = {
        "id": "trg_01",
        "kind": "research_digest",
        "payload": {"top_item_id": "d_01"},
    }

    compact_ctx = build_compact_context(merchant=merchant, category=category, trigger=trigger)
    fallback_data = ("Fallback message", "template_fallback", [], "Fallback reason")

    good_message = (
        "Dr. Meera, JIDA Oct 2026, p.14 published a 2,100-patient trial showing 3-month fluoride recall "
        "cuts caries 38% better for your high-risk adult patients. Want me to draft a patient-ed WhatsApp?"
    )
    llm_payload = {
        "body": good_message,
        "cta": "open_ended",
        "rationale": "Directly grounded in JIDA clinical digest and high-risk adult cohort",
    }
    set_custom_llm_caller(lambda prompt, sys, timeout: json.dumps(llm_payload))

    body, tmpl, params, rationale = compose_message(compact_ctx, fallback_data)
    assert body == good_message
    assert tmpl == "llm_grounded_composer"
    assert rationale == llm_payload["rationale"]


def test_dynamic_voice_policy_overrides_hardcoded():
    """Verify that category.voice dynamically configures tone and taboos without hardcoded lock-in."""
    custom_category = {
        "slug": "custom_specialty",
        "display_name": "Bespoke Wellness",
        "voice": {
            "tone": "mindful_gentle",
            "register": "intimate_warm",
            "vocab_allowed": ["balance", "renewal", "serenity"],
            "taboos": ["hustle", "guaranteed", "fast"],
        },
    }
    ctx = build_compact_context(
        merchant={"identity": {"name": "Soul Spa"}},
        category=custom_category,
        trigger={"id": "t_01", "kind": "seasonal_beat"},
    )
    voice = ctx["category"]["voice"]
    assert voice["tone"] == "mindful_gentle"
    assert "renewal" in voice["vocab_allowed"]
    assert "hustle" in voice["taboos"]
    assert "Tone: mindful_gentle" in ctx["category_voice_policy"]


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
    context_store.set("category", "dentists", 1, {
        "slug": "dentists",
        "display_name": "Dentists",
        "voice": {"tone": "peer_clinical"},
    })
    context_store.set("merchant", "m_001", 1, {
        "merchant_id": "m_001",
        "category_slug": "dentists",
        "identity": {"name": "Meera Dental", "city": "Delhi"},
        "performance": {"ctr": 0.02},
        "offers": [{"title": "Dental Cleaning @ ₹299", "status": "active"}],
    })
    context_store.set("trigger", "trg_01", 1, {
        "id": "trg_01",
        "kind": "compliance_alert",
        "merchant_id": "m_001",
        "urgency": 4,
    })

    llm_payload = {
        "body": "Meera Dental, urgent compliance update for Dentists in Delhi. Want me to draft a summary?",
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
    context_store.set("category", "dentists", 1, {
        "slug": "dentists",
        "display_name": "Dentists",
        "peer_stats": {"avg_ctr": 0.03},
    })
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


def test_master_system_prompt_dynamic_variables_injection():
    """Verify that build_system_prompt dynamically injects current_date, language_preference, locality, and business_name."""
    from app.composer import build_system_prompt, MASTER_SYSTEM_PROMPT_TEMPLATE

    ctx = {
        "now": "2026-04-26T10:00:00Z",
        "merchant": {
            "name": "Dr. Meera Dental Clinic",
            "locality": "Lajpat Nagar",
            "languages": ["en", "hi"],
        },
        "customer": {
            "name": "Priya",
            "language_pref": "hi-en mix",
        },
    }

    prompt = build_system_prompt(ctx)

    # 1. Header is placed at the very top
    assert prompt.startswith("SYSTEM INSTRUCTIONS FOR MERCHANT OUTREACH")

    # 2. Dynamic variable substitution
    assert "The current date is April 26, 2026." in prompt
    assert "exact language format requested: hi-en mix" in prompt
    assert "around Lajpat Nagar" in prompt
    assert "ensure Dr. Meera Dental Clinic captures that local intent" in prompt

    # 3. All 4 master evaluation rules are verbatim present
    assert "1. Zero Hallucination & Strict Data Grounding" in prompt
    assert "2. Smart Category Adaptation (Vocabulary Guardrails)" in prompt
    assert "3. Strict Language Compliance" in prompt
    assert "4. Grounded Urgency (The Hook)" in prompt

    # 4. Fallback when merchant context is minimal
    minimal_prompt = build_system_prompt({})
    assert "SYSTEM INSTRUCTIONS FOR MERCHANT OUTREACH" in minimal_prompt
    assert "exact language format requested: English" in minimal_prompt


def test_compose_message_passes_dynamic_system_prompt():
    """Ensure compose_message passes the dynamically generated system prompt to the LLM caller."""
    captured_sys_prompt = []

    def capturing_mock(user_prompt: str, system_prompt: str, timeout: float) -> str:
        captured_sys_prompt.append(system_prompt)
        return json.dumps({
            "body": "Dr. Meera, Priya is due for recall check-up in Lajpat Nagar. Reply YES to dispatch invite.",
            "cta": "open_ended",
            "rationale": "Test rationale",
        })

    set_custom_llm_caller(capturing_mock)

    ctx = {
        "now": "2026-10-31T09:00:00Z",
        "merchant": {
            "name": "Dr. Meera Dental Clinic",
            "locality": "Lajpat Nagar",
            "languages": ["en", "hi"],
        },
        "customer": {
            "name": "Priya",
            "language_pref": "hi-en mix",
        },
    }
    fallback = ("Fallback", "tmpl", [], "rat")
    compose_message(ctx, fallback)

    assert len(captured_sys_prompt) == 1
    sys_p = captured_sys_prompt[0]
    assert sys_p.startswith("SYSTEM INSTRUCTIONS FOR MERCHANT OUTREACH")
    assert "The current date is October 31, 2026." in sys_p
    assert "exact language format requested: hi-en mix" in sys_p


def test_render_template_smart_category_adaptation_gym_chronic_refill():
    """Verify that chronic_refill on gyms adapts to membership renewal and member terminology."""
    from app.decision.templates import render_template

    merchant = {
        "identity": {"name": "Roshni Fitness Gym", "city": "Ahmedabad", "locality": "Navrangpura"},
        "offers": [{"title": "Annual Gym Pass @ ₹9,999", "status": "active"}],
        "category_slug": "gyms",
    }
    category = {"slug": "gyms", "display_name": "Gyms & Fitness"}
    customer = {"identity": {"name": "Ira"}}
    trigger = {
        "id": "trg_082_chronic_refill_due_m_036_roshni_gym_ahm",
        "kind": "chronic_refill_due",
        "merchant_id": "m_036_roshni_gym_ahmedabad",
        "customer_id": "c_142_ira",
        "payload": {"placeholder": True, "metric_or_topic": "chronic_refill_due"},
    }

    body, tmpl, params, rationale = render_template(trigger, merchant, category, customer)

    assert "Member Ira" in body
    assert "membership renewal" in body
    assert "workout streak" in body
    assert "chronic" not in body.lower()
    assert "refill" not in body.lower()

