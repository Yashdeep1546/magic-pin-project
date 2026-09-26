"""In-memory state management for Magicpin Vera bot."""

import threading
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional, Tuple


class VersionGateResult(str, Enum):
    CREATED = "created"
    REPLACED = "replaced"
    IDEMPOTENT = "idempotent"
    STALE = "stale"


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
                self._data[key] = {
                    "version": version,
                    "payload": payload,
                    "updated_at": now_iso,
                }
                return VersionGateResult.CREATED, None

            current_version = existing["version"]
            if version > current_version:
                self._data[key] = {
                    "version": version,
                    "payload": payload,
                    "updated_at": now_iso,
                }
                return VersionGateResult.REPLACED, current_version
            elif version == current_version:
                # Idempotent no-op
                return VersionGateResult.IDEMPOTENT, current_version
            else:
                # Stale version
                return VersionGateResult.STALE, current_version

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
        """Record a turn in the conversation."""
        with self._lock:
            if conversation_id not in self._conversations:
                self._conversations[conversation_id] = {
                    "merchant_id": turn.get("merchant_id"),
                    "customer_id": turn.get("customer_id"),
                    "trigger_id": turn.get("trigger_id"),
                    "turns": [turn],
                    "last_action": None,
                    "state": "active",
                    "sent_messages": [],
                }
            else:
                self._conversations[conversation_id]["turns"].append(turn)

    def add_sent_message(self, conversation_id: str, message: Dict[str, Any]) -> None:
        """Record a sent message in the conversation."""
        with self._lock:
            if conversation_id in self._conversations:
                self._conversations[conversation_id]["sent_messages"].append(message)

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
