"""Tests for conversation state machine and POST /v1/reply endpoint."""

import pytest
from fastapi.testclient import TestClient

from app.conversation import (
    ConversationState,
    Intent,
    classify_intent,
    conversation_state_machine,
    is_auto_reply_text,
)
from app.main import app
from app.store import context_store, conversation_store

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_stores():
    """Ensure context and conversation stores are reset before each test."""
    context_store.clear()
    conversation_store.clear()
    conversation_state_machine.reset()
    yield
    context_store.clear()
    conversation_store.clear()
    conversation_state_machine.reset()


# ---------------------------------------------------------------------------
# Unit Tests for Intent Classification & Auto-Reply Detection
# ---------------------------------------------------------------------------

def test_intent_classifier():
    # Positive
    for phrase in ["yes", "Sure", "okay, let's do it", "send it over", "go ahead and run it"]:
        assert classify_intent(phrase) == Intent.POSITIVE, f"Failed on {phrase}"

    # Negative
    for phrase in ["no", "not interested", "don't need this right now", "stop", "No thanks"]:
        assert classify_intent(phrase) == Intent.NEGATIVE, f"Failed on {phrase}"

    # Delay
    for phrase in ["later", "call me tomorrow", "not now please", "give me some time", "busy"]:
        assert classify_intent(phrase) == Intent.DELAY, f"Failed on {phrase}"

    # Hostile
    for phrase in ["leave me alone", "stop messaging me", "don't contact me again", "this is spam"]:
        assert classify_intent(phrase) == Intent.HOSTILE, f"Failed on {phrase}"

    # Off-topic
    for phrase in ["what's the weather today?", "who is PM of India", "tell me a joke"]:
        assert classify_intent(phrase) == Intent.OFF_TOPIC, f"Failed on {phrase}"

    # Question / Inquiries
    for phrase in ["How does billing work?", "Can you explain the discounts?", "What are the terms?"]:
        assert classify_intent(phrase) == Intent.QUESTION, f"Failed on {phrase}"


def test_auto_reply_detector():
    assert is_auto_reply_text("Thank you for contacting us! Our team will respond shortly.") is True
    assert is_auto_reply_text("I am currently out of office until Monday.") is True
    assert is_auto_reply_text("Auto-reply: We have received your message.") is True
    assert is_auto_reply_text("Yes, send me the details") is False


# ---------------------------------------------------------------------------
# Tests for Every Intent in /v1/reply
# ---------------------------------------------------------------------------

def test_reply_positive_intent():
    """Positive intent moves to SEND action with commitment execution."""
    response = client.post("/v1/reply", json={
        "conversation_id": "conv_pos_01",
        "merchant_id": "m_001",
        "message": "Yes, let's do it. Go ahead.",
        "turn_number": 2,
    })
    assert response.status_code == 200
    data = response.json()
    assert data["action"] == "send"
    assert data["body"] is not None
    assert any(w in data["body"].lower() for w in ["done", "proceed", "draft", "ready"])

    conv = conversation_store.get("conv_pos_01")
    assert conv is not None
    assert conv["state"] == ConversationState.SEND.value
    assert len(conv["turns"]) == 1
    assert len(conv["sent_messages"]) == 1


def test_reply_exact_commitment_transition_regression():
    """
    Regression test for: 'Ok lets do it. Whats next?'
    Must immediately return action: 'send' with concrete deliverables (e.g. 'draft ready',
    'here's what's next') and zero qualifying questions.
    """
    response = client.post("/v1/reply", json={
        "conversation_id": "conv_intent_regression_1",
        "merchant_id": "m_test_mid",
        "message": "Ok lets do it. Whats next?",
        "turn_number": 2,
    })
    assert response.status_code == 200
    data = response.json()
    assert data["action"] == "send"
    assert data["wait_seconds"] is None
    body = data["body"]
    assert body is not None

    body_lower = body.lower()
    # Check actioning deliverables
    actioning = ["done", "sending", "draft", "here", "confirm", "proceed", "next"]
    assert any(w in body_lower for w in actioning)
    assert "draft ready" in body_lower or "ready" in body_lower
    assert "here's what's next" in body_lower or "next" in body_lower

    # Zero qualifying questions allowed
    qualifying = ["would you", "do you", "can you tell", "what if", "how about"]
    assert not any(w in body_lower for w in qualifying)


