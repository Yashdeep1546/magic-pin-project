"""Deterministic replay harness for the complete Magicpin Vera bot lifecycle.

Executes ordered sequences of lifecycle events (context pushes, ticks, counterpart replies)
against the FastAPI bot application and captures normalized, deterministic execution traces.

Key capabilities:
1. Normalizes nondeterministic runtime values:
   - Masks volatile UUIDs (ack_id, conversation_id, request_id) into stable tokens.
   - Masks dynamic clock timestamps (stored_at, sent_at, updated_at).
   - Maps runtime conversation UUIDs to deterministic aliases (conv_norm_0, conv_norm_1, etc.)
     so subsequent reply events can seamlessly reference `$latest`, `$merchant:<id>`, or aliases.
2. Extracts canonical logical decisions:
   - Context gate decisions (accepted, reason, stale_version).
   - Selected trigger IDs, candidate rankings, and template policies.
   - Active suppression keys and frequency windows.
   - Conversation state transitions and intent classifications.
   - Fallback rendering behaviors.
3. Provides strict equality assertion (`assert_matches` / `assert_replays_identical`):
   - Compares traces across repeated runs with detailed unified diffs on any divergence.
"""

import copy
import difflib
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Sequence, Union

from fastapi.testclient import TestClient

from app.composer import set_custom_llm_caller
from app.conversation import conversation_state_machine
from app.decision_engine import suppression_engine
from app.main import app as default_fastapi_app
from app.store import context_store, conversation_store

logger = logging.getLogger("magicpin-bot.replay_harness")


# ---------------------------------------------------------------------------
# Lifecycle Event Models
# ---------------------------------------------------------------------------

class EventType(str, Enum):
    CONTEXT = "context"
    TICK = "tick"
    REPLY = "reply"


@dataclass
class ContextEvent:
    """Represents a POST /v1/context push event."""
    scope: str
    context_id: str
    version: int
    payload: Dict[str, Any]
    event_type: str = EventType.CONTEXT.value

    @classmethod
    def category(cls, context_id: str, version: int, payload: Dict[str, Any]) -> "ContextEvent":
        return cls(scope="category", context_id=context_id, version=version, payload=payload)

    @classmethod
    def merchant(cls, context_id: str, version: int, payload: Dict[str, Any]) -> "ContextEvent":
        return cls(scope="merchant", context_id=context_id, version=version, payload=payload)

    @classmethod
    def customer(cls, context_id: str, version: int, payload: Dict[str, Any]) -> "ContextEvent":
        return cls(scope="customer", context_id=context_id, version=version, payload=payload)

    @classmethod
    def trigger(cls, context_id: str, version: int, payload: Dict[str, Any]) -> "ContextEvent":
        return cls(scope="trigger", context_id=context_id, version=version, payload=payload)

    def to_request_payload(self) -> Dict[str, Any]:
        return {
            "scope": self.scope,
            "context_id": self.context_id,
            "version": self.version,
            "payload": self.payload,
        }


@dataclass
class TickEvent:
    """Represents a POST /v1/tick event."""
    available_triggers: List[str]
    now: Optional[str] = None
    event_type: str = EventType.TICK.value

    def to_request_payload(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {"available_triggers": list(self.available_triggers)}
        if self.now is not None:
            data["now"] = self.now
        return data


@dataclass
class ReplyEvent:
    """Represents a POST /v1/reply event.

    `conversation_id` can be:
    - None or "$latest": targets the most recently created or targeted conversation.
    - "$merchant:<merchant_id>": targets the latest conversation for that merchant.
    - "alias:<name>" or an explicit alias: targets an earlier aliased conversation.
    - A fixed explicit conversation ID.
    """
    message: str
    conversation_id: Optional[str] = "$latest"
    merchant_id: Optional[str] = None
    customer_id: Optional[str] = None
    from_role: str = "merchant"
    turn_number: Optional[int] = None
    received_at: Optional[str] = None
    alias: Optional[str] = None
    event_type: str = EventType.REPLY.value

    def to_request_payload(self, resolved_conv_id: str) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "conversation_id": resolved_conv_id,
            "message": self.message,
            "from_role": self.from_role,
        }
        if self.merchant_id:
            payload["merchant_id"] = self.merchant_id
        if self.customer_id:
            payload["customer_id"] = self.customer_id
        if self.turn_number is not None:
            payload["turn_number"] = self.turn_number
        if self.received_at:
            payload["received_at"] = self.received_at
        return payload


