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


# ---------------------------------------------------------------------------
# Multi-Turn Context-Aware Conversation Tests
# ---------------------------------------------------------------------------

def test_multiturn_tick_to_reply_to_reply():
    """
    Multi-turn test: tick -> reply (question) -> reply (positive).
    1. Tick initiates conversation with trigger and active offer.
    2. Counterpart asks a question: answered with grounded context (active offer).
    3. Counterpart responds positively: continues the specific campaign.
    """
    mid = "m_dentist_multiturn_01"
    trg_id = "trg_multiturn_01"

    # Context setup
    context_store.set("category", "dentists", 1, {
        "slug": "dentists",
        "display_name": "Dentists",
        "peer_stats": {"avg_ctr": 0.035},
    })
    context_store.set("merchant", mid, 1, {
        "id": mid,
        "name": "Dr. Meera Dental Clinic",
        "category_slug": "dentists",
        "identity": {"name": "Dr. Meera Dental Clinic", "owner_first_name": "Dr. Meera"},
        "offers": [{"title": "Dental Cleaning @ ₹299", "status": "active"}],
        "performance": {"views": 1500, "ctr": 0.024},
    })
    context_store.set("trigger", trg_id, 1, {
        "id": trg_id,
        "kind": "recall_due",
        "merchant_id": mid,
        "urgency": 3,
        "payload": {"service_due": "teeth cleaning"},
    })

    # Step 1: tick initiates conversation
    resp_tick = client.post("/v1/tick", json={
        "available_triggers": [trg_id],
        "now": "2026-04-26T10:00:00Z",
    })
    assert resp_tick.status_code == 200
    actions = resp_tick.json()["actions"]
    assert len(actions) == 1
    conv_id = actions[0]["conversation_id"]
    assert "Dr. Meera" in actions[0]["body"]

    # Step 2: reply 1 (question about the offer)
    resp_q = client.post("/v1/reply", json={
        "conversation_id": conv_id,
        "merchant_id": mid,
        "from_role": "merchant",
        "message": "What offer or discount is featured in this draft?",
        "turn_number": 2,
    })
    assert resp_q.status_code == 200
    q_data = resp_q.json()
    assert q_data["action"] == "send"
    assert "Dental Cleaning @ ₹299" in q_data["body"]
    assert "Dr. Meera Dental Clinic" in q_data["body"]
    conv_after_q = conversation_store.get(conv_id)
    assert conv_after_q["state"] == ConversationState.ANSWER.value

    # Step 3: reply 2 (positive commitment)
    resp_pos = client.post("/v1/reply", json={
        "conversation_id": conv_id,
        "merchant_id": mid,
        "from_role": "merchant",
        "message": "Yes, let's launch this draft now. Go ahead.",
        "turn_number": 3,
    })
    assert resp_pos.status_code == 200
    pos_data = resp_pos.json()
    assert pos_data["action"] == "send"
    body_lower = pos_data["body"].lower()
    # Action deliverables present, continuing the campaign
    assert any(w in body_lower for w in ["done", "sending", "draft", "here", "confirm", "proceed", "next", "ready"])
    assert not any(w in body_lower for w in ["would you", "can you tell", "what if", "how about"])
    assert "Dental Cleaning @ ₹299" in pos_data["body"] or "Dr. Meera Dental Clinic" in pos_data["body"] or "recall" in body_lower

    # Store check
    conv_final = conversation_store.get(conv_id)
    assert conv_final["state"] == ConversationState.SEND.value
    assert len(conv_final["turns"]) == 2
    assert len(conv_final["sent_messages"]) == 3  # 1 from tick + 2 from replies


