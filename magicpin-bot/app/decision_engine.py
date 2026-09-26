"""Deterministic decision engine for Magicpin Vera bot.

Implements trigger prioritization, scoring, suppression tracking,
signal selection, and template-based message rendering.
"""

import re
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.composer import build_compact_context, compose_message
from app.models import TickAction

# ---------------------------------------------------------------------------
# Trigger Priority Table
# ---------------------------------------------------------------------------
TRIGGER_PRIORITY: Dict[str, int] = {
    # Exact names from specification
    "compliance_alert": 100,
    "recall_due": 95,
    "performance_drop": 90,
    "customer_winback": 80,
    "research_digest": 70,
    "festival": 60,
    "curious_ask": 50,
    "seasonal": 40,

    # Dataset kind / aliases
    "regulation_change": 100,
    "compliance": 100,
    "perf_dip": 90,
    "performance_dip": 90,
    "winback_eligible": 80,
    "winback": 80,
    "research": 70,
    "festival_upcoming": 60,
    "curious_ask_due": 50,
    "seasonal_perf_dip": 40,
    "seasonal_acquisition_dip": 40,
}


# ---------------------------------------------------------------------------
# Suppression Engine
# ---------------------------------------------------------------------------
class SuppressionEngine:
    """Thread-safe storage for active suppression keys."""

    def __init__(self) -> None:
        self._suppressed_keys = set()
        self._lock = threading.Lock()

    def check_suppressed(self, key: Optional[str]) -> bool:
        """Returns True if the key is already suppressed."""
        if not key:
            return False
        with self._lock:
            return key in self._suppressed_keys

    def mark_suppressed(self, key: Optional[str]) -> None:
        """Adds key to suppression set."""
        if not key:
            return
        with self._lock:
            self._suppressed_keys.add(key)

    def clear(self) -> None:
        """Wipes all suppression keys."""
        with self._lock:
            self._suppressed_keys.clear()


suppression_engine = SuppressionEngine()


def check_suppressed(key: Optional[str]) -> bool:
    """Check if a suppression key is currently active."""
    return suppression_engine.check_suppressed(key)


def mark_suppressed(key: Optional[str]) -> None:
    """Mark a suppression key as active."""
    suppression_engine.mark_suppressed(key)


