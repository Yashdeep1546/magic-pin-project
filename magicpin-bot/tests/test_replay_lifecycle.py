"""Tests for the deterministic lifecycle replay harness.

Validates that given the same ordered sequence of context pushes, ticks, and replies:
1. Logical decisions, selected trigger IDs, suppression decisions, conversation transitions,
   and fallback template rendering remain 100% identical across repeated runs.
2. Nondeterministic values (generated UUIDs, request IDs, volatile timestamps) are ignored/normalized.
3. Covers multi-merchant scenarios, multiple competing triggers, context updates & stale versions,
   duplicate request idempotency, and multi-turn conversations.
"""

import json
from pathlib import Path
import pytest

from app.replay_harness import (
    ContextEvent,
    EventType,
    ReplayHarness,
    ReplyEvent,
    TickEvent,
)


@pytest.fixture
def harness():
    return ReplayHarness()


def test_replay_multi_merchant_lifecycle(harness):
    """
    Test determinism across multiple merchants in different categories:
    Interleaved context pushes, multi-merchant tick, and independent replies.
    """
    events = [
        # 1. Categories
        ContextEvent.category("dentists", 1, {
            "slug": "dentists",
            "display_name": "Dentists & Clinics",
            "voice": {"tone": "clinical_peer"},
            "peer_stats": {"avg_ctr": 0.030},
        }),
        ContextEvent.category("salons", 1, {
            "slug": "salons",
            "display_name": "Hair & Beauty Salons",
            "voice": {"tone": "friendly"},
            "peer_stats": {"avg_ctr": 0.025},
        }),
        ContextEvent.category("pharmacies", 1, {
            "slug": "pharmacies",
            "display_name": "Pharmacies",
            "voice": {"tone": "informative"},
            "peer_stats": {"avg_ctr": 0.020},
        }),

        # 2. Merchants
        ContextEvent.merchant("m_dent_01", 1, {
            "merchant_id": "m_dent_01",
            "category_slug": "dentists",
            "identity": {"name": "Dr. Sharma Dental", "city": "Delhi"},
            "performance": {"ctr": 0.015, "views": 900},
            "offers": [{"title": "Scaling & Polishing @ ₹399", "status": "active"}],
        }),
        ContextEvent.merchant("m_salon_01", 1, {
            "merchant_id": "m_salon_01",
            "category_slug": "salons",
            "identity": {"name": "Glamour Studio", "city": "Mumbai"},
            "performance": {"ctr": 0.010, "views": 1200},
            "offers": [{"title": "Hair Spa Flat 30% Off", "status": "active"}],
        }),
        ContextEvent.merchant("m_pharm_01", 1, {
            "merchant_id": "m_pharm_01",
            "category_slug": "pharmacies",
            "identity": {"name": "Apollo Chemist", "city": "Bengaluru"},
            "performance": {"ctr": 0.018, "views": 600},
            "offers": [{"title": "Health Checkup Package", "status": "active"}],
        }),

        # 3. Triggers for each merchant
        ContextEvent.trigger("trg_dent", 1, {
            "id": "trg_dent",
            "kind": "compliance_alert",
            "merchant_id": "m_dent_01",
            "urgency": 4,
            "suppression_key": "sup_dent_w17",
        }),
        ContextEvent.trigger("trg_salon", 1, {
            "id": "trg_salon",
            "kind": "performance_drop",
            "merchant_id": "m_salon_01",
            "urgency": 3,
            "suppression_key": "sup_salon_w17",
        }),
        ContextEvent.trigger("trg_pharm", 1, {
            "id": "trg_pharm",
            "kind": "festival",
            "merchant_id": "m_pharm_01",
            "urgency": 2,
            "suppression_key": "sup_pharm_w17",
        }),

        # 4. Tick wake-up evaluating all 3 triggers
        TickEvent(
            available_triggers=["trg_dent", "trg_salon", "trg_pharm"],
            now="2026-04-26T12:00:00Z",
        ),

        # 5. Replies to each merchant's conversation
        ReplyEvent(
            conversation_id="$merchant:m_dent_01",
            merchant_id="m_dent_01",
            message="haan bhai details send kar do",
            turn_number=2,
        ),
        ReplyEvent(
            conversation_id="$merchant:m_salon_01",
            merchant_id="m_salon_01",
            message="kal call karo abhi busy hoon",
            turn_number=2,
        ),
        ReplyEvent(
            conversation_id="$merchant:m_pharm_01",
            merchant_id="m_pharm_01",
            message="nahi chahiye band karo",
            turn_number=2,
        ),
    ]

    canonical_trace = harness.assert_replays_identical(
        events,
        repetitions=3,
        trace_name="multi_merchant_lifecycle",
    )

    # Validate high-level logical assertions on canonical trace
    assert canonical_trace.summary["total_steps"] == 13
    assert canonical_trace.summary["context_events"] == 9
    assert canonical_trace.summary["tick_events"] == 1
    assert canonical_trace.summary["reply_events"] == 3
    assert canonical_trace.summary["actions_generated"] == 3

    tick_step = canonical_trace.steps[9]
    assert tick_step.event_type == EventType.TICK.value
    actions = tick_step.output["actions"]
    assert len(actions) == 3
    m_ids = [a["merchant_id"] for a in actions]
    assert set(m_ids) == {"m_dent_01", "m_salon_01", "m_pharm_01"}

    # Validate reply actions
    dent_reply = canonical_trace.steps[10]
    assert dent_reply.output["action"] == "send"
    assert dent_reply.output["post_state"] == "SEND"

    salon_reply = canonical_trace.steps[11]
    assert salon_reply.output["action"] == "wait"
    assert salon_reply.output["wait_seconds"] == 1800
    assert salon_reply.output["post_state"] == "WAIT"

    pharm_reply = canonical_trace.steps[12]
    assert pharm_reply.output["action"] == "end"
    assert pharm_reply.output["post_state"] == "END"