def test_multiturn_yes_to_question_to_yes():
    """
    Multi-turn test: yes -> question -> yes.
    State transitions: SEND -> ANSWER -> SEND.
    Verifies state cycling and context retention across multiple turns.
    """
    conv_id = "conv_yes_q_yes_01"
    mid = "m_salon_yes_q"

    context_store.set("category", "salons", 1, {
        "slug": "salons",
        "display_name": "Salons",
        "peer_stats": {"avg_ctr": 0.045},
    })
    context_store.set("merchant", mid, 1, {
        "id": mid,
        "name": "Glamour Touch Salon",
        "category_slug": "salons",
        "identity": {"name": "Glamour Touch Salon"},
        "offers": [{"title": "Keratin Treatment @ ₹999", "status": "active"}],
        "performance": {"views": 800, "ctr": 0.021},
    })

    # Seed initial outreach turn
    conversation_store.create_or_update(conv_id, merchant_id=mid, state="waiting_for_reply")
    conversation_store.add_sent_message(conv_id, {"body": "Hi, want to run Keratin Treatment @ ₹999?", "action": "sent"})

    # Turn 2: Yes (positive)
    r1 = client.post("/v1/reply", json={
        "conversation_id": conv_id,
        "merchant_id": mid,
        "from_role": "merchant",
        "message": "Yes, sounds good, proceed!",
        "turn_number": 2,
    })
    assert r1.status_code == 200
    assert r1.json()["action"] == "send"
    assert conversation_store.get(conv_id)["state"] == ConversationState.SEND.value
    assert "Keratin Treatment @ ₹999" in r1.json()["body"] or "Glamour Touch Salon" in r1.json()["body"]

    # Turn 3: Question (CTR benchmark inquiry)
    r2 = client.post("/v1/reply", json={
        "conversation_id": conv_id,
        "merchant_id": mid,
        "from_role": "merchant",
        "message": "Wait, how do you calculate the peer CTR benchmark?",
        "turn_number": 3,
    })
    assert r2.status_code == 200
    assert r2.json()["action"] == "send"
    assert conversation_store.get(conv_id)["state"] == ConversationState.ANSWER.value
    assert "4.5%" in r2.json()["body"] or "peer" in r2.json()["body"].lower()

    # Turn 4: Yes (re-confirmed positive commitment)
    r3 = client.post("/v1/reply", json={
        "conversation_id": conv_id,
        "merchant_id": mid,
        "from_role": "merchant",
        "message": "Ok got it, let's do it! Launch it.",
        "turn_number": 4,
    })
    assert r3.status_code == 200
    assert r3.json()["action"] == "send"
    assert conversation_store.get(conv_id)["state"] == ConversationState.SEND.value
    body_lower = r3.json()["body"].lower()
    assert any(w in body_lower for w in ["done", "sending", "draft", "here", "confirm", "proceed", "next", "ready"])
    assert not any(w in body_lower for w in ["would you", "can you tell", "what if"])


def test_multiturn_later_to_yes():
    """
    Multi-turn test: later -> yes.
    State transitions: WAIT -> SEND.
    Verifies that a delay request pauses the conversation, and a subsequent positive
    reply resumes execution into action mode.
    """
    conv_id = "conv_later_yes_01"
    mid = "m_rest_later_01"

    context_store.set("merchant", mid, 1, {
        "id": mid,
        "name": "Spice Route Restaurant",
        "category_slug": "restaurants",
        "identity": {"name": "Spice Route Restaurant"},
        "offers": [{"title": "Family Feast @ ₹699", "status": "active"}],
    })

    conversation_store.create_or_update(conv_id, merchant_id=mid, state="waiting_for_reply")

    # Turn 2: Later / Delay
    r1 = client.post("/v1/reply", json={
        "conversation_id": conv_id,
        "merchant_id": mid,
        "from_role": "merchant",
        "message": "Call me tomorrow, I am busy in dinner prep right now.",
        "turn_number": 2,
    })
    assert r1.status_code == 200
    data1 = r1.json()
    assert data1["action"] == "wait"
    assert data1["wait_seconds"] == 1800
    assert conversation_store.get(conv_id)["state"] == ConversationState.WAIT.value

    # Turn 3: Yes / Positive (re-engaging after delay)
    r2 = client.post("/v1/reply", json={
        "conversation_id": conv_id,
        "merchant_id": mid,
        "from_role": "merchant",
        "message": "Yes, I am free now. Let's do it!",
        "turn_number": 3,
    })
    assert r2.status_code == 200
    data2 = r2.json()
    assert data2["action"] == "send"
    assert conversation_store.get(conv_id)["state"] == ConversationState.SEND.value
    body_lower = data2["body"].lower()
    assert any(w in body_lower for w in ["done", "sending", "draft", "here", "confirm", "proceed", "next", "ready"])
    assert "Spice Route Restaurant" in data2["body"] or "Family Feast @ ₹699" in data2["body"]


