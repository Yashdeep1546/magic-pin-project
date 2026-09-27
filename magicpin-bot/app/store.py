"""In-memory state management for Magicpin Vera bot."""

import threading
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional, Tuple


class VersionGateResult(str, Enum):
    CREATED = "created"
    REPLACED = "replaced"
    IDEMPOTENT = "idempotent"
    STALE = "stale"


def format_gate_response(
    gate_result: VersionGateResult,
    current_version: Optional[int] = None,
    ack_id: Optional[str] = None,
    stored_at: Optional[str] = None,
) -> Tuple[int, Dict[str, Any]]:
    """
    Maps VersionGateResult to exact contract response per testing-brief.md §2.1:
    - 200 (accepted, new or replaced or idempotent same version):
      {"accepted": true, "ack_id": "ack_abc123", "stored_at": "2026-04-26T10:00:00.123Z"}
    - 409 (stale version conflict):
      {"accepted": false, "reason": "stale_version", "current_version": 5}
    """
    if gate_result == VersionGateResult.STALE:
        return 409, {
            "accepted": False,
            "reason": "stale_version",
            "current_version": current_version if current_version is not None else 0,
        }

    return 200, {
        "accepted": True,
        "ack_id": ack_id or f"ack_{uuid.uuid4().hex[:8]}",
        "stored_at": stored_at or datetime.now(timezone.utc).isoformat(),
    }