ReplayEventUnion = Union[ContextEvent, TickEvent, ReplyEvent, Dict[str, Any]]


def parse_event(raw: ReplayEventUnion) -> Union[ContextEvent, TickEvent, ReplyEvent]:
    """Parses a raw dict or event object into a canonical lifecycle event."""
    if isinstance(raw, (ContextEvent, TickEvent, ReplyEvent)):
        return raw
    if not isinstance(raw, dict):
        raise ValueError(f"Unsupported event type: {type(raw).__name__}")

    ev_type = raw.get("event") or raw.get("event_type") or raw.get("type")
    if not ev_type:
        # Infer by keys
        if "scope" in raw and "context_id" in raw:
            ev_type = EventType.CONTEXT.value
        elif "available_triggers" in raw:
            ev_type = EventType.TICK.value
        elif "message" in raw:
            ev_type = EventType.REPLY.value
        else:
            raise ValueError(f"Could not infer event type from keys: {list(raw.keys())}")

    ev_type = str(ev_type).lower()
    if ev_type == EventType.CONTEXT.value:
        return ContextEvent(
            scope=raw["scope"],
            context_id=raw["context_id"],
            version=raw["version"],
            payload=raw.get("payload", {}),
        )
    elif ev_type == EventType.TICK.value:
        return TickEvent(
            available_triggers=raw.get("available_triggers", []),
            now=raw.get("now"),
        )
    elif ev_type == EventType.REPLY.value:
        return ReplyEvent(
            message=raw.get("message", ""),
            conversation_id=raw.get("conversation_id", "$latest"),
            merchant_id=raw.get("merchant_id"),
            customer_id=raw.get("customer_id"),
            from_role=raw.get("from_role", "merchant"),
            turn_number=raw.get("turn_number"),
            received_at=raw.get("received_at"),
            alias=raw.get("alias"),
        )
    else:
        raise ValueError(f"Unknown event type '{ev_type}'")


# ---------------------------------------------------------------------------
# Normalized Trace & Step Representation
# ---------------------------------------------------------------------------

@dataclass
class NormalizedStep:
    """A normalized step recording deterministic inputs, outputs, and logical decisions."""
    step_index: int
    event_type: str
    event_input: Dict[str, Any]
    status_code: int
    output: Dict[str, Any]
    logical_decision: Dict[str, Any]
    system_state: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_index": self.step_index,
            "event_type": self.event_type,
            "event_input": self.event_input,
            "status_code": self.status_code,
            "output": self.output,
            "logical_decision": self.logical_decision,
            "system_state": self.system_state,
        }


@dataclass
class ReplayTrace:
    """Complete lifecycle replay trace capturing all normalized steps."""
    trace_name: str
    steps: List[NormalizedStep] = field(default_factory=list)
    summary: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trace_name": self.trace_name,
            "summary": self.summary,
            "steps": [s.to_dict() for s in self.steps],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)

    @property
    def logical_decisions(self) -> List[Dict[str, Any]]:
        return [
            {"step": s.step_index, "type": s.event_type, **s.logical_decision}
            for s in self.steps
        ]

    def assert_matches(self, other: "ReplayTrace", strict_bodies: bool = True) -> None:
        """Asserts that two replay traces are identical, ignoring nondeterministic values."""
        if len(self.steps) != len(other.steps):
            raise AssertionError(
                f"Trace step count mismatch: {len(self.steps)} != {len(other.steps)}"
            )

        for i, (s1, s2) in enumerate(zip(self.steps, other.steps)):
            d1 = s1.to_dict()
            d2 = s2.to_dict()

            if not strict_bodies:
                # Optionally relax message bodies if LLM nondeterminism testing
                d1 = copy.deepcopy(d1)
                d2 = copy.deepcopy(d2)
                if s1.event_type == EventType.TICK.value:
                    for act in d1.get("output", {}).get("actions", []):
                        act.pop("body", None)
                    for act in d2.get("output", {}).get("actions", []):
                        act.pop("body", None)
                elif s1.event_type == EventType.REPLY.value:
                    d1.get("output", {}).pop("body", None)
                    d2.get("output", {}).pop("body", None)

            if d1 != d2:
                j1 = json.dumps(d1, indent=2, sort_keys=True)
                j2 = json.dumps(d2, indent=2, sort_keys=True)
                diff = "\n".join(
                    difflib.unified_diff(
                        j1.splitlines(),
                        j2.splitlines(),
                        fromfile=f"trace_expected_step_{i}",
                        tofile=f"trace_actual_step_{i}",
                    )
                )
                raise AssertionError(
                    f"Replay divergence detected at step {i} ({s1.event_type}):\n{diff}"
                )

    def assert_logical_decisions_match(self, other: "ReplayTrace") -> None:
        """Asserts that all core logical decisions match exactly between runs."""
        dec1 = self.logical_decisions
        dec2 = other.logical_decisions
        if dec1 != dec2:
            j1 = json.dumps(dec1, indent=2, sort_keys=True)
            j2 = json.dumps(dec2, indent=2, sort_keys=True)
            diff = "\n".join(
                difflib.unified_diff(
                    j1.splitlines(),
                    j2.splitlines(),
                    fromfile="expected_decisions",
                    tofile="actual_decisions",
                )
            )
            raise AssertionError(f"Logical decision mismatch:\n{diff}")