def test_replay_multi_trigger_ranking_and_suppression(harness):
    """
    Test determinism for multiple competing triggers for a single merchant:
    Evaluates ranking, tie-breaking, suppression activation, and progressive next-best trigger selection.
    """
    events = [
        ContextEvent.category("dentists", 1, {
            "slug": "dentists",
            "display_name": "Dentists",
            "peer_stats": {"avg_ctr": 0.030},
        }),
        ContextEvent.merchant("m_dent_compete", 1, {
            "merchant_id": "m_dent_compete",
            "category_slug": "dentists",
            "identity": {"name": "Smile Care", "city": "Delhi"},
            "performance": {"ctr": 0.015, "views": 1000},
            "offers": [{"title": "Checkup @ ₹199", "status": "active"}],
        }),
        # 4 Triggers with decreasing priority
        ContextEvent.trigger("trg_01_comp", 1, {
            "id": "trg_01_comp",
            "kind": "compliance_alert",
            "merchant_id": "m_dent_compete",
            "urgency": 4,
            "suppression_key": "sup_dent_comp:2026-W17",
        }),
        ContextEvent.trigger("trg_02_perf", 1, {
            "id": "trg_02_perf",
            "kind": "performance_drop",
            "merchant_id": "m_dent_compete",
            "urgency": 3,
            "suppression_key": "sup_dent_perf:2026-W17",
        }),
        ContextEvent.trigger("trg_03_fest", 1, {
            "id": "trg_03_fest",
            "kind": "festival",
            "merchant_id": "m_dent_compete",
            "urgency": 2,
            "suppression_key": "sup_dent_fest:2026-W17",
        }),
        ContextEvent.trigger("trg_04_curious", 1, {
            "id": "trg_04_curious",
            "kind": "curious_ask",
            "merchant_id": "m_dent_compete",
            "urgency": 1,
            "suppression_key": "sup_dent_curious:2026-W17",
        }),

        # Tick 1: Must select highest priority (trg_01_comp)
        TickEvent(
            available_triggers=["trg_01_comp", "trg_02_perf", "trg_03_fest", "trg_04_curious"],
            now="2026-04-26T10:00:00Z",
        ),

        # Tick 2: trg_01_comp is now suppressed; must select trg_02_perf
        TickEvent(
            available_triggers=["trg_01_comp", "trg_02_perf", "trg_03_fest", "trg_04_curious"],
            now="2026-04-26T10:05:00Z",
        ),

        # Tick 3: trg_01_comp and trg_02_perf are suppressed; must select trg_03_fest
        TickEvent(
            available_triggers=["trg_01_comp", "trg_02_perf", "trg_03_fest", "trg_04_curious"],
            now="2026-04-26T10:10:00Z",
        ),
    ]

    canonical_trace = harness.assert_replays_identical(
        events,
        repetitions=3,
        trace_name="multi_trigger_ranking_lifecycle",
    )

    tick1 = canonical_trace.steps[6]
    tick2 = canonical_trace.steps[7]
    tick3 = canonical_trace.steps[8]

    assert tick1.output["actions"][0]["trigger_id"] == "trg_01_comp"
    assert tick2.output["actions"][0]["trigger_id"] == "trg_02_perf"
    assert tick3.output["actions"][0]["trigger_id"] == "trg_03_fest"

    # Confirm suppression records grew deterministically
    assert len(tick1.system_state["active_suppressions"]) >= 2  # trigger key + effective trg key
    assert len(tick2.system_state["active_suppressions"]) >= 4
    assert len(tick3.system_state["active_suppressions"]) >= 6