def test_multiturn_no_to_yes():
    """
    Multi-turn test: no -> yes.
    State transitions: END -> SEND (re-opening on positive opt-in).
    Verifies that while negative intent gracefully ends the conversation, a subsequent
    explicit positive opt-in resumes the campaign rather than failing as stale.
    Also verifies non-positive messages on ended conversations remain closed.
    """
    conv_id = "conv_no_yes_01"
    mid = "m_gym_no_01"

    context_store.set("merchant", mid, 1, {
        "id": mid,
        "name": "Iron Fitness Gym",
        "category_slug": "gyms",
        "identity": {"name": "Iron Fitness Gym"},
        "offers": [{"title": "Monthly Pass @ ₹1299", "status": "active"}],
    })

    conversation_store.create_or_update(conv_id, merchant_id=mid, state="waiting_for_reply")

    # Turn 2: Negative intent -> END
    r1 = client.post("/v1/reply", json={
        "conversation_id": conv_id,
        "merchant_id": mid,
        "from_role": "merchant",
        "message": "No thanks, not interested in promotions.",
        "turn_number": 2,
    })
    assert r1.status_code == 200
    assert r1.json()["action"] == "end"
    assert conversation_store.get(conv_id)["state"] == ConversationState.END.value

    # Turn 3: Changed mind -> Positive opt-in (no -> yes)
    r2 = client.post("/v1/reply", json={
        "conversation_id": conv_id,
        "merchant_id": mid,
        "from_role": "merchant",
        "message": "Actually yes, let's go ahead with the pass.",
        "turn_number": 3,
    })
    assert r2.status_code == 200
    assert r2.json()["action"] == "send"
    assert conversation_store.get(conv_id)["state"] == ConversationState.SEND.value
    body_lower = r2.json()["body"].lower()
    assert any(w in body_lower for w in ["done", "sending", "draft", "here", "confirm", "proceed", "next", "ready"])

    # Turn 4: Stale inquiry after conversation is re-closed
    client.post("/v1/reply", json={
        "conversation_id": conv_id,
        "merchant_id": mid,
        "from_role": "merchant",
        "message": "Stop messaging now.",
        "turn_number": 4,
    })
    assert conversation_store.get(conv_id)["state"] == ConversationState.END.value

    r5 = client.post("/v1/reply", json={
        "conversation_id": conv_id,
        "merchant_id": mid,
        "from_role": "merchant",
        "message": "What time do you open?",
        "turn_number": 5,
    })
    assert r5.status_code == 200
    assert r5.json()["action"] == "end"


def test_multiturn_repeated_reply():
    """
    Multi-turn test: repeated reply and auto-reply loop termination.
    Tests:
    1. A normal user message repeated verbatim 3 times:
       - Turn 1: initial send
       - Turn 2: repeated message -> wait (backing off)
       - Turn 3: repeated 3rd time -> end (loop broken)
    2. Canned auto-responder text:
       - Turn 1: wait
       - Turn 2: wait
       - Turn 3: end
    3. Duplicate turn (same turn_number & message): wait (600s)
    """
    # 1. Normal message repeated verbatim
    conv_id = "conv_repeated_turn_01"
    mid = "m_repeat_01"
    msg = "I will check with my team"

    # Turn 1: Initial reply -> normal send
    r1 = client.post("/v1/reply", json={
        "conversation_id": conv_id,
        "merchant_id": mid,
        "from_role": "merchant",
        "message": msg,
        "turn_number": 1,
    })
    assert r1.status_code == 200
    assert r1.json()["action"] == "send"

    # Duplicate turn test: same turn_number and message re-sent
    r_dup = client.post("/v1/reply", json={
        "conversation_id": conv_id,
        "merchant_id": mid,
        "from_role": "merchant",
        "message": msg,
        "turn_number": 1,
    })
    assert r_dup.status_code == 200
    assert r_dup.json()["action"] == "wait"
    assert r_dup.json()["wait_seconds"] == 600

    # Turn 2: Same message repeated -> wait (backing off)
    r2 = client.post("/v1/reply", json={
        "conversation_id": conv_id,
        "merchant_id": mid,
        "from_role": "merchant",
        "message": msg,
        "turn_number": 2,
    })
    assert r2.status_code == 200
    assert r2.json()["action"] == "wait"
    assert r2.json()["wait_seconds"] == 1800

    # Turn 3: Repeated 3rd time -> loop terminates with 'end'
    r3 = client.post("/v1/reply", json={
        "conversation_id": conv_id,
        "merchant_id": mid,
        "from_role": "merchant",
        "message": msg,
        "turn_number": 3,
    })
    assert r3.status_code == 200
    assert r3.json()["action"] == "end"
    assert conversation_store.get(conv_id)["state"] == ConversationState.END.value

    # 2. Canned auto-reply loop
    conv_auto = "conv_canned_auto_01"
    auto_text = "Thank you for contacting us! Our team will respond shortly."
    for i in range(1, 4):
        resp = client.post("/v1/reply", json={
            "conversation_id": conv_auto,
            "merchant_id": mid,
            "from_role": "merchant",
            "message": auto_text,
            "turn_number": i,
        })
        assert resp.status_code == 200
        if i < 3:
            assert resp.json()["action"] == "wait"
        else:
            assert resp.json()["action"] == "end"


