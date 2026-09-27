"""Formal transition matrix and conversation state machine test suite.

Tests:
1. Complete transition matrix coverage for every ConversationState x Intent (7 x 6 = 42 combinations).
2. Explicit rejection of impossible transitions from terminal states.
3. End-to-end execution of all 42 transitions via POST /v1/reply.
4. Sensitivity to current_state and prior_action in rationale and continuation text.
5. Deterministic handling of duplicate, stale, out-of-order, repeated, hostile, negative, delay, question, and positive turns.
"""

import pytest
from fastapi.testclient import TestClient

from app.conversation import (
    ACTIVE_STATES,
    ConversationState,
    Intent,
    TERMINAL_STATES,
    TRANSITION_MATRIX,
    TransitionRule,
    conversation_state_machine,
    is_auto_reply_text,
    normalize_state,
)
from app.main import app
from app.store import context_store, conversation_store

client = TestClient(app)

INTENT_SAMPLE_MESSAGES = {
    Intent.POSITIVE: "Yes, let's do it",
    Intent.NEGATIVE: "No thanks, not interested",
    Intent.DELAY: "Call me tomorrow, I am busy",
    Intent.HOSTILE: "Stop messaging me, spam!",
    Intent.OFF_TOPIC: "What is the weather today?",
    Intent.QUESTION: "How does the peer CTR benchmark work?",
}


@pytest.fixture(autouse=True)
def reset_stores():
    """Reset context store, conversation store, and auto-reply tracking before every test."""
    context_store.clear()
    conversation_store.clear()
    conversation_state_machine.reset()
    yield
    context_store.clear()
    conversation_store.clear()
    conversation_state_machine.reset()


def test_transition_matrix_has_complete_42_entry_coverage():
    """Verify that every state and every intent is mapped in the formal transition matrix."""
    all_states = conversation_state_machine.get_all_states()
    all_intents = list(Intent)

    assert len(all_states) == 7
    assert len(all_intents) == 6
    assert len(TRANSITION_MATRIX) == 42

    for state in all_states:
        for intent in all_intents:
            key = (state, intent)
            assert key in TRANSITION_MATRIX, f"Missing matrix entry for {key}"
            rule = TRANSITION_MATRIX[key]
            assert isinstance(rule, TransitionRule)
            assert rule.from_state == state
            assert rule.intent == intent
            assert isinstance(rule.to_state, ConversationState)
            assert isinstance(rule.allowed, bool)
            assert rule.action in ("send", "wait", "end")
            assert len(rule.rationale_template) > 0


def test_terminal_states_and_rejection_of_impossible_transitions():
    """
    Verify that terminal states (ConversationState.END) reject impossible transitions.
    Only POSITIVE intent is allowed to reopen the conversation; all others are rejected.
    """
    assert ConversationState.END in TERMINAL_STATES
    assert conversation_state_machine.get_terminal_states() == {ConversationState.END}

    # POSITIVE intent: allowed reopening
    pos_rule = conversation_state_machine.evaluate_transition(ConversationState.END, Intent.POSITIVE)
    assert pos_rule.allowed is True
    assert pos_rule.to_state == ConversationState.SEND
    assert pos_rule.action == "send"
    assert conversation_state_machine.is_transition_allowed(ConversationState.END, Intent.POSITIVE) is True

    # All other intents: rejected impossible transitions
    non_positive_intents = [
        Intent.QUESTION,
        Intent.DELAY,
        Intent.OFF_TOPIC,
        Intent.NEGATIVE,
        Intent.HOSTILE,
    ]
    for intent in non_positive_intents:
        rule = conversation_state_machine.evaluate_transition(ConversationState.END, intent)
        assert rule.allowed is False, f"Expected {intent} in state END to be rejected"
        assert rule.to_state == ConversationState.END
        assert rule.action == "end"
        assert "impossible" in rule.rationale_template.lower() or "rejected" in rule.rationale_template.lower()
        assert conversation_state_machine.is_transition_allowed(ConversationState.END, intent) is False


