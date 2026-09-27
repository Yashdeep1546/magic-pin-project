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

import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Template Families and Trigger Taxonomy (§4.3)
# ---------------------------------------------------------------------------
FAMILY_RESEARCH_KNOWLEDGE = "research_knowledge"
FAMILY_PERFORMANCE = "performance"
FAMILY_RECALL_LAPSE = "recall_lapse"
FAMILY_OPPORTUNITY_EVENT = "opportunity_event"
FAMILY_MILESTONE = "milestone"
FAMILY_RELATIONSHIP = "relationship"
FAMILY_GENERIC = "generic_fallback"

# Mapping from taxonomy kind to family
KIND_TO_FAMILY: Dict[str, str] = {
    # External triggers (§4.3)
    "category_research_digest_release": FAMILY_RESEARCH_KNOWLEDGE,
    "research_digest": FAMILY_RESEARCH_KNOWLEDGE,
    "research": FAMILY_RESEARCH_KNOWLEDGE,
    "regulation_change": FAMILY_RESEARCH_KNOWLEDGE,
    "category_trend_movement": FAMILY_RESEARCH_KNOWLEDGE,
    "cde_opportunity": FAMILY_RESEARCH_KNOWLEDGE,
    "festival_upcoming": FAMILY_OPPORTUNITY_EVENT,
    "festival": FAMILY_OPPORTUNITY_EVENT,
    "weather_heatwave": FAMILY_OPPORTUNITY_EVENT,
    "local_news_event": FAMILY_OPPORTUNITY_EVENT,
    "competitor_opened": FAMILY_OPPORTUNITY_EVENT,
    "ipl_match_today": FAMILY_OPPORTUNITY_EVENT,
    "seasonal": FAMILY_OPPORTUNITY_EVENT,
    "category_seasonal": FAMILY_OPPORTUNITY_EVENT,

    # Internal triggers (§4.3)
    "perf_spike": FAMILY_PERFORMANCE,
    "perf_dip": FAMILY_PERFORMANCE,
    "performance_drop": FAMILY_PERFORMANCE,
    "performance_dip": FAMILY_PERFORMANCE,
    "seasonal_perf_dip": FAMILY_PERFORMANCE,
    "seasonal_acquisition_dip": FAMILY_PERFORMANCE,
    "milestone_reached": FAMILY_MILESTONE,
    "dormant_with_vera": FAMILY_RELATIONSHIP,
    "customer_lapsed_soft": FAMILY_RECALL_LAPSE,
    "customer_lapsed_hard": FAMILY_RECALL_LAPSE,
    "appointment_tomorrow": FAMILY_RECALL_LAPSE,
    "review_theme_emerged": FAMILY_RELATIONSHIP,
    "scheduled_recurring": FAMILY_RELATIONSHIP,
    "curious_ask": FAMILY_RELATIONSHIP,
    "curious_ask_due": FAMILY_RELATIONSHIP,

    # Customer-scoped triggers (§4.4 / engagement loops)
    "recall_due": FAMILY_RECALL_LAPSE,
    "unplanned_slot_open": FAMILY_RECALL_LAPSE,
    "chronic_refill_due": FAMILY_RECALL_LAPSE,
    "customer_winback": FAMILY_RECALL_LAPSE,
    "winback_eligible": FAMILY_RECALL_LAPSE,
    "winback": FAMILY_RECALL_LAPSE,

    # Legacy / alias kinds
    "compliance_alert": FAMILY_RESEARCH_KNOWLEDGE,
    "compliance": FAMILY_RESEARCH_KNOWLEDGE,
}

# Template name overrides for test backwards-compatibility
TEMPLATE_NAME_OVERRIDES: Dict[str, str] = {
    "compliance_alert": "template_compliance_alert_v1",
    "compliance": "template_compliance_alert_v1",
    "performance_drop": "template_performance_drop_v1",
    "performance_dip": "template_performance_drop_v1",
    "recall_due": "template_recall_due_v1",
    "customer_winback": "template_customer_winback_v1",
    "winback_eligible": "template_customer_winback_v1",
    "winback": "template_customer_winback_v1",
    "research_digest": "template_research_digest_v1",
    "research": "template_research_digest_v1",
    "festival": "template_festival_v1",
    "seasonal": "template_seasonal_v1",
    "category_seasonal": "template_seasonal_v1",
    "curious_ask": "template_curious_ask_v1",
    "curious_ask_due": "template_curious_ask_v1",
}

