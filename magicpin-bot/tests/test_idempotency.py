"""Idempotency tests across /v1/context, /v1/tick, and /v1/reply for Magicpin Vera bot.

Asserts:
1. Repeated identical requests produce no duplicate actions, messages, or state mutations.
2. Exact duplicates are distinguished from legitimate new turns and new trigger versions.
3. Same turn number with changed message updates turn in place without duplicate entries.
4. Same message with changed turn number is processed as a legitimate subsequent turn.
5. Immediate replay after process_tick yields zero duplicate actions or sent messages.
6. Concurrent duplicate requests across all three endpoints execute race-free and deduplicate cleanly.
"""

import concurrent.futures
import pytest
from fastapi.testclient import TestClient

from app.conversation import ConversationState, conversation_state_machine
from app.decision_engine import process_tick, suppression_engine
from app.main import app
from app.store import context_store, conversation_store

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_state():
    """Reset all state stores before and after each test."""
    context_store.clear()
    conversation_store.clear()
    suppression_engine.clear()
    conversation_state_machine.reset()
    yield
    context_store.clear()
    conversation_store.clear()
    suppression_engine.clear()
    conversation_state_machine.reset()


def seed_standard_merchant_and_category(mid: str = "m_idem_01", cat_slug: str = "dentists"):
    context_store.set(
        "category",
        cat_slug,
        1,
        {
            "slug": cat_slug,
            "display_name": "Dentists & Clinics",
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
            "identity": {"name": "Apex Dental Clinic", "city": "Delhi"},
            "performance": {"ctr": 0.018, "views": 1200},
            "offers": [{"title": "Scaling & Polishing @ ₹499", "status": "active"}],
        },
    )


# ===========================================================================
# 1. Repeated Identical Requests
# ===========================================================================

def test_context_repeated_identical_requests():
    """Exact identical /v1/context pushes return 200 with the exact same ack_id and stored_at."""
    payload = {
        "scope": "merchant",
        "context_id": "m_test_repeat",
        "version": 1,
        "payload": {"name": "Test Merchant", "city": "Mumbai"},
    }

    r1 = client.post("/v1/context", json=payload)
    assert r1.status_code == 200
    data1 = r1.json()
    assert data1["accepted"] is True
    ack_id1 = data1["ack_id"]
    stored_at1 = data1["stored_at"]

    # Replay 1
    r2 = client.post("/v1/context", json=payload)
    assert r2.status_code == 200
    data2 = r2.json()
    assert data2["accepted"] is True
    assert data2["ack_id"] == ack_id1, "Replay must return identical ack_id"
    assert data2["stored_at"] == stored_at1, "Replay must return identical stored_at"

    # Replay 2
    r3 = client.post("/v1/context", json=payload)
    assert r3.status_code == 200
    data3 = r3.json()
    assert data3["accepted"] is True
    assert data3["ack_id"] == ack_id1
    assert data3["stored_at"] == stored_at1

    # Store must contain exactly one record
    assert context_store.count() == 1


def test_tick_repeated_identical_requests():
    """Replaying the exact same /v1/tick request produces 0 duplicate actions or sent messages."""
    seed_standard_merchant_and_category("m_tick_rep", "dentists")
    context_store.set(
        "trigger",
        "trg_rep_01",
        1,
        {
            "id": "trg_rep_01",
            "kind": "performance_drop",
            "merchant_id": "m_tick_rep",
            "urgency": 3,
            "suppression_key": "perf:m_tick_rep:2026-W17",
        },
    )

    tick_payload = {
        "now": "2026-04-26T10:00:00Z",
        "available_triggers": ["trg_rep_01"],
    }

    # Initial tick
    r1 = client.post("/v1/tick", json=tick_payload)
    assert r1.status_code == 200
    actions1 = r1.json().get("actions", [])
    assert len(actions1) == 1
    conv_id = actions1[0]["conversation_id"]

    conv = conversation_store.get(conv_id)
    assert conv is not None
    assert len(conv["sent_messages"]) == 1

    # Replay 1: same request payload
    r2 = client.post("/v1/tick", json=tick_payload)
    assert r2.status_code == 200
    assert r2.json().get("actions", []) == []

    # Replay 2: same request payload
    r3 = client.post("/v1/tick", json=tick_payload)
    assert r3.status_code == 200
    assert r3.json().get("actions", []) == []

    # Sent messages count must remain exactly 1 (no duplicate sent messages)
    conv_after = conversation_store.get(conv_id)
    assert len(conv_after["sent_messages"]) == 1