def test_active_states_allow_all_intents():
    """Verify that all active states allow all 6 standard intents."""
    for state in ACTIVE_STATES:
        allowed = conversation_state_machine.get_allowed_transitions(state)
        assert set(allowed) == set(Intent)
        for intent in Intent:
            rule = conversation_state_machine.evaluate_transition(state, intent)
            assert rule.allowed is True, f"Expected {intent} to be allowed from {state}"
            assert conversation_state_machine.is_transition_allowed(state, intent) is True


@pytest.mark.parametrize("state", list(ConversationState))
@pytest.mark.parametrize("intent", list(Intent))
def test_matrix_e2e_reply_execution(state: ConversationState, intent: Intent):
    """
    End-to-end execution of every state x intent combination via POST /v1/reply.
    Verifies state progression and action dispatch for all 42 combinations.
    """
    conv_id = f"conv_matrix_{state.value.lower()}_{intent.value.lower()}"
    mid = f"m_{state.value.lower()}_{intent.value.lower()}"

    # Seed context
    context_store.set("merchant", mid, 1, {
        "id": mid,
        "name": "Matrix Merchant",
        "category_slug": "dentists",
        "identity": {"name": "Matrix Merchant"},
        "offers": [{"title": "Checkup @ ₹199", "status": "active"}],
    })
    context_store.set("category", "dentists", 1, {
        "slug": "dentists",
        "display_name": "Dentists",
    })

    # Seed conversation with starting state
    conversation_store.create_or_update(
        conversation_id=conv_id,
        merchant_id=mid,
        last_action="sent" if state != ConversationState.NEW else None,
        state=state.value,
    )

    msg = INTENT_SAMPLE_MESSAGES[intent]
    resp = client.post("/v1/reply", json={
        "conversation_id": conv_id,
        "merchant_id": mid,
        "message": msg,
        "turn_number": 2,
    })
    assert resp.status_code == 200
    data = resp.json()

    expected_rule = conversation_state_machine.evaluate_transition(state, intent)
    assert data["action"] == expected_rule.action

    if not expected_rule.allowed:
        # Rejected impossible transition: state remains terminal END, action is end
        assert conversation_store.get(conv_id)["state"] == ConversationState.END.value
        assert "rejected" in data["rationale"].lower() or "impossible" in data["rationale"].lower()
    else:
        # Allowed transition: state advances to rule.to_state
        assert conversation_store.get(conv_id)["state"] == expected_rule.to_state.value


def test_transition_behavior_depends_on_current_state_and_prior_action():
    """Verify transition rationales and continuation messages reflect current state and prior action."""
    mid = "m_prior_test"
    context_store.set("merchant", mid, 1, {
        "id": mid,
        "name": "State Aware Clinic",
        "category_slug": "dentists",
        "identity": {"name": "State Aware Clinic"},
    })

    # 1. Resuming from WAIT
    rule_wait = conversation_state_machine.evaluate_transition(
        ConversationState.WAIT, Intent.POSITIVE, prior_action="wait"
    )
    assert "resuming" in rule_wait.rationale_template.lower()

    # 2. Moving forward after ANSWER
    rule_ans = conversation_state_machine.evaluate_transition(
        ConversationState.ANSWER, Intent.POSITIVE, prior_action="send"
    )
    assert "accepted proposal" in rule_ans.rationale_template.lower()

    # 3. Reaffirming after SEND
    rule_send = conversation_state_machine.evaluate_transition(
        ConversationState.SEND, Intent.POSITIVE, prior_action="send"
    )
    assert "reaffirmed" in rule_send.rationale_template.lower()

    # 4. Opting in after REDIRECT
    rule_redir = conversation_state_machine.evaluate_transition(
        ConversationState.REDIRECT, Intent.POSITIVE, prior_action="send"
    )
    assert "redirection" in rule_redir.rationale_template.lower()

    # 5. Question after commitment in SEND
    rule_q_send = conversation_state_machine.evaluate_transition(
        ConversationState.SEND, Intent.QUESTION, prior_action="send"
    )
    assert "post-commitment" in rule_q_send.rationale_template.lower()


