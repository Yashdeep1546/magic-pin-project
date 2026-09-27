"""Replay determinism tests for Magicpin Vera bot.

Asserts:
1. Sending identical /v1/tick requests repeatedly produces identical semantic decisions
   (selected trigger, action type, CTA) across runs, even if timestamps and ack_ids vary.
2. Sending identical /v1/reply requests repeatedly produces identical action types (send/wait/end)
   and state transitions across runs.
3. LLM nondeterminism in composer.py (varying message bodies) NEVER changes which trigger
   was selected or which action was returned — only the message body may vary.
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


def test_tick_deterministic_replay_across_runs():
    """Identical contexts and multiple triggers must produce identical trigger selection and semantic decision."""
    # Setup standard category and merchant
    context_store.set(
        "category",
        "dentists",
        1,
        {
            "slug": "dentists",
            "display_name": "Dentists & Clinics",
            "peer_stats": {"avg_ctr": 0.030},
        },
    )
    context_store.set(
        "merchant",
        "m_det_01",
        1,
        {
            "merchant_id": "m_det_01",
            "category_slug": "dentists",
            "identity": {"name": "Dr. Sharma Dental", "city": "Gurugram"},
            "performance": {"ctr": 0.015, "views": 800},
            "offers": [{"title": "Scaling & Polishing @ ₹399", "status": "active"}],
        },
    )
    # Multiple triggers: T1 (compliance_alert, prio=100), T2 (perf_drop, prio=90), T3 (festival, prio=60)
    context_store.set("trigger", "trg_comp", 1, {"id": "trg_comp", "kind": "compliance_alert", "merchant_id": "m_det_01", "urgency": 4})
    context_store.set("trigger", "trg_perf", 1, {"id": "trg_perf", "kind": "performance_drop", "merchant_id": "m_det_01", "urgency": 3})
    context_store.set("trigger", "trg_fest", 1, {"id": "trg_fest", "kind": "festival", "merchant_id": "m_det_01", "urgency": 2})

    fixed_now = "2026-04-26T12:00:00Z"
    selected_trigger_id = None
    expected_action_template = None
    expected_cta = None

    # Run 10 consecutive replays
    for i in range(10):
        suppression_engine.clear()
        conversation_store.clear()

        resp = client.post(
            "/v1/tick",
            json={"now": fixed_now, "available_triggers": ["trg_comp", "trg_perf", "trg_fest"]},
        )
        assert resp.status_code == 200
        actions = resp.json().get("actions", [])
        assert len(actions) == 1
        action = actions[0]

        if selected_trigger_id is None:
            selected_trigger_id = action["trigger_id"]
            expected_action_template = action["template_name"]
            expected_cta = action["cta"]
            # Compliance alert has highest priority
            assert selected_trigger_id == "trg_comp"
            assert expected_cta == "open_ended"
        else:
            # Semantic decision must be identical every time
            assert action["trigger_id"] == selected_trigger_id, f"Trigger selection changed on run {i}"
            assert action["template_name"] == expected_action_template
            assert action["cta"] == expected_cta
            assert action["merchant_id"] == "m_det_01"


def test_reply_state_machine_replay_determinism():
    """Identical reply messages must yield identical state transitions and action choices across replays."""
    test_sequences = [
        ("Kal call karo, abhi busy hoon", "wait", "WAIT"),
        ("haan bhai send kar do details", "send", "SEND"),
        ("nahi chahiye band karo", "end", "END"),
        ("who is prime minister?", "send", "REDIRECT"),
        ("Thank you for contacting us! We will respond shortly.", "wait", "WAIT"),
    ]

    for msg, expected_action, expected_state in test_sequences:
        for run_idx in range(5):
            conv_id = f"conv_replay_{run_idx}_{expected_action}"
            conversation_store.create_or_update(
                conversation_id=conv_id,
                merchant_id="m_det_01",
                state="waiting_for_reply",
            )
            resp = client.post(
                "/v1/reply",
                json={
                    "conversation_id": conv_id,
                    "merchant_id": "m_det_01",
                    "from_role": "merchant",
                    "message": msg,
                    "turn_number": 2,
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["action"] == expected_action, f"Action mismatch on run {run_idx} for '{msg}'"
            conv = conversation_store.get(conv_id)
            assert conv["state"] == expected_state, f"State mismatch on run {run_idx} for '{msg}'"


def test_llm_nondeterminism_does_not_affect_trigger_selection_or_action_decision():
    """
    Specifically test that LLM wording variations in composer.py NEVER change
    which trigger was selected or which action was returned — only the message body may vary.
    """
    context_store.set("category", "dentists", 1, {"slug": "dentists", "display_name": "Dentists", "peer_stats": {"avg_ctr": 0.030}})
    context_store.set(
        "merchant",
        "m_var_llm",
        1,
        {
            "merchant_id": "m_var_llm",
            "category_slug": "dentists",
            "identity": {"name": "Meera Clinic"},
            "performance": {"ctr": 0.021},
            "offers": [{"title": "Cleaning @ ₹299", "status": "active"}],
        },
    )
    context_store.set("trigger", "trg_high", 1, {"id": "trg_high", "kind": "compliance_alert", "merchant_id": "m_var_llm", "urgency": 4})
    context_store.set("trigger", "trg_low", 1, {"id": "trg_low", "kind": "curious_ask", "merchant_id": "m_var_llm", "urgency": 1})

    call_count = 0
    generated_bodies = []

    def fluctuating_llm_caller(prompt, sys, timeout):
        nonlocal call_count
        call_count += 1
        # Alternate phrasing between calls
        if call_count % 2 == 1:
            body = "Meera Clinic, urgent compliance notice for Dentists! Would you like a 1-page summary?"
        else:
            body = "Meera Clinic, Dentists compliance guidelines have been updated. Want me to draft the 1-page summary?"
        generated_bodies.append(body)
        return json.dumps({
            "body": body,
            "cta": "open_ended",
            "rationale": f"Variation #{call_count}",
        })

    set_custom_llm_caller(fluctuating_llm_caller)

    first_trigger_id = None
    first_cta = None

    for i in range(6):
        suppression_engine.clear()
        conversation_store.clear()
        resp = client.post(
            "/v1/tick",
            json={"now": "2026-04-26T10:00:00Z", "available_triggers": ["trg_high", "trg_low"]},
        )
        assert resp.status_code == 200
        actions = resp.json().get("actions", [])
        assert len(actions) == 1
        action = actions[0]

        if first_trigger_id is None:
            first_trigger_id = action["trigger_id"]
            first_cta = action["cta"]
            assert first_trigger_id == "trg_high"  # Highest priority compliance
        else:
            # The selected trigger and CTA MUST remain invariant
            assert action["trigger_id"] == first_trigger_id, "Selected trigger changed due to LLM phrasing!"
            assert action["cta"] == first_cta, "CTA changed due to LLM phrasing!"
            assert action["merchant_id"] == "m_var_llm"

    # Confirm bodies did actually vary (proving LLM nondeterminism was active)
    assert len(set(generated_bodies)) > 1