def test_reply_repeated_identical_requests():
    """Replaying identical /v1/reply returns wait without adding duplicate turns or mutating state."""
    seed_standard_merchant_and_category("m_rep_01", "dentists")
    conv_id = "conv_rep_replay_test"

    reply_payload = {
        "conversation_id": conv_id,
        "merchant_id": "m_rep_01",
        "message": "Yes please proceed with the offer",
        "turn_number": 2,
    }

    # Initial turn: positive -> state SEND
    r1 = client.post("/v1/reply", json=reply_payload)
    assert r1.status_code == 200
    assert r1.json()["action"] == "send"
    assert conversation_store.get(conv_id)["state"] == ConversationState.SEND.value
    assert len(conversation_store.get(conv_id)["turns"]) == 1
    assert len(conversation_store.get(conv_id)["sent_messages"]) == 1

    # Replay 1: identical turn
    r2 = client.post("/v1/reply", json=reply_payload)
    assert r2.status_code == 200
    assert r2.json()["action"] == "wait"
    assert r2.json()["wait_seconds"] == 600

    # State must remain SEND, turns and sent_messages must not duplicate
    conv = conversation_store.get(conv_id)
    assert conv["state"] == ConversationState.SEND.value
    assert len(conv["turns"]) == 1
    assert len(conv["sent_messages"]) == 1

    # Replay 2: identical turn again
    r3 = client.post("/v1/reply", json=reply_payload)
    assert r3.status_code == 200
    assert r3.json()["action"] == "wait"
    assert len(conversation_store.get(conv_id)["turns"]) == 1
    assert len(conversation_store.get(conv_id)["sent_messages"]) == 1


# ===========================================================================
# 2. Same Turn Number with Changed Message
# ===========================================================================

def test_reply_same_turn_number_with_changed_message():
    """Same turn number with changed message is a legitimate correction/update, not an exact duplicate."""
    seed_standard_merchant_and_category("m_change_msg", "dentists")
    conv_id = "conv_change_msg"

    # Turn 2: Question
    r1 = client.post("/v1/reply", json={
        "conversation_id": conv_id,
        "merchant_id": "m_change_msg",
        "message": "What is the offer pricing?",
        "turn_number": 2,
    })
    assert r1.status_code == 200
    assert r1.json()["action"] == "send"
    assert conversation_store.get(conv_id)["state"] == ConversationState.ANSWER.value
    assert len(conversation_store.get(conv_id)["turns"]) == 1
    assert conversation_store.get(conv_id)["turns"][0]["message"] == "What is the offer pricing?"

    # Turn 2 with CHANGED message: Negative cancellation
    r2 = client.post("/v1/reply", json={
        "conversation_id": conv_id,
        "merchant_id": "m_change_msg",
        "message": "Nevermind, do not contact me",
        "turn_number": 2,
    })
    assert r2.status_code == 200
    # Must NOT be rejected as duplicate "wait"
    assert r2.json()["action"] == "end"
    assert conversation_store.get(conv_id)["state"] == ConversationState.END.value

    # Turn 2 must be updated in place without duplicate entries in turns
    conv = conversation_store.get(conv_id)
    assert len(conv["turns"]) == 1
    assert conv["turns"][0]["message"] == "Nevermind, do not contact me"
    assert conv["turns"][0]["turn_number"] == 2


# ===========================================================================
# 3. Same Message with Changed Turn Number
# ===========================================================================

def test_reply_same_message_with_changed_turn_number():
    """Same message with changed turn number is a legitimate subsequent turn, not an exact duplicate."""
    seed_standard_merchant_and_category("m_changed_turn", "dentists")
    conv_id = "conv_changed_turn"
    msg = "I will check with my team"

    # Turn 1: Initial reply
    r1 = client.post("/v1/reply", json={
        "conversation_id": conv_id,
        "merchant_id": "m_changed_turn",
        "message": msg,
        "turn_number": 1,
    })
    assert r1.status_code == 200
    assert r1.json()["action"] == "send"
    assert len(conversation_store.get(conv_id)["turns"]) == 1

    # Exact duplicate replay of Turn 1 (same turn_number: 1)
    r1_dup = client.post("/v1/reply", json={
        "conversation_id": conv_id,
        "merchant_id": "m_changed_turn",
        "message": msg,
        "turn_number": 1,
    })
    assert r1_dup.status_code == 200
    assert r1_dup.json()["action"] == "wait"
    assert r1_dup.json()["wait_seconds"] == 600
    # Must NOT add duplicate turn
    assert len(conversation_store.get(conv_id)["turns"]) == 1

    # Turn 2: Same message, but changed turn number (legitimate subsequent turn)
    r2 = client.post("/v1/reply", json={
        "conversation_id": conv_id,
        "merchant_id": "m_changed_turn",
        "message": msg,
        "turn_number": 2,
    })
    assert r2.status_code == 200
    # Distinguishes from exact duplicate: advances turn, applies repeated reply backoff (1800s)
    assert r2.json()["action"] == "wait"
    assert r2.json()["wait_seconds"] == 1800

    # Conversation must record both turns in sequence
    conv = conversation_store.get(conv_id)
    assert len(conv["turns"]) == 2
    assert conv["turns"][0]["turn_number"] == 1
    assert conv["turns"][1]["turn_number"] == 2

    # Turn 3: 3rd repeated occurrence breaks loop and terminates
    r3 = client.post("/v1/reply", json={
        "conversation_id": conv_id,
        "merchant_id": "m_changed_turn",
        "message": msg,
        "turn_number": 3,
    })
    assert r3.status_code == 200
    assert r3.json()["action"] == "end"
    assert conversation_store.get(conv_id)["state"] == ConversationState.END.value
    assert len(conversation_store.get(conv_id)["turns"]) == 3


