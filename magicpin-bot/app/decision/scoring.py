"""Trigger expiration, scoring, and signal selection logic."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.decision.resolution import resolve_context
from app.decision.suppression import check_suppressed
from app.decision.taxonomy import get_trigger_policy
from app.models import ResolvedContext


def is_trigger_expired(trigger: Dict[str, Any], now_iso: Optional[str] = None) -> bool:
    """Returns True if the trigger has passed its expires_at timestamp.

    Per testing-brief.md §2.2:
    The expiry check must ALWAYS compare against the 'now' field passed in the
    /v1/tick request body, never against the server's real wall-clock time.
    If now_iso is omitted or empty, triggers are not expired by wall-clock time.
    """
    if not isinstance(trigger, dict):
        return False
    expires_at = trigger.get("expires_at")
    if not expires_at or not isinstance(expires_at, str):
        return False
    if not now_iso or not isinstance(now_iso, str) or not now_iso.strip():
        return False
    try:
        now_dt = datetime.fromisoformat(now_iso.strip().replace("Z", "+00:00"))
        exp_dt = datetime.fromisoformat(expires_at.strip().replace("Z", "+00:00"))
        if now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=timezone.utc)
        if exp_dt.tzinfo is None:
            exp_dt = exp_dt.replace(tzinfo=timezone.utc)
        return now_dt > exp_dt
    except Exception:
        return False


def score_trigger(
    trigger: Dict[str, Any],
    merchant: Optional[Dict[str, Any]] = None,
    category: Optional[Dict[str, Any]] = None,
    customer: Optional[Dict[str, Any]] = None,
    now: Optional[Any] = None,
) -> int:
    """
    Computes deterministic score for a trigger:
    score = urgency_score + merchant_relevance + category_relevance + customer_relevance - suppression_penalty

    Data-driven approach reading urgency from TriggerContext.urgency (1-5)
    combined with contextual relevance.
    """
    raw_urgency = trigger.get("urgency") if isinstance(trigger, dict) else None
    if raw_urgency is not None:
        try:
            urgency = int(raw_urgency)
        except (ValueError, TypeError):
            urgency = 1
    else:
        policy = get_trigger_policy(trigger.get("kind", trigger.get("type")) if isinstance(trigger, dict) else None)
        urgency = policy.base_urgency if policy else 1
    urgency = max(1, min(5, urgency))
    urgency_score = urgency * 20

    # Merchant relevance
    merchant_relevance = 10 if merchant else 0
    if merchant and merchant.get("subscription", {}).get("status") == "active":
        merchant_relevance += 5

    # Category relevance
    category_relevance = 10 if category else 0
    if merchant and category and merchant.get("category_slug") == category.get("slug"):
        category_relevance += 5

    # Customer relevance
    if trigger.get("customer_id") or trigger.get("scope") == "customer":
        if customer:
            customer_relevance = 10
            if customer.get("preferences", {}).get("reminder_opt_in") is True:
                customer_relevance += 5
        else:
            customer_relevance = -10
    else:
        customer_relevance = 5  # Merchant-level trigger

    # Suppression penalty
    suppression_key = trigger.get("suppression_key")
    suppression_penalty = 1000 if check_suppressed(suppression_key, now=now) else 0

    return urgency_score + merchant_relevance + category_relevance + customer_relevance - suppression_penalty


def select_strongest_signal(
    triggers: List[Dict[str, Any]],
    now: Optional[str] = None,
    context_store: Optional[Any] = None,
) -> Optional[Dict[str, Any]]:
    """
    Selects the single highest-priority, qualified trigger to act on, or None.
    Handles:
    - zero triggers -> None
    - expired triggers -> filtered out
    - already-suppressed triggers -> filtered out
    - missing context -> filtered out if context_store is provided
    - ranks remaining triggers by score_trigger
    """
    if not triggers:
        return None

    qualified = []
    for trigger in triggers:
        if not isinstance(trigger, dict):
            continue

        # Check expiry
        if is_trigger_expired(trigger, now):
            continue

        # Check suppression
        suppression_key = trigger.get("suppression_key")
        if check_suppressed(suppression_key, now=now):
            continue

        rc: Optional[ResolvedContext] = None
        if context_store is not None:
            rc = resolve_context(context_store, trigger)
            if rc is None or not rc.merchant:
                # Essential merchant context missing -> skip
                continue
            merchant = rc.merchant
            category = rc.category
            customer = rc.customer
        else:
            merchant = None
            category = None
            customer = None

        score = score_trigger(trigger, merchant, category, customer, now=now)
        if score > 0:
            qualified.append((score, trigger, rc))

    if not qualified:
        return None

    # Sort descending by score, tie-breaking by trigger ID for stability
    qualified.sort(key=lambda item: (item[0], str(item[1].get("id", ""))), reverse=True)
    return qualified[0][1]


__all__ = [
    "is_trigger_expired",
    "score_trigger",
    "select_strongest_signal",
]
