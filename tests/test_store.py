"""Unit tests for ContextStore and ConversationStore in app/store.py."""

from app.store import ContextStore, ConversationStore, VersionGateResult


def test_context_store_version_gating():
    store = ContextStore()

    # 1. New entry
    res, cur = store.set("merchant", "m1", 1, {"name": "M1"})
    assert res == VersionGateResult.CREATED
    assert cur is None
    assert store.get("merchant", "m1")["version"] == 1

    # 2. Same version
    res, cur = store.set("merchant", "m1", 1, {"name": "M1"})
    assert res == VersionGateResult.IDEMPOTENT
    assert cur == 1

    # 3. Newer version
    res, cur = store.set("merchant", "m1", 3, {"name": "M1_v3"})
    assert res == VersionGateResult.REPLACED
    assert cur == 1
    assert store.get("merchant", "m1")["version"] == 3
    assert store.get("merchant", "m1")["payload"]["name"] == "M1_v3"

    # 4. Older version
    res, cur = store.set("merchant", "m1", 2, {"name": "M1_v2"})
    assert res == VersionGateResult.STALE
    assert cur == 3
    assert store.get("merchant", "m1")["version"] == 3

    # Counts
    counts = store.get_counts()
    assert counts["merchant"] == 1
    assert counts["category"] == 0

    # Clear
    store.clear()
    assert store.count() == 0
    assert store.get("merchant", "m1") is None


def test_conversation_store():
    store = ConversationStore()

    # Create conversation
    conv = store.create_or_update(
        conversation_id="conv_100",
        merchant_id="m_100",
        customer_id="c_100",
        trigger_id="trg_100",
        state="active",
        last_action="initiated",
    )
    assert conv["merchant_id"] == "m_100"
    assert conv["customer_id"] == "c_100"
    assert conv["trigger_id"] == "trg_100"
    assert conv["state"] == "active"
    assert conv["last_action"] == "initiated"
    assert conv["turns"] == []
    assert conv["sent_messages"] == []

    # Add turn
    store.add_turn("conv_100", {"role": "merchant", "message": "hello", "turn_number": 1})
    # Add sent message
    store.add_sent_message("conv_100", {"body": "hi", "timestamp": "2026-04-26T10:00:00Z"})
    # Update action and state
    store.set_last_action("conv_100", "wait")
    store.set_state("conv_100", "waiting_for_reply")

    retrieved = store.get("conv_100")
    assert retrieved is not None
    assert len(retrieved["turns"]) == 1
    assert retrieved["turns"][0]["message"] == "hello"
    assert len(retrieved["sent_messages"]) == 1
    assert retrieved["sent_messages"][0]["body"] == "hi"
    assert retrieved["last_action"] == "wait"
    assert retrieved["state"] == "waiting_for_reply"

    assert store.count() == 1
    store.clear()
    assert store.count() == 0
    assert store.get("conv_100") is None