# ===========================================================================
# 4. Replay After process_tick
# ===========================================================================

def test_replay_after_process_tick():
    """Immediate replay after process_tick generates 0 duplicate actions or sent messages."""
    seed_standard_merchant_and_category("m_proc_tick", "dentists")
    context_store.set(
        "trigger",
        "trg_proc_01",
        1,
        {
            "id": "trg_proc_01",
            "kind": "compliance_alert",
            "merchant_id": "m_proc_tick",
            "urgency": 4,
            "suppression_key": "compliance:m_proc_tick:2026-W17",
        },
    )

    now = "2026-04-26T10:00:00Z"

    # Execution 1
    actions1 = process_tick(
        available_trigger_ids=["trg_proc_01"],
        now=now,
        context_store=context_store,
        conversation_store=conversation_store,
    )
    assert len(actions1) == 1
    conv_id = actions1[0].conversation_id
    assert len(conversation_store.get(conv_id)["sent_messages"]) == 1

    # Immediate replay after process_tick
    actions2 = process_tick(
        available_trigger_ids=["trg_proc_01"],
        now=now,
        context_store=context_store,
        conversation_store=conversation_store,
    )
    assert actions2 == []

    # Sent messages must remain 1
    assert len(conversation_store.get(conv_id)["sent_messages"]) == 1


def test_replay_after_process_tick_without_explicit_suppression_key():
    """Triggers without explicit suppression_key are still idempotent against immediate replay."""
    seed_standard_merchant_and_category("m_no_sup", "dentists")
    context_store.set(
        "trigger",
        "trg_no_sup",
        1,
        {
            "id": "trg_no_sup",
            "kind": "curious_ask",
            "merchant_id": "m_no_sup",
            "urgency": 2,
            # No suppression_key provided
        },
    )

    now = "2026-04-26T10:00:00Z"

    # Execution 1
    actions1 = process_tick(
        available_trigger_ids=["trg_no_sup"],
        now=now,
        context_store=context_store,
        conversation_store=conversation_store,
    )
    assert len(actions1) == 1
    assert actions1[0].suppression_key is None  # Contract preserved
    conv_id = actions1[0].conversation_id
    assert len(conversation_store.get(conv_id)["sent_messages"]) == 1

    # Immediate replay
    actions2 = process_tick(
        available_trigger_ids=["trg_no_sup"],
        now=now,
        context_store=context_store,
        conversation_store=conversation_store,
    )
    assert actions2 == []
    assert len(conversation_store.get(conv_id)["sent_messages"]) == 1


# ===========================================================================
# 5. Distinguish Exact Duplicates from New Trigger Versions
# ===========================================================================

def test_context_distinguish_exact_duplicate_from_new_version():
    """Version bumps are accepted and replace context, while identical versions are idempotent no-ops."""
    context_id = "trg_version_test"

    # Version 1 initial push
    r1 = client.post("/v1/context", json={
        "scope": "trigger",
        "context_id": context_id,
        "version": 1,
        "payload": {"topic": "initial_data", "urgency": 1},
    })
    assert r1.status_code == 200
    ack1 = r1.json()["ack_id"]
    stored1 = r1.json()["stored_at"]

    # Replay Version 1 (exact duplicate)
    r1_rep = client.post("/v1/context", json={
        "scope": "trigger",
        "context_id": context_id,
        "version": 1,
        "payload": {"topic": "initial_data", "urgency": 1},
    })
    assert r1_rep.status_code == 200
    assert r1_rep.json()["ack_id"] == ack1
    assert r1_rep.json()["stored_at"] == stored1
    assert context_store.count() == 1

    # Version 2 (legitimate new trigger version)
    r2 = client.post("/v1/context", json={
        "scope": "trigger",
        "context_id": context_id,
        "version": 2,
        "payload": {"topic": "updated_data", "urgency": 3},
    })
    assert r2.status_code == 200
    data2 = r2.json()
    assert data2["accepted"] is True
    # Must get new ack_id
    assert data2["ack_id"] != ack1
    assert context_store.count() == 1

    # Verify updated payload
    stored_item = context_store.get("trigger", context_id)
    assert stored_item["version"] == 2
    assert stored_item["payload"]["topic"] == "updated_data"

    # Stale version 1 after version 2 must return 409
    r_stale = client.post("/v1/context", json={
        "scope": "trigger",
        "context_id": context_id,
        "version": 1,
        "payload": {"topic": "old_data"},
    })
    assert r_stale.status_code == 409
    assert r_stale.json()["accepted"] is False
    assert r_stale.json()["reason"] == "stale_version"
    assert r_stale.json()["current_version"] == 2