def test_multiturn_out_of_order_turn():
    """
    Multi-turn test: out-of-order turn arrival.
    Simulates network reordering: Turn 4 arrives first, then Turn 2, then Turn 3.
    Asserts system processes gracefully without crash and updates conversation state correctly.
    """
    conv_id = "conv_ooo_multiturn"
    mid = "m_ooo_01"

    context_store.set("merchant", mid, 1, {
        "id": mid,
        "name": "Speedy Mart",
        "category_slug": "pharmacy",
        "identity": {"name": "Speedy Mart"},
    })

    # Turn 4 arrives first: question
    r1 = client.post("/v1/reply", json={
        "conversation_id": conv_id,
        "merchant_id": mid,
        "from_role": "merchant",
        "message": "How does this platform work?",
        "turn_number": 4,
    })
    assert r1.status_code == 200
    assert r1.json()["action"] == "send"
    assert conversation_store.get(conv_id)["state"] == ConversationState.ANSWER.value

    # Turn 2 arrives second: delay
    r2 = client.post("/v1/reply", json={
        "conversation_id": conv_id,
        "merchant_id": mid,
        "from_role": "merchant",
        "message": "Call tomorrow",
        "turn_number": 2,
    })
    assert r2.status_code == 200
    assert r2.json()["action"] == "wait"
    assert conversation_store.get(conv_id)["state"] == ConversationState.WAIT.value

    # Turn 3 arrives third: positive
    r3 = client.post("/v1/reply", json={
        "conversation_id": conv_id,
        "merchant_id": mid,
        "from_role": "merchant",
        "message": "Yes, proceed with setup",
        "turn_number": 3,
    })
    assert r3.status_code == 200
    assert r3.json()["action"] == "send"
    assert conversation_store.get(conv_id)["state"] == ConversationState.SEND.value