# ---------------------------------------------------------------------------
# Replay Harness Engine
# ---------------------------------------------------------------------------

class ReplayHarness:
    """Harness that executes lifecycle event sequences and returns normalized traces."""

    def __init__(
        self,
        client: Optional[TestClient] = None,
        app: Optional[Any] = None,
    ) -> None:
        self.fastapi_app = app or default_fastapi_app
        self.client = client or TestClient(self.fastapi_app)
        self._conv_id_to_norm: Dict[str, str] = {}
        self._norm_to_conv_id: Dict[str, str] = {}
        self._alias_to_conv_id: Dict[str, str] = {}
        self._latest_conv_id: Optional[str] = None
        self._latest_conv_by_merchant: Dict[str, str] = {}
        self._next_conv_idx: int = 0

    def reset(self) -> None:
        """Completely clears all in-memory stores and harness alias state."""
        context_store.clear()
        conversation_store.clear()
        suppression_engine.clear()
        conversation_state_machine.reset()
        set_custom_llm_caller(None)

        self._conv_id_to_norm.clear()
        self._norm_to_conv_id.clear()
        self._alias_to_conv_id.clear()
        self._latest_conv_id = None
        self._latest_conv_by_merchant.clear()
        self._next_conv_idx = 0

    def _normalize_conv_id(self, raw_id: Optional[str]) -> Optional[str]:
        """Maps runtime dynamic conversation ID to deterministic index token."""
        if not raw_id:
            return None
        if raw_id not in self._conv_id_to_norm:
            norm_id = f"conv_norm_{self._next_conv_idx}"
            self._next_conv_idx += 1
            self._conv_id_to_norm[raw_id] = norm_id
            self._norm_to_conv_id[norm_id] = raw_id
        return self._conv_id_to_norm[raw_id]

    def _resolve_conv_id(
        self,
        conv_selector: Optional[str],
        merchant_id: Optional[str] = None,
    ) -> str:
        """Resolves symbolic conversation selectors ($latest, $merchant:<id>, aliases)."""
        sel = conv_selector.strip() if conv_selector else "$latest"

        # 1. $latest
        if sel == "$latest":
            if self._latest_conv_id:
                return self._latest_conv_id
            # Fallback to single active conversation if available
            with conversation_store._lock:
                if len(conversation_store._conversations) == 1:
                    return next(iter(conversation_store._conversations.keys()))
            raise ValueError("Replay selector '$latest' could not be resolved (no active conversations).")

        # 2. $merchant:<id>
        if sel.startswith("$merchant:"):
            mid = sel.split(":", 1)[1].strip()
            if mid in self._latest_conv_by_merchant:
                return self._latest_conv_by_merchant[mid]
            with conversation_store._lock:
                for cid, cdata in reversed(list(conversation_store._conversations.items())):
                    if cdata.get("merchant_id") == mid:
                        return cid
            raise ValueError(f"Replay selector '{sel}' could not find conversation for merchant '{mid}'.")

        # 3. Alias / Normalized ID
        if sel in self._alias_to_conv_id:
            return self._alias_to_conv_id[sel]
        if sel in self._norm_to_conv_id:
            return self._norm_to_conv_id[sel]

        # 4. If merchant_id specified and selector is default, try merchant latest
        if merchant_id and merchant_id in self._latest_conv_by_merchant:
            return self._latest_conv_by_merchant[merchant_id]

        # 5. Fixed explicit string
        return sel

    def _capture_system_state(self) -> Dict[str, Any]:
        """Captures a normalized snapshot of internal system state."""
        # 1. Suppression summary
        with suppression_engine._lock:
            active_suppressions = []
            for k in sorted(suppression_engine._records.keys()):
                rec = suppression_engine._records[k]
                active_suppressions.append({
                    "key": k,
                    "window_seconds": rec.get("window_seconds"),
                    "has_explicit_time": rec.get("has_explicit_time"),
                    "has_expiry": rec.get("expires_at") is not None,
                })

        # 2. Conversations summary (sorted deterministically by merchant, trigger, and index)
        with conversation_store._lock:
            conv_items = []
            for cid, cdata in conversation_store._conversations.items():
                norm_id = self._normalize_conv_id(cid)
                norm_idx = int(norm_id.split('_')[-1]) if norm_id and '_' in norm_id else 0
                conv_items.append((
                    cdata.get("merchant_id") or "",
                    cdata.get("trigger_id") or "",
                    norm_idx,
                    {
                        "normalized_id": norm_id,
                        "merchant_id": cdata.get("merchant_id"),
                        "trigger_id": cdata.get("trigger_id"),
                        "state": cdata.get("state"),
                        "last_action": cdata.get("last_action"),
                        "turn_count": len(cdata.get("turns", [])),
                        "sent_messages_count": len(cdata.get("sent_messages", [])),
                    }
                ))
            conv_items.sort(key=lambda x: (x[0], x[1], x[2]))
            convs = [item[3] for item in conv_items]

        # 3. Context counts
        counts = context_store.get_counts()

        return {
            "active_suppressions": active_suppressions,
            "conversations": convs,
            "context_counts": {
                "category": counts.get("category", 0),
                "merchant": counts.get("merchant", 0),
                "customer": counts.get("customer", 0),
                "trigger": counts.get("trigger", 0),
            },
        }

    def run(
        self,
        events: Sequence[ReplayEventUnion],
        trace_name: str = "lifecycle_replay",
        auto_reset: bool = True,
    ) -> ReplayTrace:
        """Executes an ordered sequence of lifecycle events and returns a normalized trace."""
        if auto_reset:
            self.reset()

        parsed_events = [parse_event(ev) for ev in events]
        steps: List[NormalizedStep] = []

        context_count = 0
        tick_count = 0
        reply_count = 0
        action_count = 0

        for idx, ev in enumerate(parsed_events):
            if isinstance(ev, ContextEvent):
                context_count += 1
                req_payload = ev.to_request_payload()
                resp = self.client.post("/v1/context", json=req_payload)
                status_code = resp.status_code
                resp_json = resp.json() if resp.status_code != 500 else {}

                # Mask non-deterministic fields (ack_id, stored_at)
                norm_output = {
                    "accepted": resp_json.get("accepted"),
                    "reason": resp_json.get("reason"),
                    "current_version": resp_json.get("current_version"),
                }
                if "details" in resp_json:
                    norm_output["details"] = resp_json["details"]

                logical_decision = {
                    "action": "context_push",
                    "scope": ev.scope,
                    "context_id": ev.context_id,
                    "version": ev.version,
                    "accepted": resp_json.get("accepted"),
                    "reason": resp_json.get("reason"),
                    "status_code": status_code,
                }

                step = NormalizedStep(
                    step_index=idx,
                    event_type=EventType.CONTEXT.value,
                    event_input={
                        "scope": ev.scope,
                        "context_id": ev.context_id,
                        "version": ev.version,
                    },
                    status_code=status_code,
                    output=norm_output,
                    logical_decision=logical_decision,
                    system_state=self._capture_system_state(),
                )
                steps.append(step)

            elif isinstance(ev, TickEvent):
                tick_count += 1
                req_payload = ev.to_request_payload()
                resp = self.client.post("/v1/tick", json=req_payload)
                status_code = resp.status_code
                resp_json = resp.json() if resp.status_code == 200 else {}
                raw_actions = resp_json.get("actions", [])
                action_count += len(raw_actions)

                norm_actions = []
                selected_triggers = []

                for act in raw_actions:
                    raw_conv_id = act.get("conversation_id")
                    norm_conv_id = self._normalize_conv_id(raw_conv_id)
                    mid = act.get("merchant_id")

                    if raw_conv_id:
                        self._latest_conv_id = raw_conv_id
                        if mid:
                            self._latest_conv_by_merchant[mid] = raw_conv_id

                    norm_act = {
                        "normalized_conversation_id": norm_conv_id,
                        "merchant_id": mid,
                        "customer_id": act.get("customer_id"),
                        "trigger_id": act.get("trigger_id"),
                        "template_name": act.get("template_name"),
                        "template_params": act.get("template_params", []),
                        "body": act.get("body"),
                        "cta": act.get("cta"),
                        "suppression_key": act.get("suppression_key"),
                        "rationale": act.get("rationale"),
                    }
                    norm_actions.append(norm_act)
                    selected_triggers.append({
                        "merchant_id": mid,
                        "trigger_id": act.get("trigger_id"),
                        "template_name": act.get("template_name"),
                        "suppression_key": act.get("suppression_key"),
                    })

                logical_decision = {
                    "action": "tick_evaluation",
                    "action_count": len(norm_actions),
                    "selected_triggers": selected_triggers,
                }

                step = NormalizedStep(
                    step_index=idx,
                    event_type=EventType.TICK.value,
                    event_input={
                        "available_triggers": ev.available_triggers,
                        "now": ev.now,
                    },
                    status_code=status_code,
                    output={"actions": norm_actions},
                    logical_decision=logical_decision,
                    system_state=self._capture_system_state(),
                )
                steps.append(step)

            elif isinstance(ev, ReplyEvent):
                reply_count += 1
                resolved_cid = self._resolve_conv_id(
                    ev.conversation_id, merchant_id=ev.merchant_id
                )
                self._latest_conv_id = resolved_cid
                if ev.merchant_id:
                    self._latest_conv_by_merchant[ev.merchant_id] = resolved_cid
                if ev.alias:
                    self._alias_to_conv_id[ev.alias] = resolved_cid

                req_payload = ev.to_request_payload(resolved_cid)
                resp = self.client.post("/v1/reply", json=req_payload)
                status_code = resp.status_code
                resp_json = resp.json() if resp.status_code == 200 else {}

                # State after reply
                conv_data = conversation_store.get(resolved_cid)
                post_state = conv_data.get("state") if conv_data else None
                norm_cid = self._normalize_conv_id(resolved_cid)

                norm_output = {
                    "action": resp_json.get("action"),
                    "wait_seconds": resp_json.get("wait_seconds"),
                    "rationale": resp_json.get("rationale"),
                    "body": resp_json.get("body"),
                    "cta": resp_json.get("cta"),
                    "post_state": post_state,
                }

                logical_decision = {
                    "action": "reply_turn",
                    "reply_action": resp_json.get("action"),
                    "wait_seconds": resp_json.get("wait_seconds"),
                    "post_state": post_state,
                    "normalized_conversation_id": norm_cid,
                }

                step = NormalizedStep(
                    step_index=idx,
                    event_type=EventType.REPLY.value,
                    event_input={
                        "message": ev.message,
                        "normalized_conversation_id": norm_cid,
                        "from_role": ev.from_role,
                        "turn_number": ev.turn_number,
                    },
                    status_code=status_code,
                    output=norm_output,
                    logical_decision=logical_decision,
                    system_state=self._capture_system_state(),
                )
                steps.append(step)

        summary = {
            "total_steps": len(steps),
            "context_events": context_count,
            "tick_events": tick_count,
            "reply_events": reply_count,
            "actions_generated": action_count,
        }

        return ReplayTrace(
            trace_name=trace_name,
            steps=steps,
            summary=summary,
        )

    def assert_replays_identical(
        self,
        events: Sequence[ReplayEventUnion],
        repetitions: int = 3,
        strict_bodies: bool = True,
        trace_name: str = "replayed_lifecycle",
    ) -> ReplayTrace:
        """
        Runs the exact same lifecycle event sequence `repetitions` times.
        Asserts that every execution produces byte-for-byte identical traces.
        Returns the verified canonical trace.
        """
        if repetitions < 2:
            raise ValueError("Repetitions must be at least 2 to verify determinism.")

        canonical_trace: Optional[ReplayTrace] = None

        for run_idx in range(repetitions):
            run_name = f"{trace_name}_run_{run_idx + 1}"
            trace = self.run(events, trace_name=run_name, auto_reset=True)
            if canonical_trace is None:
                canonical_trace = trace
            else:
                canonical_trace.assert_matches(trace, strict_bodies=strict_bodies)
                canonical_trace.assert_logical_decisions_match(trace)

        assert canonical_trace is not None
        return canonical_trace


__all__ = [
    "EventType",
    "ContextEvent",
    "TickEvent",
    "ReplyEvent",
    "ReplayEventUnion",
    "parse_event",
    "NormalizedStep",
    "ReplayTrace",
    "ReplayHarness",
]