# ===========================================================================
# 6. Omitted turn_number Replay Handling
# ===========================================================================

def test_reply_omitted_turn_number_replay():
    """Replaying a reply request where turn_number is omitted is properly deduplicated."""
    seed_standard_merchant_and_category("m_no_turn_num", "dentists")
    conv_id = "conv_no_turn_num"

    payload = {
        "conversation_id": conv_id,
        "merchant_id": "m_no_turn_num",
        "message": "Yes, schedule this please",
        # turn_number omitted
    }

    r1 = client.post("/v1/reply", json=payload)
    assert r1.status_code == 200
    assert r1.json()["action"] == "send"
    assert len(conversation_store.get(conv_id)["turns"]) == 1

    # Replay with turn_number omitted
    r2 = client.post("/v1/reply", json=payload)
    assert r2.status_code == 200
    assert r2.json()["action"] == "wait"
    assert len(conversation_store.get(conv_id)["turns"]) == 1
    assert len(conversation_store.get(conv_id)["sent_messages"]) == 1


# ===========================================================================
# 7. Concurrent Duplicate Requests
# ===========================================================================

def test_concurrent_duplicate_context_requests():
    """Concurrent identical context requests all return 200 and store exactly one entry without race conditions."""
    payload = {
        "scope": "merchant",
        "context_id": "m_concurrent",
        "version": 1,
        "payload": {"name": "Concurrent Merchant", "city": "Bengaluru"},
    }

    def send_context():
        return client.post("/v1/context", json=payload)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(send_context) for _ in range(10)]
        responses = [f.result() for f in futures]

    for resp in responses:
        assert resp.status_code == 200
        assert resp.json()["accepted"] is True

    # Store has exactly 1 entry
    assert context_store.count() == 1


def test_concurrent_duplicate_tick_requests():
    """Concurrent identical /v1/tick requests execute race-free and append exactly one sent message."""
    seed_standard_merchant_and_category("m_conc_tick", "dentists")
    context_store.set(
        "trigger",
        "trg_conc_tick",
        1,
        {
            "id": "trg_conc_tick",
            "kind": "performance_drop",
            "merchant_id": "m_conc_tick",
            "urgency": 3,
            "suppression_key": "perf:m_conc_tick:2026-W17",
        },
    )

    tick_payload = {
        "now": "2026-04-26T10:00:00Z",
        "available_triggers": ["trg_conc_tick"],
    }

    def send_tick():
        return client.post("/v1/tick", json=tick_payload)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(send_tick) for _ in range(10)]
        responses = [f.result() for f in futures]

    # Every request succeeded with 200
    for resp in responses:
        assert resp.status_code == 200

    # Exactly one request produced an action, other concurrent requests were suppressed
    total_actions = sum(len(resp.json().get("actions", [])) for resp in responses)
    assert total_actions == 1

    # Verify conversation store has exactly 1 conversation with 1 sent message
    assert conversation_store.count() == 1
    conv = list(conversation_store._conversations.values())[0]
    assert len(conv["sent_messages"]) == 1


def test_concurrent_duplicate_reply_requests():
    """Concurrent identical /v1/reply requests execute race-free with exactly one logical action taken."""
    seed_standard_merchant_and_category("m_conc_reply", "dentists")
    conv_id = "conv_concurrent_reply"

    reply_payload = {
        "conversation_id": conv_id,
        "merchant_id": "m_conc_reply",
        "message": "Yes proceed immediately",
        "turn_number": 2,
    }

    def send_reply():
        return client.post("/v1/reply", json=reply_payload)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(send_reply) for _ in range(10)]
        responses = [f.result() for f in futures]

    for resp in responses:
        assert resp.status_code == 200

    # Exactly 1 request executed the "send" action, other 9 returned "wait" (duplicate detected)
    actions = [resp.json()["action"] for resp in responses]
    assert actions.count("send") == 1
    assert actions.count("wait") == 9

    # Conversation store has exactly 1 turn and 1 sent message
    conv = conversation_store.get(conv_id)
    assert conv is not None
    assert conv["state"] == ConversationState.SEND.value
    assert len(conv["turns"]) == 1
    assert len(conv["sent_messages"]) == 1