def test_reply_negative_intent():
    """Negative intent gracefully ends the conversation."""
    response = client.post("/v1/reply", json={
        "conversation_id": "conv_neg_01",
        "merchant_id": "m_001",
        "message": "No, not interested in this.",
        "turn_number": 2,
    })
    assert response.status_code == 200
    data = response.json()
    assert data["action"] == "end"

    conv = conversation_store.get("conv_neg_01")
    assert conv["state"] == ConversationState.END.value


def test_reply_delay_intent():
    """Delay intent returns wait action with delay seconds."""
    response = client.post("/v1/reply", json={
        "conversation_id": "conv_delay_01",
        "merchant_id": "m_001",
        "message": "Busy in surgery, call me tomorrow.",
        "turn_number": 2,
    })
    assert response.status_code == 200
    data = response.json()
    assert data["action"] == "wait"
    assert data["wait_seconds"] == 1800

    conv = conversation_store.get("conv_delay_01")
    assert conv["state"] == ConversationState.WAIT.value


def test_reply_hostile_intent():
    """Hostile intent immediately ends conversation."""
    response = client.post("/v1/reply", json={
        "conversation_id": "conv_hostile_01",
        "merchant_id": "m_001",
        "message": "Stop messaging me. This is useless spam.",
        "turn_number": 2,
    })
    assert response.status_code == 200
    data = response.json()
    assert data["action"] == "end"

    conv = conversation_store.get("conv_hostile_01")
    assert conv["state"] == ConversationState.END.value


def test_reply_off_topic_intent():
    """Off-topic messages return send action redirecting to merchant growth."""
    response = client.post("/v1/reply", json={
        "conversation_id": "conv_offtopic_01",
        "merchant_id": "m_001",
        "message": "What's the weather in Delhi today?",
        "turn_number": 2,
    })
    assert response.status_code == 200
    data = response.json()
    assert data["action"] == "send"
    assert "magicpin" in data["body"].lower()

    conv = conversation_store.get("conv_offtopic_01")
    assert conv["state"] == ConversationState.REDIRECT.value


def test_reply_question_intent():
    """Questions return send action with informational answer."""
    response = client.post("/v1/reply", json={
        "conversation_id": "conv_question_01",
        "merchant_id": "m_001",
        "message": "How do you calculate the peer CTR benchmark?",
        "turn_number": 2,
    })
    assert response.status_code == 200
    data = response.json()
    assert data["action"] == "send"
    assert "benchmark" in data["body"].lower() or "peer" in data["body"].lower()

    conv = conversation_store.get("conv_question_01")
    assert conv["state"] == ConversationState.ANSWER.value


# ---------------------------------------------------------------------------
# Complex State Machine & Edge Cases
# ---------------------------------------------------------------------------

def test_intent_transition_qualification_to_action():
    """
    Simulates:
    1. Initial bot engagement.
    2. Merchant commitment: 'Ok lets do it. Whats next?'
    3. Bot MUST enter ACTION mode with action words and NO qualifying words.
    """
    conv_id = "conv_intent_transition"

    # Pre-seed prior turn
    conversation_store.create_or_update(conv_id, "m_001", state="INITIAL_MESSAGE")
    conversation_store.add_turn(conv_id, {"role": "vera", "message": "Want me to draft this?", "turn_number": 1})

    commitment_msg = "Ok lets do it. Whats next?"
    response = client.post("/v1/reply", json={
        "conversation_id": conv_id,
        "merchant_id": "m_001",
        "message": commitment_msg,
        "turn_number": 2,
    })
    assert response.status_code == 200
    data = response.json()
    assert data["action"] == "send"
    body_lower = data["body"].lower()

    # Action words present
    action_words = ["done", "sending", "draft", "here", "confirm", "proceed", "next", "ready"]
    assert any(w in body_lower for w in action_words), f"Action words missing in: {body_lower}"

    # Qualifying questions NOT present
    qualifying_words = ["would you", "can you tell", "what if", "how about"]
    assert not any(w in body_lower for w in qualifying_words), f"Unexpected qualifying words in: {body_lower}"

    # State updated to SEND
    conv = conversation_store.get(conv_id)
    assert conv["state"] == ConversationState.SEND.value