def test_replay_context_updates_and_stale_versions(harness):
    """
    Test determinism for context updates, idempotent pushes, and stale version conflict handling.
    """
    events = [
        # Initial Category v1
        ContextEvent.category("dentists", 1, {
            "slug": "dentists",
            "display_name": "Dentists",
            "peer_stats": {"avg_ctr": 0.020},
        }),
        # Initial Merchant v2
        ContextEvent.merchant("m_update_01", 2, {
            "merchant_id": "m_update_01",
            "category_slug": "dentists",
            "identity": {"name": "Apex Dental v2", "city": "Delhi"},
            "performance": {"ctr": 0.010, "views": 500},
            "offers": [{"title": "Old Offer", "status": "active"}],
        }),
        # Trigger
        ContextEvent.trigger("trg_update", 1, {
            "id": "trg_update",
            "kind": "performance_drop",
            "merchant_id": "m_update_01",
            "urgency": 3,
            "suppression_key": "sup_update_key:2026-04-26",
        }),

        # Tick 1: Runs against v2
        TickEvent(
            available_triggers=["trg_update"],
            now="2026-04-26T08:00:00Z",
        ),

        # Attempt stale version push (version 1 < 2) -> 409
        ContextEvent.merchant("m_update_01", 1, {
            "merchant_id": "m_update_01",
            "category_slug": "dentists",
            "identity": {"name": "Apex Dental v1"},
            "performance": {"ctr": 0.005},
        }),

        # Attempt idempotent same version push (version 2 == 2) -> 200 accepted
        ContextEvent.merchant("m_update_01", 2, {
            "merchant_id": "m_update_01",
            "category_slug": "dentists",
            "identity": {"name": "Apex Dental v2", "city": "Delhi"},
            "performance": {"ctr": 0.010, "views": 500},
            "offers": [{"title": "Old Offer", "status": "active"}],
        }),

        # Valid updated version (version 3 > 2) -> 200 replaced
        ContextEvent.merchant("m_update_01", 3, {
            "merchant_id": "m_update_01",
            "category_slug": "dentists",
            "identity": {"name": "Apex Dental Premium", "city": "Delhi"},
            "performance": {"ctr": 0.035, "views": 2500},
            "offers": [{"title": "Premium Root Canal @ ₹999", "status": "active"}],
        }),

        # New trigger for v3
        ContextEvent.trigger("trg_v3", 1, {
            "id": "trg_v3",
            "kind": "performance_drop",
            "merchant_id": "m_update_01",
            "urgency": 3,
            "suppression_key": "sup_v3_key:2026-04-26",
        }),

        # Tick 2: Runs against updated merchant v3
        TickEvent(
            available_triggers=["trg_v3"],
            now="2026-04-26T09:00:00Z",
        ),
    ]

    canonical_trace = harness.assert_replays_identical(
        events,
        repetitions=3,
        trace_name="context_updates_lifecycle",
    )

    # Check stale response
    stale_step = canonical_trace.steps[4]
    assert stale_step.status_code == 409
    assert stale_step.output["accepted"] is False
    assert stale_step.output["reason"] == "stale_version"
    assert stale_step.output["current_version"] == 2

    # Check idempotent response
    idemp_step = canonical_trace.steps[5]
    assert idemp_step.status_code == 200
    assert idemp_step.output["accepted"] is True

    # Check update v3 response
    update_step = canonical_trace.steps[6]
    assert update_step.status_code == 200
    assert update_step.output["accepted"] is True