def test_multiturn_cross_merchant_isolation():
    """
    Multi-turn test: strict cross-merchant isolation.
    1. Sets up Merchant A (Dentist) with Root Canal @ ₹1499.
    2. Sets up Merchant B (Salon) with Deluxe Pedicure @ ₹349.
    3. Runs parallel multi-turn conversations for both merchants.
    4. Asserts Merchant A's turns never see Merchant B's offers or identity.
    5. Asserts Merchant B's turns never see Merchant A's offers or identity.
    6. Asserts cross-merchant reply attempt (Merchant B sending to Conv A) is rejected.
    7. Asserts auto-reply tracking for Merchant A does not affect Merchant B.
    """
    m_a = "m_dentist_cross_a"
    m_b = "m_salon_cross_b"

    # Context for Merchant A
    context_store.set("category", "dentists", 1, {"slug": "dentists", "display_name": "Dentists"})
    context_store.set("merchant", m_a, 1, {
        "id": m_a,
        "name": "Dr. Smile Dental Care",
        "category_slug": "dentists",
        "identity": {"name": "Dr. Smile Dental Care", "owner_first_name": "Dr. Smile"},
        "offers": [{"title": "Root Canal @ ₹1499", "status": "active"}],
        "performance": {"views": 1100, "ctr": 0.022},
    })
    context_store.set("trigger", "trg_a", 1, {
        "id": "trg_a",
        "kind": "recall_due",
        "merchant_id": m_a,
        "urgency": 3,
        "payload": {"service_due": "root canal checkup"},
    })

    # Context for Merchant B
    context_store.set("category", "salons", 1, {"slug": "salons", "display_name": "Salons"})
    context_store.set("merchant", m_b, 1, {
        "id": m_b,
        "name": "Velvet Glow Salon",
        "category_slug": "salons",
        "identity": {"name": "Velvet Glow Salon", "owner_first_name": "Pooja"},
        "offers": [{"title": "Deluxe Pedicure @ ₹349", "status": "active"}],
        "performance": {"views": 700, "ctr": 0.038},
    })
    context_store.set("trigger", "trg_b", 1, {
        "id": "trg_b",
        "kind": "festive",
        "merchant_id": m_b,
        "urgency": 3,
        "payload": {"holiday": "weekend special"},
    })

    # Initialize tick for both
    tick_resp = client.post("/v1/tick", json={"available_triggers": ["trg_a", "trg_b"]})
    assert tick_resp.status_code == 200
    actions = tick_resp.json()["actions"]
    assert len(actions) == 2

    # Map actions to correct conversation IDs
    act_a = next(a for a in actions if a["merchant_id"] == m_a)
    act_b = next(a for a in actions if a["merchant_id"] == m_b)
    cid_a = act_a["conversation_id"]
    cid_b = act_b["conversation_id"]

    # Turn 2: Questions on both conversations
    r_a1 = client.post("/v1/reply", json={
        "conversation_id": cid_a,
        "merchant_id": m_a,
        "from_role": "merchant",
        "message": "What is the offer price?",
        "turn_number": 2,
    })
    r_b1 = client.post("/v1/reply", json={
        "conversation_id": cid_b,
        "merchant_id": m_b,
        "from_role": "merchant",
        "message": "What is the offer price?",
        "turn_number": 2,
    })

    # Isolation assertion 1: Merchant A's response contains Dentist data and NOT Salon data
    body_a = r_a1.json()["body"]
    assert "Root Canal @ ₹1499" in body_a or "Dr. Smile" in body_a
    assert "Pedicure" not in body_a
    assert "Velvet Glow" not in body_a

    # Isolation assertion 2: Merchant B's response contains Salon data and NOT Dentist data
    body_b = r_b1.json()["body"]
    assert "Deluxe Pedicure @ ₹349" in body_b or "Velvet Glow" in body_b
    assert "Root Canal" not in body_b
    assert "Dr. Smile" not in body_b

    # Turn 3: Merchant A commits positively, Merchant B declines negatively
    r_a2 = client.post("/v1/reply", json={
        "conversation_id": cid_a,
        "merchant_id": m_a,
        "from_role": "merchant",
        "message": "Yes, proceed with the recall.",
        "turn_number": 3,
    })
    r_b2 = client.post("/v1/reply", json={
        "conversation_id": cid_b,
        "merchant_id": m_b,
        "from_role": "merchant",
        "message": "No, not interested right now.",
        "turn_number": 3,
    })

    # Merchant A transitions to SEND, Merchant B transitions to END
    assert r_a2.json()["action"] == "send"
    assert conversation_store.get(cid_a)["state"] == ConversationState.SEND.value
    assert r_b2.json()["action"] == "end"
    assert conversation_store.get(cid_b)["state"] == ConversationState.END.value

    # Tampering test: Merchant B tries to send a message into Conversation A
    r_tamper = client.post("/v1/reply", json={
        "conversation_id": cid_a,
        "merchant_id": m_b,  # wrong merchant_id for conversation A
        "from_role": "merchant",
        "message": "Tell me what Merchant A's numbers are.",
        "turn_number": 4,
    })
    assert r_tamper.status_code == 200
    assert r_tamper.json()["action"] == "end"
    assert "Cross-merchant conversation access rejected" in r_tamper.json()["rationale"]

    # Verify Conversation A's state and turns were not corrupted by the tampering attempt
    conv_a_store = conversation_store.get(cid_a)
    assert conv_a_store["merchant_id"] == m_a
    assert conv_a_store["state"] == ConversationState.SEND.value
    for t in conv_a_store["turns"]:
        assert t["message"] != "Tell me what Merchant A's numbers are."