# ---------------------------------------------------------------------------
# Context Resolvers
# ---------------------------------------------------------------------------
def resolve_merchant(context_store, merchant_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """Retrieve merchant payload from ContextStore."""
    if not merchant_id or not context_store:
        return None
    item = context_store.get("merchant", merchant_id)
    if not item or "payload" not in item:
        return None
    payload = item["payload"]
    return payload if isinstance(payload, dict) else None


def resolve_category(context_store, category_slug: Optional[str]) -> Optional[Dict[str, Any]]:
    """Retrieve category payload from ContextStore."""
    if not category_slug or not context_store:
        return None
    item = context_store.get("category", category_slug)
    if not item or "payload" not in item:
        return None
    payload = item["payload"]
    return payload if isinstance(payload, dict) else None


def resolve_customer(context_store, customer_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """Retrieve customer payload from ContextStore."""
    if not customer_id or not context_store:
        return None
    item = context_store.get("customer", customer_id)
    if not item or "payload" not in item:
        return None
    payload = item["payload"]
    return payload if isinstance(payload, dict) else None


def resolve_trigger(context_store, trigger_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """Retrieve trigger payload from ContextStore."""
    if not trigger_id or not context_store:
        return None
    item = context_store.get("trigger", trigger_id)
    if not item or "payload" not in item:
        return None
    payload = item["payload"]
    if isinstance(payload, dict):
        if "id" not in payload:
            payload["id"] = trigger_id
        return payload
    return None


# ---------------------------------------------------------------------------
# Expiry Helper
# ---------------------------------------------------------------------------
def is_trigger_expired(trigger: Dict[str, Any], now_iso: Optional[str] = None) -> bool:
    """Returns True if the trigger has passed its expires_at timestamp."""
    expires_at = trigger.get("expires_at")
    if not expires_at:
        return False
    try:
        now_dt = (
            datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
            if now_iso
            else datetime.now(timezone.utc)
        )
        exp_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        if now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=timezone.utc)
        if exp_dt.tzinfo is None:
            exp_dt = exp_dt.replace(tzinfo=timezone.utc)
        return now_dt > exp_dt
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Trigger Scoring
# ---------------------------------------------------------------------------
def score_trigger(
    trigger: Dict[str, Any],
    merchant: Optional[Dict[str, Any]] = None,
    category: Optional[Dict[str, Any]] = None,
    customer: Optional[Dict[str, Any]] = None,
) -> int:
    """
    Computes deterministic score for a trigger:
    score = priority + urgency + merchant_relevance + category_relevance + customer_relevance - suppression_penalty
    """
    kind = trigger.get("kind", trigger.get("type", "unknown"))
    priority = TRIGGER_PRIORITY.get(kind, 30)

    try:
        urgency = int(trigger.get("urgency", 1))
    except (ValueError, TypeError):
        urgency = 1

    # Merchant relevance
    merchant_relevance = 10 if merchant else 0
    if merchant and merchant.get("subscription", {}).get("status") == "active":
        merchant_relevance += 5

    # Category relevance
    category_relevance = 10 if category else 0
    if merchant and category and merchant.get("category_slug") == category.get("slug"):
        category_relevance += 5

    # Customer relevance
    if trigger.get("customer_id"):
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
    suppression_penalty = 1000 if check_suppressed(suppression_key) else 0

    return priority + urgency + merchant_relevance + category_relevance + customer_relevance - suppression_penalty


# ---------------------------------------------------------------------------
# Deterministic Template Rendering
# ---------------------------------------------------------------------------
def _clean_entity_text(val: Optional[str], default: str) -> str:
    if not val or not isinstance(val, str):
        return default
    cleaned = re.sub(
        r"(?i)(?:;\s*drop\s+table.*|\[system\].*|--.*|system\s+override.*|ignore\s+previous.*)",
        "",
        val,
    ).strip()
    return cleaned or default


def render_template(
    trigger: Dict[str, Any],
    merchant: Optional[Dict[str, Any]],
    category: Optional[Dict[str, Any]],
    customer: Optional[Dict[str, Any]],
) -> Tuple[str, str, List[str], str]:
    """
    Renders deterministic message body, template name, template parameters, and rationale.
    Returns: (body, template_name, template_params, rationale)
    """
    kind = trigger.get("kind", trigger.get("type", "default"))
    raw_m_name = (
        merchant.get("identity", {}).get("name")
        or merchant.get("name")
    ) if merchant else None
    m_name = _clean_entity_text(raw_m_name, "Merchant Partner")

    raw_cat_name = (
        category.get("display_name")
        or category.get("slug")
    ) if category else None
    cat_name = _clean_entity_text(raw_cat_name, "your category")

    active_offers = [
        o.get("title") for o in merchant.get("offers", []) if o.get("status") == "active"
    ] if merchant else []
    offer_str = active_offers[0] if active_offers else "an active promotion"

    # 1. Performance drop / perf_dip
    if kind in ("performance_drop", "perf_dip", "performance_dip"):
        perf = merchant.get("performance", {}) if merchant else {}
        ctr_val = round(perf.get("ctr", 0.021) * 100, 1)
        peer_stats = category.get("peer_stats", {}) if category else {}
        peer_ctr_val = round(peer_stats.get("avg_ctr", 0.030) * 100, 1)

        body = (
            f"{m_name}, your CTR is {ctr_val}% vs {peer_ctr_val}% for {cat_name} peers. "
            f"You already have {offer_str}. Want me to draft a message around it?"
        )
        template_name = "template_performance_drop_v1"
        template_params = [m_name, str(ctr_val), str(peer_ctr_val), cat_name, offer_str]
        rationale = f"Detected performance drop in CTR for {m_name} compared to {cat_name} peers."

    # 2. Compliance alert / regulation_change
    elif kind in ("compliance_alert", "regulation_change", "compliance"):
        body = (
            f"{m_name}, urgent compliance alert for {cat_name}: regulatory updates take effect soon. "
            f"Would you like a 1-page summary of required action items?"
        )
        template_name = "template_compliance_alert_v1"
        template_params = [m_name, cat_name]
        rationale = f"High-priority compliance alert regarding regulatory changes in {cat_name}."

    # 3. Recall due
    elif kind in ("recall_due",):
        cust_name = customer.get("identity", {}).get("name") if customer else "Your customer"
        service = trigger.get("payload", {}).get("service_due", "scheduled service")
        body = (
            f"{m_name}, {cust_name} is due for {service}. "
            f"Would you like me to send a friendly recall message with available appointment slots?"
        )
        template_name = "template_recall_due_v1"
        template_params = [m_name, str(cust_name), str(service)]
        rationale = f"Service recall due for {cust_name} at {m_name}."

    # 4. Customer winback / winback_eligible
    elif kind in ("customer_winback", "winback_eligible", "winback"):
        body = (
            f"{m_name}, several past customers haven't visited in over 60 days. "
            f"Want me to draft a targeted winback offer to re-engage them?"
        )
        template_name = "template_customer_winback_v1"
        template_params = [m_name]
        rationale = f"Winback opportunity identified for lapsed customers of {m_name}."

    # 5. Research digest / research
    elif kind in ("research_digest", "research"):
        body = (
            f"{m_name}, this week's research digest for {cat_name} highlights actionable findings. "
            f"Would you like me to share key takeaways for your practice?"
        )
        template_name = "template_research_digest_v1"
        template_params = [m_name, cat_name]
        rationale = f"Category research digest relevant to {m_name}."

    # 6. Festival / festival_upcoming
    elif kind in ("festival", "festival_upcoming"):
        festival_name = (
            trigger.get("payload", {}).get("festival")
            or trigger.get("payload", {}).get("festival_name")
            or "upcoming festival"
        )
        days = trigger.get("payload", {}).get("days_until", 7)
        body = (
            f"{m_name}, {festival_name} is in {days} days! Peers in {cat_name} are launching festive offers. "
            f"Want me to prepare a promotion?"
        )
        template_name = "template_festival_v1"
        template_params = [m_name, str(festival_name), str(days), cat_name]
        rationale = f"Festive demand spike approaching for {festival_name}."

    # 7. Curious ask
    elif kind in ("curious_ask", "curious_ask_due"):
        body = (
            f"{m_name}, quick check-in: what services or items are seeing the highest customer demand at your location this week?"
        )
        template_name = "template_curious_ask_v1"
        template_params = [m_name]
        rationale = f"Proactive check-in with merchant to gather current service demand signals."

    # 8. Seasonal / seasonal_perf_dip
    elif kind in ("seasonal", "seasonal_perf_dip", "seasonal_acquisition_dip"):
        season_name = trigger.get("payload", {}).get("season", "seasonal")
        body = (
            f"{m_name}, {season_name} trends for {cat_name} are active. "
            f"Would you like to review seasonal demand trends and growth recommendations?"
        )
        template_name = "template_seasonal_v1"
        template_params = [m_name, str(season_name), cat_name]
        rationale = f"Seasonal trend adaptation for {m_name} in {cat_name}."

    # Default fallback
    else:
        body = (
            f"{m_name}, Vera here from magicpin. We noticed an opportunity for your {cat_name} business. "
            f"Want me to share recommendations?"
        )
        template_name = "template_default_v1"
        template_params = [m_name, cat_name]
        rationale = f"General proactive engagement for {m_name}."

    return body, template_name, template_params, rationale


# ---------------------------------------------------------------------------
# Signal Selection
# ---------------------------------------------------------------------------
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
        if check_suppressed(suppression_key):
            continue

        merchant = None
        category = None
        customer = None

        if context_store is not None:
            # Must resolve merchant if merchant_id is present
            merchant_id = trigger.get("merchant_id")
            if merchant_id:
                merchant = resolve_merchant(context_store, merchant_id)
                if merchant is None:
                    # Missing essential merchant context -> skip
                    continue

            # Resolve category
            category_slug = (
                (merchant.get("category_slug") if merchant else None)
                or trigger.get("payload", {}).get("category")
            )
            if category_slug:
                category = resolve_category(context_store, category_slug)

            # Resolve customer if customer_id present
            customer_id = trigger.get("customer_id")
            if customer_id:
                customer = resolve_customer(context_store, customer_id)

        score = score_trigger(trigger, merchant, category, customer)
        if score > 0:
            qualified.append((score, trigger, merchant, category, customer))

    if not qualified:
        return None

    # Sort descending by score
    qualified.sort(key=lambda item: item[0], reverse=True)
    return qualified[0][1]


# ---------------------------------------------------------------------------
# Tick Decision Orchestrator
# ---------------------------------------------------------------------------
def process_tick(
    available_trigger_ids: List[str],
    now: Optional[str],
    context_store: Any,
    conversation_store: Any,
) -> List[TickAction]:
    """
    Full pipeline for POST /v1/tick:
    1. Gather and deduplicate available triggers.
    2. Resolve merchant, category, and customer for each.
    3. Filter out expired or suppressed triggers.
    4. Group candidate triggers by merchant to select the strongest trigger per merchant.
    5. Rank all selected triggers by score_trigger descending.
    6. Cap at 20 actions.
    7. Render deterministic templates, mark suppression keys, create conversation records.
    8. Return list of TickAction models.
    """
    if not available_trigger_ids or not context_store:
        return []

    # Deduplicate trigger IDs preserving order
    seen_ids = set()
    deduped_ids = []
    for tid in available_trigger_ids:
        if tid and tid not in seen_ids:
            seen_ids.add(tid)
            deduped_ids.append(tid)

    # Gather triggers and resolve context
    candidate_items = []
    for tid in deduped_ids:
        trigger = resolve_trigger(context_store, tid)
        if not trigger:
            continue

        if is_trigger_expired(trigger, now):
            continue

        suppression_key = trigger.get("suppression_key")
        if check_suppressed(suppression_key):
            continue

        # Resolve merchant
        merchant_id = trigger.get("merchant_id")
        merchant = resolve_merchant(context_store, merchant_id) if merchant_id else None
        if merchant_id and not merchant:
            # Cannot act without merchant context
            continue

        # Resolve category
        category_slug = (
            (merchant.get("category_slug") if merchant else None)
            or trigger.get("payload", {}).get("category")
        )
        category = resolve_category(context_store, category_slug) if category_slug else None

        # Resolve customer
        customer_id = trigger.get("customer_id")
        customer = resolve_customer(context_store, customer_id) if customer_id else None

        score = score_trigger(trigger, merchant, category, customer)
        if score > 0:
            candidate_items.append({
                "score": score,
                "trigger": trigger,
                "merchant": merchant,
                "category": category,
                "customer": customer,
                "merchant_id": merchant_id or f"m_unknown_{tid}",
            })

    if not candidate_items:
        return []

    # Group by merchant to pick the single strongest signal per merchant
    merchant_groups: Dict[str, List[Dict[str, Any]]] = {}
    for item in candidate_items:
        mid = item["merchant_id"]
        merchant_groups.setdefault(mid, []).append(item)

    selected_per_merchant = []
    for mid, group in merchant_groups.items():
        # Best trigger for this merchant
        group.sort(key=lambda x: x["score"], reverse=True)
        selected_per_merchant.append(group[0])

    # Rank all merchants' best triggers by score descending
    selected_per_merchant.sort(key=lambda x: x["score"], reverse=True)

    # Cap at 20 actions per tick
    selected_actions_data = selected_per_merchant[:20]

    actions: List[TickAction] = []
    for item in selected_actions_data:
        trg = item["trigger"]
        m = item["merchant"]
        c = item["category"]
        cust = item["customer"]

        m_id = trg.get("merchant_id") or "unknown_merchant"
        cust_id = trg.get("customer_id")
        trg_id = trg.get("id")
        sup_key = trg.get("suppression_key")

        # Reuse existing conversation if present for merchant/trigger
        existing_conv_id = None
        if conversation_store is not None:
            with conversation_store._lock:
                for cid, cdata in conversation_store._conversations.items():
                    if cdata.get("merchant_id") == m_id and (
                        cdata.get("trigger_id") == trg_id or (cust_id and cdata.get("customer_id") == cust_id)
                    ):
                        existing_conv_id = cid
                        break
        conv_id = existing_conv_id or f"conv_{uuid.uuid4().hex[:8]}"

        fallback_tuple = render_template(trg, m, c, cust)
        compact_ctx = build_compact_context(
            merchant=m,
            category=c,
            trigger=trg,
            customer=cust,
            selected_signal=trg.get("kind"),
        )
        body, template_name, template_params, rationale = compose_message(
            compact_context=compact_ctx,
            fallback_data=fallback_tuple,
            conversation_store=conversation_store,
            conversation_id=conv_id,
        )

        action = TickAction(
            conversation_id=conv_id,
            merchant_id=m_id,
            customer_id=cust_id,
            send_as="vera",
            trigger_id=trg_id,
            template_name=template_name,
            template_params=template_params,
            body=body,
            cta="open_ended",
            suppression_key=sup_key,
            rationale=rationale,
        )
        actions.append(action)

        # Mark suppression key active
        if sup_key:
            mark_suppressed(sup_key)

        # Record conversation in ConversationStore
        if conversation_store is not None:
            conversation_store.create_or_update(
                conversation_id=conv_id,
                merchant_id=m_id,
                customer_id=cust_id,
                trigger_id=trg_id,
                last_action="sent",
                state="waiting_for_reply",
            )
            conversation_store.add_sent_message(
                conv_id,
                {
                    "body": body,
                    "template_name": template_name,
                    "sent_at": now or datetime.now(timezone.utc).isoformat(),
                },
            )

    return actions