def test_replay_duplicate_request_idempotency(harness):
    """
    Test determinism with duplicate tick requests, duplicate context pushes, and duplicate replies.
    """
    events = [
        ContextEvent.category("salons", 1, {
            "slug": "salons",
            "display_name": "Salons",
            "peer_stats": {"avg_ctr": 0.025},
        }),
        ContextEvent.merchant("m_idem_01", 1, {
            "merchant_id": "m_idem_01",
            "category_slug": "salons",
            "identity": {"name": "Luxe Salon", "city": "Gurugram"},
            "performance": {"ctr": 0.012, "views": 800},
            "offers": [{"title": "Keratin Treatment @ ₹1999", "status": "active"}],
        }),
        ContextEvent.trigger("trg_idem", 1, {
            "id": "trg_idem",
            "kind": "compliance_alert",
            "merchant_id": "m_idem_01",
            "urgency": 4,
            "suppression_key": "sup_idem_key:2026-04-26",
        }),

        # Tick 1: First tick generates action and suppresses
        TickEvent(
            available_triggers=["trg_idem"],
            now="2026-04-26T11:00:00Z",
        ),

        # Immediate Duplicate Tick (same now, same triggers) -> 0 actions because trigger is suppressed
        TickEvent(
            available_triggers=["trg_idem"],
            now="2026-04-26T11:00:00Z",
        ),

        # Reply 1: Turn 2 question
        ReplyEvent(
            conversation_id="$latest",
            merchant_id="m_idem_01",
            message="What are the details of the offer?",
            turn_number=2,
        ),

        # Duplicate Reply 1: Exact same turn number and message -> idempotent cached result
        ReplyEvent(
            conversation_id="$latest",
            merchant_id="m_idem_01",
            message="What are the details of the offer?",
            turn_number=2,
        ),

        # Reply 2: Turn 3 positive
        ReplyEvent(
            conversation_id="$latest",
            merchant_id="m_idem_01",
            message="haan bhai start kar do",
            turn_number=3,
        ),
    ]

    canonical_trace = harness.assert_replays_identical(
        events,
        repetitions=3,
        trace_name="idempotency_lifecycle",
    )

    tick1 = canonical_trace.steps[3]
    tick2 = canonical_trace.steps[4]

    assert len(tick1.output["actions"]) == 1
    assert len(tick2.output["actions"]) == 0  # Idempotently suppressed

    reply1 = canonical_trace.steps[5]
    reply1_dup = canonical_trace.steps[6]
    reply2 = canonical_trace.steps[7]

    assert reply1.output["action"] == "send"
    assert reply1_dup.output["action"] == "wait"  # Repeated message detected as auto-reply/loop
    assert reply1_dup.output["wait_seconds"] == 600
    assert reply1_dup.output["post_state"] == "ANSWER"  # State preserved idempotently

    assert reply2.output["action"] == "send"
    assert reply2.output["post_state"] == "SEND"