# Deprecated: Retained for backward-compatibility with external imports.
# Prioritization is now data-driven via TriggerContext.urgency (1-5).
TRIGGER_PRIORITY: Dict[str, int] = {
    "compliance_alert": 100,
    "regulation_change": 100,
    "compliance": 100,
    "recall_due": 95,
    "performance_drop": 90,
    "perf_dip": 90,
    "performance_dip": 90,
    "customer_winback": 80,
    "winback_eligible": 80,
    "winback": 80,
    "research_digest": 70,
    "research": 70,
    "festival": 60,
    "festival_upcoming": 60,
    "curious_ask": 50,
    "curious_ask_due": 50,
    "seasonal": 40,
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



# ---------------------------------------------------------------------------
# Trigger Scoring (Data-driven Urgency 1-5 + Context Relevance)
# ---------------------------------------------------------------------------
def score_trigger(
    trigger: Dict[str, Any],
    merchant: Optional[Dict[str, Any]] = None,
    category: Optional[Dict[str, Any]] = None,
    customer: Optional[Dict[str, Any]] = None,
) -> int:
    """
    Computes deterministic score for a trigger:
    score = urgency_score + merchant_relevance + category_relevance + customer_relevance - suppression_penalty

    Data-driven approach reading urgency from TriggerContext.urgency (1-5)
    combined with contextual relevance.
    """
    try:
        raw_urgency = trigger.get("urgency", 1)
        urgency = int(raw_urgency) if raw_urgency is not None else 1
    except (ValueError, TypeError):
        urgency = 1
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
    suppression_penalty = 1000 if check_suppressed(suppression_key) else 0

    return urgency_score + merchant_relevance + category_relevance + customer_relevance - suppression_penalty


# ---------------------------------------------------------------------------
# Deterministic Template Rendering (Generic Families)
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


def _render_research_knowledge(
    kind: str,
    payload: Dict[str, Any],
    m_name: str,
    cat_name: str,
    offer_str: str,
) -> Tuple[str, str, List[str], str]:
    if kind in ("regulation_change", "compliance_alert", "compliance") or "deadline_iso" in payload:
        reg_topic = payload.get("topic") or payload.get("top_item_id") or "regulatory guidelines"
        body = (
            f"{m_name}, urgent regulatory update for {cat_name}: {reg_topic} takes effect soon. "
            f"Reply YES to get the 1-page compliance checklist before the deadline."
        )
        template_name = TEMPLATE_NAME_OVERRIDES.get(kind, "template_research_knowledge_v1")
        template_params = [m_name, cat_name, str(reg_topic)]
        rationale = f"Regulatory and compliance update ({reg_topic}) for {m_name} in {cat_name}."
    elif kind == "category_trend_movement" or "trend" in payload or "query" in payload:
        trend_name = payload.get("trend") or payload.get("query") or payload.get("topic") or f"demand for {cat_name} services"
        pct = payload.get("delta_pct") or payload.get("growth_pct") or 50
        pct_str = f"+{round(pct * 100)}%" if isinstance(pct, float) and pct <= 1.0 else f"+{pct}%" if not str(pct).startswith("+") else str(pct)
        body = (
            f"{m_name}, search interest for '{trend_name}' in {cat_name} is up {pct_str}. "
            f"You have {offer_str} active — reply YES to launch a targeted promo before this surge ends."
        )
        template_name = TEMPLATE_NAME_OVERRIDES.get(kind, "template_research_knowledge_v1")
        template_params = [m_name, str(trend_name), pct_str, cat_name, offer_str]
        rationale = f"Category search trend movement ({trend_name} {pct_str}) relevant to {m_name}."
    else:
        top_item = payload.get("top_item")
        digest_title = (
            (top_item.get("title") if isinstance(top_item, dict) else None)
            or payload.get("title")
            or payload.get("top_item_id")
            or "actionable peer insights"
        )
        body = (
            f"{m_name}, this week's research digest for {cat_name} highlights: {digest_title}. "
            f"Reply YES to get the 3-step action checklist for your practice before the weekend."
        )
        template_name = TEMPLATE_NAME_OVERRIDES.get(kind, "template_research_knowledge_v1")
        template_params = [m_name, cat_name, str(digest_title)]
        rationale = f"Category research digest ({digest_title}) relevant to {m_name} in {cat_name}."

    return body, template_name, template_params, rationale


def _render_performance(
    kind: str,
    payload: Dict[str, Any],
    m_name: str,
    cat_name: str,
    offer_str: str,
    merchant: Optional[Dict[str, Any]],
    category: Optional[Dict[str, Any]],
) -> Tuple[str, str, List[str], str]:
    if kind == "perf_spike":
        metric = payload.get("metric", "views")
        delta = payload.get("delta_pct", 0.25)
        pct_str = f"+{round(delta * 100)}%" if isinstance(delta, float) and delta <= 1.0 else f"+{delta}%" if not str(delta).startswith("+") else str(delta)
        driver = payload.get("likely_driver")
        driver_str = f" likely driven by {driver.replace('_', ' ')}" if driver else ""
        body = (
            f"{m_name}, great momentum! Your {metric} jumped {pct_str} this week{driver_str}. "
            f"Reply YES to launch a follow-up offer and convert this traffic before it cools."
        )
        template_name = TEMPLATE_NAME_OVERRIDES.get(kind, "template_performance_v1")
        template_params = [m_name, str(metric), pct_str, offer_str]
        rationale = f"Performance spike ({pct_str} in {metric}) detected for {m_name}."
    else:
        perf = merchant.get("performance", {}) if merchant else {}
        ctr_val = round(perf.get("ctr", 0.021) * 100, 1)
        peer_stats = category.get("peer_stats", {}) if category else {}
        peer_ctr_val = round(peer_stats.get("avg_ctr", 0.030) * 100, 1)

        metric = payload.get("metric")
        delta = payload.get("delta_pct")
        if metric and delta and metric != "ctr":
            drop_str = f"{abs(round(delta * 100))}%" if isinstance(delta, float) and abs(delta) <= 1.0 else f"{abs(delta)}%"
            body = (
                f"{m_name}, your {metric} dipped {drop_str} this week vs peer average for {cat_name}. "
                f"You already have {offer_str} active — reply YES to boost visibility before this dip widens."
            )
            template_params = [m_name, str(metric), drop_str, cat_name, offer_str]
            rationale = f"Detected performance drop in {metric} ({drop_str}) for {m_name}."
        else:
            body = (
                f"{m_name}, your CTR is {ctr_val}% vs {peer_ctr_val}% for {cat_name} peers. "
                f"You already have {offer_str} active — reply YES to boost visibility before this dip widens."
            )
            template_params = [m_name, str(ctr_val), str(peer_ctr_val), cat_name, offer_str]
            rationale = f"Detected performance drop in CTR for {m_name} compared to {cat_name} peers."

        template_name = TEMPLATE_NAME_OVERRIDES.get(kind, "template_performance_drop_v1")

    return body, template_name, template_params, rationale


def _render_recall_lapse(
    kind: str,
    payload: Dict[str, Any],
    m_name: str,
    cat_name: str,
    offer_str: str,
    customer: Optional[Dict[str, Any]],
) -> Tuple[str, str, List[str], str]:
    if kind == "appointment_tomorrow":
        cust_name = customer.get("identity", {}).get("name") if customer else "Your customer"
        appt_time = payload.get("time") or payload.get("slot") or "tomorrow"
        body = (
            f"{m_name}, {cust_name} has an appointment scheduled for {appt_time}. "
            f"Reply YES to send the appointment reminder and prep instructions now."
        )
        template_name = TEMPLATE_NAME_OVERRIDES.get(kind, "template_recall_lapse_v1")
        template_params = [m_name, str(cust_name), str(appt_time)]
        rationale = f"Upcoming appointment reminder for {cust_name} at {m_name}."
    elif kind == "unplanned_slot_open":
        slot_time = payload.get("slot") or payload.get("time") or "an open slot tomorrow"
        body = (
            f"{m_name}, you have {slot_time} open. "
            f"Reply YES to reach out to nearby lapsed regulars due for a visit before the slot goes wasted."
        )
        template_name = TEMPLATE_NAME_OVERRIDES.get(kind, "template_recall_lapse_v1")
        template_params = [m_name, str(slot_time)]
        rationale = f"Capacity optimization for unplanned open slot ({slot_time}) at {m_name}."
    elif kind in ("recall_due", "chronic_refill_due"):
        cust_name = customer.get("identity", {}).get("name") if customer else "Your customer"
        service = (
            payload.get("service_due")
            or payload.get("service")
            or payload.get("medicine_name")
            or "scheduled service"
        )
        body = (
            f"{m_name}, {cust_name} is due for {service}. "
            f"Reply YES to send the recall invite before their preferred slots fill up."
        )
        template_name = TEMPLATE_NAME_OVERRIDES.get(kind, "template_recall_lapse_v1")
        template_params = [m_name, str(cust_name), str(service)]
        rationale = f"Service recall due for {cust_name} at {m_name}."
    elif kind in ("customer_winback", "winback_eligible", "winback"):
        body = (
            f"{m_name}, several past customers haven't visited in over 60 days. "
            f"Reply YES to send a targeted winback offer before they switch to competitors."
        )
        template_name = TEMPLATE_NAME_OVERRIDES.get(kind, "template_customer_winback_v1")
        template_params = [m_name]
        rationale = f"Winback opportunity identified for lapsed customers of {m_name}."
    else:  # customer_lapsed_soft, customer_lapsed_hard
        if customer:
            cust_name = customer.get("identity", {}).get("name", "A regular customer")
            body = (
                f"{m_name}, {cust_name} hasn't visited in over 60 days. "
                f"Reply YES to send a targeted re-engagement offer before they switch to competitors."
            )
            template_params = [m_name, str(cust_name)]
            rationale = f"Winback opportunity identified for {cust_name} at {m_name}."
        else:
            body = (
                f"{m_name}, several past customers haven't visited in over 60 days. "
                f"Reply YES to send a targeted winback offer before they switch to competitors."
            )
            template_params = [m_name]
            rationale = f"Winback opportunity identified for lapsed customers of {m_name}."
        template_name = TEMPLATE_NAME_OVERRIDES.get(kind, "template_recall_lapse_v1")

    return body, template_name, template_params, rationale


def _render_opportunity_event(
    kind: str,
    payload: Dict[str, Any],
    m_name: str,
    cat_name: str,
    offer_str: str,
) -> Tuple[str, str, List[str], str]:
    if kind in ("festival_upcoming", "festival"):
        festival_name = (
            payload.get("festival")
            or payload.get("festival_name")
            or "upcoming festival"
        )
        days = payload.get("days_until", 7)
        body = (
            f"{m_name}, {festival_name} is in {days} days! Peers in {cat_name} are launching festive offers. "
            f"Reply YES to lock in your festive campaign before competitor bookings open."
        )
        template_name = TEMPLATE_NAME_OVERRIDES.get(kind, "template_opportunity_event_v1")
        template_params = [m_name, str(festival_name), str(days), cat_name]
        rationale = f"Festive demand spike approaching for {festival_name}."
    elif kind == "weather_heatwave":
        temp = payload.get("temp_c") or payload.get("temperature") or "42°C"
        temp_str = f"{temp}°C" if isinstance(temp, (int, float)) or (isinstance(temp, str) and not temp.endswith("C")) else str(temp)
        city = payload.get("city") or "your area"
        body = (
            f"{m_name}, heatwave alert ({temp_str}) in {city} today. "
            f"Reply YES to launch a weather-timed footfall promotion before the afternoon rush."
        )
        template_name = TEMPLATE_NAME_OVERRIDES.get(kind, "template_opportunity_event_v1")
        template_params = [m_name, temp_str, str(city), cat_name]
        rationale = f"Weather heatwave opportunity ({temp_str}) for {m_name} in {city}."
    elif kind == "local_news_event":
        news = payload.get("event") or payload.get("headline") or payload.get("summary") or "a local traffic advisory"
        body = (
            f"{m_name}, local update: {news}. "
            f"Reply YES to send this advisory update to scheduled customers now."
        )
        template_name = TEMPLATE_NAME_OVERRIDES.get(kind, "template_opportunity_event_v1")
        template_params = [m_name, str(news)]
        rationale = f"Local news event ({news}) affecting {m_name}."
    elif kind == "competitor_opened":
        comp_name = payload.get("competitor_name") or "A new competitor"
        dist = payload.get("distance_km", 1.5)
        dist_str = f"{dist}km away" if isinstance(dist, (int, float)) else str(dist)
        body = (
            f"{m_name}, heads-up: {comp_name} recently opened {dist_str}. "
            f"You have {offer_str} active — reply YES to highlight your unique offer before customers look elsewhere."
        )
        template_name = TEMPLATE_NAME_OVERRIDES.get(kind, "template_opportunity_event_v1")
        template_params = [m_name, str(comp_name), dist_str, offer_str]
        rationale = f"New competitor ({comp_name}, {dist_str}) opened near {m_name}."
    elif kind in ("seasonal", "category_seasonal"):
        season_name = payload.get("season", "seasonal")
        body = (
            f"{m_name}, {season_name} trends for {cat_name} are active. "
            f"Reply YES to get the seasonal growth checklist before the demand window closes."
        )
        template_name = TEMPLATE_NAME_OVERRIDES.get(kind, "template_opportunity_event_v1")
        template_params = [m_name, str(season_name), cat_name]
        rationale = f"Seasonal trend adaptation for {m_name} in {cat_name}."
    elif kind == "ipl_match_today":
        match = payload.get("match") or "big match today"
        body = (
            f"{m_name}, match day alert: {match}! "
            f"Reply YES to launch your match-day promotion before toss time."
        )
        template_name = TEMPLATE_NAME_OVERRIDES.get(kind, "template_opportunity_event_v1")
        template_params = [m_name, str(match), cat_name]
        rationale = f"Sports match event opportunity for {m_name}."
    else:
        body = (
            f"{m_name}, an opportunity has come up for your {cat_name} business. "
            f"Reply YES to activate a promotion around {offer_str} before the weekend rush."
        )
        template_name = TEMPLATE_NAME_OVERRIDES.get(kind, "template_opportunity_event_v1")
        template_params = [m_name, cat_name, offer_str]
        rationale = f"Opportunity event detected for {m_name}."

    return body, template_name, template_params, rationale


def _render_milestone(
    kind: str,
    payload: Dict[str, Any],
    m_name: str,
    cat_name: str,
    offer_str: str,
) -> Tuple[str, str, List[str], str]:
    metric = str(payload.get("metric", "reviews")).replace("_", " ")
    if payload.get("is_imminent"):
        target = payload.get("milestone_value", 100)
        now_val = payload.get("value_now", target - 5)
        remaining = max(1, target - now_val)
        body = (
            f"{m_name}, exciting news! You are at {now_val} {metric} — just {remaining} away from your {target} milestone! "
            f"Reply YES to launch a quick push and cross the milestone this week."
        )
        template_params = [m_name, str(now_val), str(target), metric]
        rationale = f"Approaching milestone ({now_val}/{target} {metric}) for {m_name}."
    else:
        milestone = payload.get("milestone_value") or payload.get("milestone") or "100"
        body = (
            f"{m_name}, congratulations on crossing {milestone} {metric}! "
            f"Reply YES to post a thank-you customer reward before the celebration momentum fades."
        )
        template_params = [m_name, str(milestone), metric]
        rationale = f"Milestone achieved ({milestone} {metric}) for {m_name}."

    template_name = TEMPLATE_NAME_OVERRIDES.get(kind, "template_milestone_v1")
    return body, template_name, template_params, rationale


def _render_relationship(
    kind: str,
    payload: Dict[str, Any],
    m_name: str,
    cat_name: str,
    offer_str: str,
) -> Tuple[str, str, List[str], str]:
    if kind == "review_theme_emerged":
        theme = str(payload.get("theme", "service speed")).replace("_", " ")
        count = payload.get("occurrences_30d", 3)
        body = (
            f"{m_name}, we noticed {count} recent customer reviews mentioning '{theme}'. "
            f"Reply YES to see the 3-step action plan to address this review feedback before ratings drop."
        )
        template_name = TEMPLATE_NAME_OVERRIDES.get(kind, "template_relationship_v1")
        template_params = [m_name, str(theme), str(count)]
        rationale = f"Customer review theme '{theme}' emerged for {m_name} ({count} mentions)."
    elif kind == "dormant_with_vera":
        days = payload.get("days_since_last_merchant_message", 14)
        body = (
            f"{m_name}, Vera here from magicpin. It has been {days} days since our last chat — "
            f"reply YES to see your top 3 growth opportunities for {cat_name} this week."
        )
        template_name = TEMPLATE_NAME_OVERRIDES.get(kind, "template_relationship_v1")
        template_params = [m_name, str(days), cat_name]
        rationale = f"Dormancy check-in for {m_name} ({days} days inactive with Vera)."
    else:  # scheduled_recurring, curious_ask, curious_ask_due
        body = (
            f"{m_name}, quick check-in: what services or items are seeing the highest customer demand at your location this week?"
        )
        template_name = TEMPLATE_NAME_OVERRIDES.get(kind, "template_curious_ask_v1")
        template_params = [m_name]
        rationale = f"Scheduled proactive check-in to gather customer demand signals for {m_name}."

    return body, template_name, template_params, rationale


def _render_generic_fallback(
    kind: str,
    trigger_id: Optional[str],
    m_name: str,
    cat_name: str,
    offer_str: str,
) -> Tuple[str, str, List[str], str]:
    logger.warning(
        "Unmatched trigger kind '%s' falling back to generic family. Trigger ID: %s",
        kind,
        trigger_id,
    )
    body = (
        f"{m_name}, Vera here from magicpin. We noticed an opportunity for your {cat_name} business. "
        f"You have {offer_str} active — reply YES to launch recommendations before the weekend."
    )
    template_name = "template_default_v1"
    template_params = [m_name, cat_name, offer_str]
    rationale = f"Safe generic fallback for unhandled trigger kind '{kind}' at {m_name}."
    return body, template_name, template_params, rationale
    return body, template_name, template_params, rationale


def render_template(
    trigger: Dict[str, Any],
    merchant: Optional[Dict[str, Any]],
    category: Optional[Dict[str, Any]],
    customer: Optional[Dict[str, Any]],
) -> Tuple[str, str, List[str], str]:
    """
    Renders deterministic message body, template name, template parameters, and rationale
    based on template families and trigger payload shape.
    Returns: (body, template_name, template_params, rationale)
    """
    kind = trigger.get("kind", trigger.get("type", "default"))
    payload = trigger.get("payload", {})
    if not isinstance(payload, dict):
        payload = {}

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

    family = KIND_TO_FAMILY.get(kind)

    if family == FAMILY_RESEARCH_KNOWLEDGE:
        return _render_research_knowledge(kind, payload, m_name, cat_name, offer_str)
    elif family == FAMILY_PERFORMANCE:
        return _render_performance(kind, payload, m_name, cat_name, offer_str, merchant, category)
    elif family == FAMILY_RECALL_LAPSE:
        return _render_recall_lapse(kind, payload, m_name, cat_name, offer_str, customer)
    elif family == FAMILY_OPPORTUNITY_EVENT:
        return _render_opportunity_event(kind, payload, m_name, cat_name, offer_str)
    elif family == FAMILY_MILESTONE:
        return _render_milestone(kind, payload, m_name, cat_name, offer_str)
    elif family == FAMILY_RELATIONSHIP:
        return _render_relationship(kind, payload, m_name, cat_name, offer_str)
    else:
        return _render_generic_fallback(kind, trigger.get("id"), m_name, cat_name, offer_str)


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

    # Sort descending by score, tie-breaking by trigger ID for stability
    qualified.sort(key=lambda item: (item[0], str(item[1].get("id", ""))), reverse=True)
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
        # Best trigger for this merchant (tie-break by trigger id)
        group.sort(key=lambda x: (x["score"], str(x["trigger"].get("id", ""))), reverse=True)
        selected_per_merchant.append(group[0])

    # Rank all merchants' best triggers by score descending (tie-break by merchant_id)
    selected_per_merchant.sort(key=lambda x: (x["score"], str(x["merchant_id"])), reverse=True)

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