def test_repeated_auto_reply_detection():
    """
    Simulates repeated auto-reply loop from merchant:
    'Thank you for contacting us! Our team will respond shortly.'
    Bot detects repetition and transitions toward WAIT or END.
    """
    conv_id = "conv_auto_loop"
    auto_msg = "Thank you for contacting us! Our team will respond shortly."

    # Turn 1: Initial auto-reply
    r1 = client.post("/v1/reply", json={
        "conversation_id": conv_id,
        "merchant_id": "m_001",
        "message": auto_msg,
        "turn_number": 2,
    })
    assert r1.status_code == 200
    assert r1.json()["action"] in ("wait", "end")

    # Turn 2: Same auto-reply repeated
    r2 = client.post("/v1/reply", json={
        "conversation_id": conv_id,
        "merchant_id": "m_001",
        "message": auto_msg,
        "turn_number": 3,
    })
    assert r2.status_code == 200
    assert r2.json()["action"] in ("wait", "end")

    # Turn 3: Repeated again -> transitions to END
    r3 = client.post("/v1/reply", json={
        "conversation_id": conv_id,
        "merchant_id": "m_001",
        "message": auto_msg,
        "turn_number": 4,
    })
    assert r3.status_code == 200
    assert r3.json()["action"] == "end"


def test_auto_reply_loop_termination_4_repeats():
    """
    Per challenge-testing-brief.md Phase 4 scenario 1:
    Judge sends the same canned text 4 times (e.g. conv_auto_1..4).
    Bot must detect the loop and after 2-3 consecutive auto-replies, return action: 'end'.
    Final action is asserted to be 'end'.
    """
    mid = "m_auto_mid_01"
    auto_msg = "Thank you for contacting us! Our team will respond shortly."

    actions = []
    for i in range(1, 5):
        resp = client.post("/v1/reply", json={
            "conversation_id": f"conv_auto_{i}",
            "merchant_id": mid,
            "message": auto_msg,
            "turn_number": i + 1,
        })
        assert resp.status_code == 200
        actions.append(resp.json()["action"])

    # Initial turns wait, then terminates gracefully with 'end'
    assert actions[0] == "wait"
    assert actions[1] == "wait"
    assert actions[2] == "end"
    assert actions[3] == "end"
    assert actions[-1] == "end"


def test_out_of_order_turns():
    """Out-of-order turn numbers are processed safely without crashing."""
    conv_id = "conv_ooo"

    # Turn 5 sent first
    r1 = client.post("/v1/reply", json={
        "conversation_id": conv_id,
        "merchant_id": "m_001",
        "message": "Tell me more",
        "turn_number": 5,
    })
    assert r1.status_code == 200

    # Turn 3 sent after Turn 5
    r2 = client.post("/v1/reply", json={
        "conversation_id": conv_id,
        "merchant_id": "m_001",
        "message": "Yes, proceed",
        "turn_number": 3,
    })
    assert r2.status_code == 200
    assert r2.json()["action"] == "send"


def test_stale_and_duplicate_conversation_handling():
    """
    1. Re-sending identical duplicate turn returns wait rather than repeating work.
    2. Interacting with an already closed/ended conversation returns end.
    """
    conv_id = "conv_stale_dup"

    # Step 1: Normal turn
    r1 = client.post("/v1/reply", json={
        "conversation_id": conv_id,
        "merchant_id": "m_001",
        "message": "How does this work?",
        "turn_number": 2,
    })
    assert r1.status_code == 200

    # Step 2: Duplicate turn identical in message and turn_number
    r2 = client.post("/v1/reply", json={
        "conversation_id": conv_id,
        "merchant_id": "m_001",
        "message": "How does this work?",
        "turn_number": 2,
    })
    assert r2.status_code == 200
    assert r2.json()["action"] == "wait"

    # Step 3: Close the conversation
    client.post("/v1/reply", json={
        "conversation_id": conv_id,
        "merchant_id": "m_001",
        "message": "No, stop",
        "turn_number": 3,
    })

    # Step 4: Stale attempt to reply to ended conversation
    r4 = client.post("/v1/reply", json={
        "conversation_id": conv_id,
        "merchant_id": "m_001",
        "message": "Hello are you there?",
        "turn_number": 4,
    })
    assert r4.status_code == 200
    assert r4.json()["action"] == "end"