def test_replay_multi_turn_conversations(harness):
    """
    Test determinism for a complete multi-turn conversation covering:
    INITIAL_MESSAGE -> QUESTION (ANSWER) -> DELAY (WAIT) -> POSITIVE (SEND) ->
    NEGATIVE (END) -> REJECTED IMPOSSIBLE TRANSITION -> REOPEN POSITIVE (SEND).
    """
    events = [
        ContextEvent.category("dentists", 1, {
            "slug": "dentists",
            "display_name": "Dentists",
            "peer_stats": {"avg_ctr": 0.030},
        }),
        ContextEvent.merchant("m_dialogue_01", 1, {
            "merchant_id": "m_dialogue_01",
            "category_slug": "dentists",
            "identity": {"name": "Cure Dental", "city": "Noida"},
            "performance": {"ctr": 0.018, "views": 750},
            "offers": [{"title": "Teeth Whitening Flat 25% Off", "status": "active"}],
        }),
        ContextEvent.trigger("trg_diag", 1, {
            "id": "trg_diag",
            "kind": "curious_ask",
            "merchant_id": "m_dialogue_01",
            "urgency": 2,
            "suppression_key": "sup_diag_key:2026-04-26",
        }),

        # Turn 1: Outbound proactive tick
        TickEvent(
            available_triggers=["trg_diag"],
            now="2026-04-26T14:00:00Z",
        ),

        # Turn 2: Question -> State ANSWER
        ReplyEvent(
            conversation_id="$latest",
            merchant_id="m_dialogue_01",
            message="Can you explain how this helps my clinic?",
            turn_number=2,
        ),

        # Turn 3: Delay -> State WAIT
        ReplyEvent(
            conversation_id="$latest",
            merchant_id="m_dialogue_01",
            message="Kal call karna, abhi patient dekh raha hoon",
            turn_number=3,
        ),

        # Turn 4: Positive confirmation -> State SEND
        ReplyEvent(
            conversation_id="$latest",
            merchant_id="m_dialogue_01",
            message="Haan theek hai, go ahead and activate it",
            turn_number=4,
        ),

        # Turn 5: Negative -> State END (terminal)
        ReplyEvent(
            conversation_id="$latest",
            merchant_id="m_dialogue_01",
            message="Abhi ke liye nahi chahiye, please stop",
            turn_number=5,
        ),

        # Turn 6: Out-of-order delay request on END state -> Rejected impossible transition
        ReplyEvent(
            conversation_id="$latest",
            merchant_id="m_dialogue_01",
            message="Kal baat karte hain",
            turn_number=6,
        ),

        # Turn 7: Explicit positive opt-in reopens ended conversation -> State SEND
        ReplyEvent(
            conversation_id="$latest",
            merchant_id="m_dialogue_01",
            message="Actually haanji, please restart the campaign",
            turn_number=7,
        ),
    ]

    canonical_trace = harness.assert_replays_identical(
        events,
        repetitions=3,
        trace_name="multi_turn_dialogue_lifecycle",
    )

    # Validate transition sequence
    steps = canonical_trace.steps

    # Turn 2: QUESTION -> ANSWER
    assert steps[4].output["action"] == "send"
    assert steps[4].output["post_state"] == "ANSWER"

    # Turn 3: DELAY -> WAIT
    assert steps[5].output["action"] == "wait"
    assert steps[5].output["wait_seconds"] == 1800
    assert steps[5].output["post_state"] == "WAIT"

    # Turn 4: POSITIVE -> SEND
    assert steps[6].output["action"] == "send"
    assert steps[6].output["post_state"] == "SEND"

    # Turn 5: NEGATIVE -> END
    assert steps[7].output["action"] == "end"
    assert steps[7].output["post_state"] == "END"

    # Turn 6: DELAY on END -> REJECTED, stays in END
    assert steps[8].output["action"] == "end"
    assert steps[8].output["post_state"] == "END"
    assert "Rejected impossible transition" in steps[8].output["rationale"]

    # Turn 7: POSITIVE on END -> REOPEN to SEND
    assert steps[9].output["action"] == "send"
    assert steps[9].output["post_state"] == "SEND"


def test_replay_official_dataset_fixtures(harness):
    """
    Test deterministic lifecycle replay using official dataset JSON files.
    """
    base_dir = Path(__file__).parent.parent / "dataset"
    cat_file = base_dir / "categories" / "dentists.json"
    merch_file = base_dir / "merchants" / "m_001_drmeera_dentist_delhi.json"
    trg_file = base_dir / "triggers" / "trg_001_research_digest_dentists.json"

    if not (cat_file.exists() and merch_file.exists() and trg_file.exists()):
        pytest.skip("Official dataset files not present in expected path.")

    with open(cat_file, "r", encoding="utf-8") as f:
        cat_data = json.load(f)
    with open(merch_file, "r", encoding="utf-8") as f:
        merch_data = json.load(f)
    with open(trg_file, "r", encoding="utf-8") as f:
        trg_data = json.load(f)

    events = [
        ContextEvent.category(cat_data["slug"], 1, cat_data),
        ContextEvent.merchant(merch_data["merchant_id"], 1, merch_data),
        ContextEvent.trigger(trg_data["id"], 1, trg_data),
        TickEvent(
            available_triggers=[trg_data["id"]],
            now="2026-04-26T12:00:00Z",
        ),
        ReplyEvent(
            conversation_id="$latest",
            merchant_id=merch_data["merchant_id"],
            message="haan send kar do",
            turn_number=2,
        ),
    ]

    canonical_trace = harness.assert_replays_identical(
        events,
        repetitions=3,
        trace_name="dataset_fixture_lifecycle",
    )

    tick_step = canonical_trace.steps[3]
    assert len(tick_step.output["actions"]) == 1
    action = tick_step.output["actions"][0]
    assert action["trigger_id"] == trg_data["id"]
    assert action["merchant_id"] == merch_data["merchant_id"]
    assert action["template_name"] == "template_research_digest_v1"

    reply_step = canonical_trace.steps[4]
    assert reply_step.output["action"] == "send"
    assert reply_step.output["post_state"] == "SEND"