class ContextStore:
    """
    Thread-safe in-memory store for context entities keyed by (scope, context_id).
    Maintains {version, payload, updated_at} with strict version-gating.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: Dict[Tuple[str, str], Dict[str, Any]] = {}

    def set(
        self,
        scope: str,
        context_id: str,
        version: int,
        payload: Dict[str, Any],
    ) -> Tuple[VersionGateResult, Optional[int]]:
        """
        Store context according to version gating rules:
        - no existing record          -> store it, return CREATED
        - incoming version > current  -> replace, return REPLACED
        - incoming version == current -> no-op, return IDEMPOTENT
        - incoming version < current  -> reject, return STALE with current_version
        """
        key = (scope, context_id)
        with self._lock:
            existing = self._data.get(key)
            now_iso = datetime.now(timezone.utc).isoformat()

            if existing is None:
                ack_id = f"ack_{uuid.uuid4().hex[:8]}"
                self._data[key] = {
                    "version": version,
                    "payload": payload,
                    "updated_at": now_iso,
                    "ack_id": ack_id,
                }
                return VersionGateResult.CREATED, None

            current_version = existing["version"]
            if version > current_version:
                ack_id = f"ack_{uuid.uuid4().hex[:8]}"
                self._data[key] = {
                    "version": version,
                    "payload": payload,
                    "updated_at": now_iso,
                    "ack_id": ack_id,
                }
                return VersionGateResult.REPLACED, current_version
            elif version == current_version:
                # Idempotent no-op
                return VersionGateResult.IDEMPOTENT, current_version
            else:
                # Stale version
                return VersionGateResult.STALE, current_version

    def get_meta(self, scope: str, context_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve metadata (ack_id, updated_at, version) for stored context."""
        key = (scope, context_id)
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            return {
                "version": item.get("version"),
                "ack_id": item.get("ack_id"),
                "updated_at": item.get("updated_at"),
            }

    def get(self, scope: str, context_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve stored context item by (scope, context_id)."""
        key = (scope, context_id)
        with self._lock:
            item = self._data.get(key)
            return dict(item) if item is not None else None

    def get_counts(self) -> Dict[str, int]:
        """Return counts of loaded contexts by scope (for /v1/healthz)."""
        counts = {"category": 0, "merchant": 0, "customer": 0, "trigger": 0}
        with self._lock:
            for (scope, _), _ in self._data.items():
                if scope in counts:
                    counts[scope] += 1
                else:
                    counts[scope] = counts.get(scope, 0) + 1
        return counts

    def clear(self) -> None:
        """Clear all stored contexts (for /v1/teardown)."""
        with self._lock:
            self._data.clear()

    def count(self) -> int:
        """Return total number of stored context items."""
        with self._lock:
            return len(self._data)


class ConversationStore:
    """
    Thread-safe in-memory store for active conversations keyed by conversation_id.
    Stores: {merchant_id, customer_id, trigger_id, turns: [], last_action, state, sent_messages: []}
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._conversations: Dict[str, Dict[str, Any]] = {}

    def get(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve conversation data copy by conversation_id."""
        with self._lock:
            conv = self._conversations.get(conversation_id)
            if conv is None:
                return None
            return {
                "merchant_id": conv.get("merchant_id"),
                "customer_id": conv.get("customer_id"),
                "trigger_id": conv.get("trigger_id"),
                "turns": list(conv.get("turns", [])),
                "last_action": conv.get("last_action"),
                "state": conv.get("state", "active"),
                "sent_messages": list(conv.get("sent_messages", [])),
            }

    def create_or_update(
        self,
        conversation_id: str,
        merchant_id: Optional[str] = None,
        customer_id: Optional[str] = None,
        trigger_id: Optional[str] = None,
        last_action: Optional[str] = None,
        state: str = "active",
    ) -> Dict[str, Any]:
        """Create or update a conversation record."""
        with self._lock:
            if conversation_id not in self._conversations:
                self._conversations[conversation_id] = {
                    "merchant_id": merchant_id,
                    "customer_id": customer_id,
                    "trigger_id": trigger_id,
                    "turns": [],
                    "last_action": last_action,
                    "state": state,
                    "sent_messages": [],
                }
            else:
                conv = self._conversations[conversation_id]
                if merchant_id is not None:
                    conv["merchant_id"] = merchant_id
                if customer_id is not None:
                    conv["customer_id"] = customer_id
                if trigger_id is not None:
                    conv["trigger_id"] = trigger_id
                if last_action is not None:
                    conv["last_action"] = last_action
                if state is not None:
                    conv["state"] = state
            return dict(self._conversations[conversation_id])

    def add_turn(self, conversation_id: str, turn: Dict[str, Any]) -> None:
        """
        Record a turn in the conversation.
        Idempotent:
        - If an exact duplicate turn already exists (same turn_number and same message),
          it is not appended.
        - If the same turn_number exists with a changed message, it updates that turn in place.
        - If turn_number is new or None, it is appended.
        """
        with self._lock:
            turn_num = turn.get("turn_number")
            msg = (turn.get("message") or "").strip()

            if conversation_id not in self._conversations:
                self._conversations[conversation_id] = {
                    "merchant_id": turn.get("merchant_id"),
                    "customer_id": turn.get("customer_id"),
                    "trigger_id": turn.get("trigger_id"),
                    "turns": [dict(turn)],
                    "last_action": None,
                    "state": "active",
                    "sent_messages": [],
                }
                return

            turns = self._conversations[conversation_id]["turns"]

            # 1. Exact duplicate check
            for existing_turn in turns:
                ex_num = existing_turn.get("turn_number")
                ex_msg = (existing_turn.get("message") or "").strip()
                norm_ex_num = 1 if ex_num is None else ex_num
                norm_new_num = 1 if turn_num is None else turn_num
                if norm_ex_num == norm_new_num and ex_msg == msg:
                    return

            # 2. Same turn number with changed message: update in place
            if turn_num is not None:
                for i, existing_turn in enumerate(turns):
                    if existing_turn.get("turn_number") == turn_num:
                        turns[i] = dict(turn)
                        return

            # 3. New turn
            turns.append(dict(turn))

    def add_sent_message(self, conversation_id: str, message: Dict[str, Any]) -> None:
        """Record a sent message in the conversation, avoiding duplicate appends of the exact same message payload."""
        with self._lock:
            if conversation_id in self._conversations:
                sent = self._conversations[conversation_id]["sent_messages"]
                # Deduplicate: if the immediately preceding message has identical body and template/action, do not duplicate
                if sent:
                    last_msg = sent[-1]
                    if (
                        last_msg.get("body") == message.get("body")
                        and last_msg.get("template_name") == message.get("template_name")
                        and last_msg.get("action") == message.get("action")
                    ):
                        return
                sent.append(dict(message))

    def set_last_action(self, conversation_id: str, action: str) -> None:
        """Update last action taken in conversation."""
        with self._lock:
            if conversation_id in self._conversations:
                self._conversations[conversation_id]["last_action"] = action

    def set_state(self, conversation_id: str, state: str) -> None:
        """Update conversation state."""
        with self._lock:
            if conversation_id in self._conversations:
                self._conversations[conversation_id]["state"] = state

    def clear(self) -> None:
        """Clear all conversation records (for /v1/teardown)."""
        with self._lock:
            self._conversations.clear()

    def count(self) -> int:
        """Return count of active conversations."""
        with self._lock:
            return len(self._conversations)


# Global in-memory singleton stores
context_store = ContextStore()
conversation_store = ConversationStore()