def test_anomalous_events_duplicate_turns():
    """Duplicate turns must be rejected as no-ops (action: wait) without mutating state."""
    conv_id = "conv_ano_dup"
    mid = "m_ano_01"

    # Turn 1
    r1 = client.post("/v1/reply", json={
        "conversation_id": conv_id,
        "merchant_id": mid,
        "message": "Yes please proceed",
        "turn_number": 2,
    })
    assert r1.status_code == 200
    assert r1.json()["action"] == "send"
    assert conversation_store.get(conv_id)["state"] == ConversationState.SEND.value

    # Duplicate Turn 1 (same turn_number and identical message)
    r2 = client.post("/v1/reply", json={
        "conversation_id": conv_id,
        "merchant_id": mid,
        "message": "Yes please proceed",
        "turn_number": 2,
    })
    assert r2.status_code == 200
    assert r2.json()["action"] == "wait"
    assert r2.json()["wait_seconds"] == 600
    # State remains SEND
    assert conversation_store.get(conv_id)["state"] == ConversationState.SEND.value


def test_anomalous_events_repeated_auto_reply_loop_break():
    """Canned auto-replies or repeated turns break after 3 consecutive occurrences and transition to END."""
    conv_id = "conv_ano_loop"
    mid = "m_ano_02"
    auto_msg = "Thank you for reaching out! Our team is currently unavailable and will reply soon."
    assert is_auto_reply_text(auto_msg) is True

    conversation_store.create_or_update(conv_id, merchant_id=mid, state="INITIAL_MESSAGE")

    # Repeat 1: backoff
    r1 = client.post("/v1/reply", json={
        "conversation_id": conv_id,
        "merchant_id": mid,
        "message": auto_msg,
        "turn_number": 2,
    })
    assert r1.json()["action"] == "wait"
    assert conversation_store.get(conv_id)["state"] == ConversationState.WAIT.value

    # Repeat 2: backoff
    r2 = client.post("/v1/reply", json={
        "conversation_id": conv_id,
        "merchant_id": mid,
        "message": auto_msg,
        "turn_number": 3,
    })
    assert r2.json()["action"] == "wait"
    assert conversation_store.get(conv_id)["state"] == ConversationState.WAIT.value

    # Repeat 3: loop broken -> terminal state END
    r3 = client.post("/v1/reply", json={
        "conversation_id": conv_id,
        "merchant_id": mid,
        "message": auto_msg,
        "turn_number": 4,
    })
    assert r3.json()["action"] == "end"
    assert conversation_store.get(conv_id)["state"] == ConversationState.END.value


def test_anomalous_events_out_of_order_turn_processing():
    """Out-of-order turn arrivals (e.g. Turn 5 before Turn 3) are handled gracefully without state corruption."""
    conv_id = "conv_ano_ooo"
    mid = "m_ano_03"

    context_store.set("merchant", mid, 1, {
        "id": mid,
        "name": "Fast Clinic",
        "category_slug": "dentists",
        "identity": {"name": "Fast Clinic"},
    })

    # Turn 5 arrives first (question)
    r1 = client.post("/v1/reply", json={
        "conversation_id": conv_id,
        "merchant_id": mid,
        "message": "What is the fee structure?",
        "turn_number": 5,
    })
    assert r1.status_code == 200
    assert r1.json()["action"] == "send"
    assert conversation_store.get(conv_id)["state"] == ConversationState.ANSWER.value

    # Turn 3 arrives second (positive opt-in)
    r2 = client.post("/v1/reply", json={
        "conversation_id": conv_id,
        "merchant_id": mid,
        "message": "Yes, proceed with campaign",
        "turn_number": 3,
    })
    assert r2.status_code == 200
    assert r2.json()["action"] == "send"
    assert conversation_store.get(conv_id)["state"] == ConversationState.SEND.value


def test_normalize_state_robustness():
    """Verify normalize_state handles enum values, raw strings, aliases, and unknown inputs safely."""
    assert normalize_state(ConversationState.SEND) == ConversationState.SEND
    assert normalize_state("SEND") == ConversationState.SEND
    assert normalize_state("send") == ConversationState.SEND
    assert normalize_state("waiting_for_reply") == ConversationState.INITIAL_MESSAGE
    assert normalize_state("WAITING_FOR_REPLY") == ConversationState.INITIAL_MESSAGE
    assert normalize_state("INITIAL_MESSAGE") == ConversationState.INITIAL_MESSAGE
    assert normalize_state(None) == ConversationState.NEW
    assert normalize_state("") == ConversationState.NEW
    assert normalize_state("unknown_xyz") == ConversationState.NEW