def test_replay_raw_dict_events_format(harness):
    """
    Test that events can be supplied as raw dictionaries (e.g. from JSON or event logs).
    """
    raw_events = [
        {
            "event": "context",
            "scope": "category",
            "context_id": "dentists",
            "version": 1,
            "payload": {"slug": "dentists", "display_name": "Dentists"},
        },
        {
            "event": "context",
            "scope": "merchant",
            "context_id": "m_dict_01",
            "version": 1,
            "payload": {
                "merchant_id": "m_dict_01",
                "category_slug": "dentists",
                "identity": {"name": "Smile Lab"},
                "performance": {"ctr": 0.02},
            },
        },
        {
            "event": "context",
            "scope": "trigger",
            "context_id": "trg_dict",
            "version": 1,
            "payload": {
                "id": "trg_dict",
                "kind": "performance_drop",
                "merchant_id": "m_dict_01",
                "urgency": 3,
                "suppression_key": "sup_dict:2026-W17",
            },
        },
        {
            "event": "tick",
            "available_triggers": ["trg_dict"],
            "now": "2026-04-26T12:00:00Z",
        },
        {
            "event": "reply",
            "conversation_id": "$latest",
            "merchant_id": "m_dict_01",
            "message": "tell me more details",
            "turn_number": 2,
        },
    ]

    canonical_trace = harness.assert_replays_identical(
        raw_events,
        repetitions=2,
        trace_name="dict_events_lifecycle",
    )

    assert canonical_trace.summary["total_steps"] == 5
    assert canonical_trace.steps[3].event_type == EventType.TICK.value
    assert canonical_trace.steps[4].event_type == EventType.REPLY.value


def test_replay_trace_divergence_detection(harness):
    """
    Test that assert_matches accurately detects non-matching runs and raises informative AssertionError.
    """
    events_a = [
        ContextEvent.category("dentists", 1, {"slug": "dentists", "display_name": "Dentists"}),
        ContextEvent.merchant("m_div_01", 1, {"merchant_id": "m_div_01", "category_slug": "dentists", "identity": {"name": "Dr. A"}}),
        ContextEvent.trigger("trg_div", 1, {"id": "trg_div", "kind": "festival", "merchant_id": "m_div_01", "urgency": 2}),
        TickEvent(available_triggers=["trg_div"], now="2026-04-26T12:00:00Z"),
    ]

    events_b = [
        ContextEvent.category("dentists", 1, {"slug": "dentists", "display_name": "Dentists"}),
        ContextEvent.merchant("m_div_01", 1, {"merchant_id": "m_div_01", "category_slug": "dentists", "identity": {"name": "Dr. B"}}),
        ContextEvent.trigger("trg_div", 1, {"id": "trg_div", "kind": "festival", "merchant_id": "m_div_01", "urgency": 2}),
        TickEvent(available_triggers=["trg_div"], now="2026-04-26T12:00:00Z"),
    ]

    trace_a = harness.run(events_a, trace_name="trace_a", auto_reset=True)
    trace_b = harness.run(events_b, trace_name="trace_b", auto_reset=True)

    with pytest.raises(AssertionError) as exc_info:
        trace_a.assert_matches(trace_b)

    assert "Replay divergence detected" in str(exc_info.value)


def test_replay_trace_serialization(harness):
    """
    Test JSON serialization and deserialization of a complete lifecycle replay trace.
    """
    events = [
        ContextEvent.category("salons", 1, {"slug": "salons", "display_name": "Salons"}),
        ContextEvent.merchant("m_ser_01", 1, {"merchant_id": "m_ser_01", "category_slug": "salons", "identity": {"name": "Salon X"}}),
        ContextEvent.trigger("trg_ser", 1, {"id": "trg_ser", "kind": "festival", "merchant_id": "m_ser_01", "urgency": 2}),
        TickEvent(available_triggers=["trg_ser"], now="2026-04-26T10:00:00Z"),
    ]

    trace = harness.run(events, trace_name="ser_test", auto_reset=True)
    json_str = trace.to_json(indent=2)
    assert isinstance(json_str, str)
    loaded = json.loads(json_str)
    assert loaded["trace_name"] == "ser_test"
    assert len(loaded["steps"]) == 4
    assert loaded["summary"]["tick_events"] == 1

