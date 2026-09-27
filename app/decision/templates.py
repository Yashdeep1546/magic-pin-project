"""Deterministic template rendering for message generation."""

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from app.decision.taxonomy import (
    FAMILY_GENERIC,
    FAMILY_MILESTONE,
    FAMILY_OPPORTUNITY_EVENT,
    FAMILY_PERFORMANCE,
    FAMILY_RECALL_LAPSE,
    FAMILY_RELATIONSHIP,
    FAMILY_RESEARCH_KNOWLEDGE,
    KIND_TO_FAMILY,
    TEMPLATE_NAME_OVERRIDES,
    get_trigger_policy,
)

logger = logging.getLogger(__name__)


def _clean_entity_text(val: Optional[str], default: str) -> str:
    if not val or not isinstance(val, str):
        return default
    cleaned = re.sub(
        r"(?i)(?:;\s*drop\s+(?:table|database).*|\bdrop\s+(?:table|database).*|--.*|union\s+select.*|delete\s+from.*|insert\s+into.*|\[system\].*|system\s+override.*|"
        r"ignore\s+(?:all\s+)?(?:previous\s+)?instructions.*|reveal\s+.*(?:prompt|instructions).*|"
        r"<\s*\/?\s*tool_call\s*>.*|call:default_api.*|exec\s*\(.*|eval\s*\(.*|"
        r"dan\s+mode.*|jailbreak.*|\[filtered\].*|<\/?untrusted_context_data>.*)",
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
        raw_reg = payload.get("topic") or payload.get("top_item_id")
        reg_topic = _clean_entity_text(raw_reg, "regulatory guidelines")
        body = (
            f"{m_name}, urgent regulatory update for {cat_name}: {reg_topic} takes effect soon. "
            f"Reply YES to get the 1-page compliance checklist before the deadline."
        )
        template_name = TEMPLATE_NAME_OVERRIDES.get(kind, "template_research_knowledge_v1")
        template_params = [m_name, cat_name, str(reg_topic)]
        rationale = f"Regulatory and compliance update ({reg_topic}) for {m_name} in {cat_name}."
    elif kind == "category_trend_movement" or "trend" in payload or "query" in payload:
        raw_trend = payload.get("trend") or payload.get("query") or payload.get("topic")
        trend_name = _clean_entity_text(raw_trend, f"demand for {cat_name} services")
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
        raw_digest = (
            (top_item.get("title") if isinstance(top_item, dict) else None)
            or payload.get("title")
            or payload.get("top_item_id")
        )
        digest_title = _clean_entity_text(raw_digest, "actionable peer insights")
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
        delta = payload.get("delta_pct")
        if delta is not None:
            pct_str = f"+{round(delta * 100)}%" if isinstance(delta, float) and delta <= 1.0 else f"+{delta}%" if not str(delta).startswith("+") else str(delta)
            body_metric_str = f"jumped {pct_str} this week"
        else:
            pct_str = ""
            views_val = merchant.get("performance", {}).get("views") if merchant else None
            body_metric_str = f"is reaching steady interest ({views_val} profile views)" if views_val else "is showing strong activity"
        driver = payload.get("likely_driver")
        driver_str = f" likely driven by {driver.replace('_', ' ')}" if driver else ""
        body = (
            f"{m_name}, great momentum! Your {metric} {body_metric_str}{driver_str}. "
            f"Reply YES to launch a follow-up offer and convert this traffic before it cools."
        )
        template_name = TEMPLATE_NAME_OVERRIDES.get(kind, "template_performance_v1")
        template_params = [m_name, str(metric), pct_str, offer_str]
        rationale = f"Performance spike in {metric} detected for {m_name}."
    else:
        perf = merchant.get("performance", {}) if merchant else {}
        raw_ctr = perf.get("ctr")
        peer_stats = category.get("peer_stats", {}) if category else {}
        raw_peer_ctr = peer_stats.get("avg_ctr")

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
        elif raw_ctr is not None and raw_peer_ctr is not None:
            ctr_val = round(raw_ctr * 100, 1)
            peer_ctr_val = round(raw_peer_ctr * 100, 1)
            body = (
                f"{m_name}, your CTR is {ctr_val}% vs {peer_ctr_val}% for {cat_name} peers. "
                f"You already have {offer_str} active — reply YES to boost visibility before this dip widens."
            )
            template_params = [m_name, str(ctr_val), str(peer_ctr_val), cat_name, offer_str]
            rationale = f"Detected performance drop in CTR for {m_name} compared to {cat_name} peers."
        else:
            body = (
                f"{m_name}, we've noticed a recent shift in local searches for {cat_name}. "
                f"You already have {offer_str} active — reply YES to boost visibility before this dip widens."
            )
            template_params = [m_name, cat_name, offer_str]
            rationale = f"Qualitative shift in local search interest for {m_name} in {cat_name}."

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
        raw_cust = customer.get("identity", {}).get("name") if customer else None
        cust_name = _clean_entity_text(raw_cust, "Your customer")
        appt_time = _clean_entity_text(payload.get("time") or payload.get("slot"), "tomorrow")
        body = (
            f"{m_name}, {cust_name} has an appointment scheduled for {appt_time}. "
            f"Reply YES to send the appointment reminder and prep instructions now."
        )
        template_name = TEMPLATE_NAME_OVERRIDES.get(kind, "template_recall_lapse_v1")
        template_params = [m_name, str(cust_name), str(appt_time)]
        rationale = f"Upcoming appointment reminder for {cust_name} at {m_name}."
    elif kind == "unplanned_slot_open":
        slot_time = _clean_entity_text(payload.get("slot") or payload.get("time"), "an open slot tomorrow")
        body = (
            f"{m_name}, you have {slot_time} open. "
            f"Reply YES to reach out to nearby lapsed regulars due for a visit before the slot goes wasted."
        )
        template_name = TEMPLATE_NAME_OVERRIDES.get(kind, "template_recall_lapse_v1")
        template_params = [m_name, str(slot_time)]
        rationale = f"Capacity optimization for unplanned open slot ({slot_time}) at {m_name}."
    elif kind in ("recall_due", "chronic_refill_due"):
        raw_cust = customer.get("identity", {}).get("name") if customer else None
        cust_name = _clean_entity_text(raw_cust, "Your customer")
        raw_service = (
            payload.get("service_due")
            or payload.get("service")
            or payload.get("medicine_name")
        )
        service = _clean_entity_text(raw_service, "scheduled service")
        cat_lower = (cat_name or "").lower()

        if "gym" in cat_lower or "fitness" in cat_lower:
            service_term = "membership renewal" if service in ("scheduled service", "chronic_refill_due") else service
            member_term = f"Member {cust_name}" if cust_name != "Your customer" else "A regular member"
            body = (
                f"{m_name}, {member_term} is due for {service_term}. "
                f"Reply YES to send the renewal invite before their workout streak breaks."
            )
            template_params = [m_name, str(cust_name), str(service_term)]
            rationale = f"Membership renewal due for {cust_name} at {m_name}."
        elif "dent" in cat_lower:
            service_term = "a recall check-up" if service in ("scheduled service", "chronic_refill_due") else service
            patient_term = f"Patient {cust_name}" if cust_name != "Your customer" else "A patient"
            body = (
                f"{m_name}, {patient_term} is due for {service_term}. "
                f"Reply YES to send the recall invite before their preferred slots fill up."
            )
            template_params = [m_name, str(cust_name), str(service_term)]
            rationale = f"Dental recall check-up due for {cust_name} at {m_name}."
        elif "salon" in cat_lower or "spa" in cat_lower:
            service_term = "an appointment" if service in ("scheduled service", "chronic_refill_due") else service
            client_term = f"Client {cust_name}" if cust_name != "Your customer" else "A regular client"
            body = (
                f"{m_name}, {client_term} is due for {service_term}. "
                f"Reply YES to send the booking invite before chairs fill up."
            )
            template_params = [m_name, str(cust_name), str(service_term)]
            rationale = f"Salon appointment due for {cust_name} at {m_name}."
        elif "restaur" in cat_lower or "cafe" in cat_lower:
            diner_term = f"Diner {cust_name}" if cust_name != "Your customer" else "A regular diner"
            body = (
                f"{m_name}, {diner_term} is due for another visit. "
                f"Reply YES to send a table reservation invite before peak covers begin."
            )
            template_params = [m_name, str(cust_name)]
            rationale = f"Diner re-engagement opportunity for {cust_name} at {m_name}."
        else:
            service_term = "a prescription refill" if (kind == "chronic_refill_due" or service in ("scheduled service", "chronic_refill_due")) else service
            body = (
                f"{m_name}, {cust_name} is due for {service_term}. "
                f"Reply YES to send the recall invite before their preferred slots fill up."
            )
            template_params = [m_name, str(cust_name), str(service_term)]
            rationale = f"Service recall due for {cust_name} at {m_name}."

        template_name = TEMPLATE_NAME_OVERRIDES.get(kind, "template_recall_lapse_v1")
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
            raw_cust = customer.get("identity", {}).get("name")
            cust_name = _clean_entity_text(raw_cust, "A regular customer")
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
        raw_fest = payload.get("festival") or payload.get("festival_name")
        festival_name = _clean_entity_text(raw_fest, "upcoming festival")
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
        raw_city = payload.get("city")
        city = _clean_entity_text(raw_city, "your area")
        body = (
            f"{m_name}, heatwave alert ({temp_str}) in {city} today. "
            f"Reply YES to launch a weather-timed footfall promotion before the afternoon rush."
        )
        template_name = TEMPLATE_NAME_OVERRIDES.get(kind, "template_opportunity_event_v1")
        template_params = [m_name, temp_str, str(city), cat_name]
        rationale = f"Weather heatwave opportunity ({temp_str}) for {m_name} in {city}."
    elif kind == "local_news_event":
        raw_news = payload.get("event") or payload.get("headline") or payload.get("summary")
        news = _clean_entity_text(raw_news, "a local traffic advisory")
        body = (
            f"{m_name}, local update: {news}. "
            f"Reply YES to send this advisory update to scheduled customers now."
        )
        template_name = TEMPLATE_NAME_OVERRIDES.get(kind, "template_opportunity_event_v1")
        template_params = [m_name, str(news)]
        rationale = f"Local news event ({news}) affecting {m_name}."
    elif kind == "competitor_opened":
        raw_comp = payload.get("competitor_name")
        comp_name = _clean_entity_text(raw_comp, "A new competitor")
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
        raw_season = payload.get("season")
        season_name = _clean_entity_text(raw_season, "seasonal")
        body = (
            f"{m_name}, {season_name} trends for {cat_name} are active. "
            f"Reply YES to get the seasonal growth checklist before the demand window closes."
        )
        template_name = TEMPLATE_NAME_OVERRIDES.get(kind, "template_opportunity_event_v1")
        template_params = [m_name, str(season_name), cat_name]
        rationale = f"Seasonal trend adaptation for {m_name} in {cat_name}."
    elif kind == "ipl_match_today":
        raw_match = payload.get("match")
        match = _clean_entity_text(raw_match, "big match today")
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
        raw_theme = str(payload.get("theme", "service speed")).replace("_", " ")
        theme = _clean_entity_text(raw_theme, "service speed")
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


def render_template(
    trigger: Any,
    merchant: Optional[Dict[str, Any]] = None,
    category: Optional[Dict[str, Any]] = None,
    customer: Optional[Dict[str, Any]] = None,
    resolved_context: Optional[Any] = None,
) -> Tuple[str, str, List[str], str]:
    """
    Renders deterministic message body, template name, template parameters, and rationale
    based on template families and trigger payload shape.
    Accepts either a single ResolvedContext object or individual component dicts.
    Returns: (body, template_name, template_params, rationale)
    """
    if resolved_context is not None:
        rc = resolved_context
        trg = getattr(rc, "trigger", {})
        m = getattr(rc, "merchant", None)
        c = getattr(rc, "category", None)
        cust = getattr(rc, "customer", None)
    elif hasattr(trigger, "trigger") and hasattr(trigger, "merchant"):
        rc = trigger
        trg = getattr(rc, "trigger", {})
        m = getattr(rc, "merchant", None)
        c = getattr(rc, "category", None)
        cust = getattr(rc, "customer", None)
    else:
        trg = trigger or {}
        m = merchant
        c = category
        cust = customer

    kind = trg.get("kind", trg.get("type", "default"))
    payload = trg.get("payload", {})
    if not isinstance(payload, dict):
        payload = {}

    raw_m_name = (
        m.get("identity", {}).get("name")
        or m.get("name")
    ) if m else None
    m_name = _clean_entity_text(raw_m_name, "Merchant Partner")

    raw_cat_name = (
        c.get("display_name")
        or c.get("slug")
    ) if c else None
    cat_name = _clean_entity_text(raw_cat_name, "your category")

    active_offers = [
        o.get("title") for o in m.get("offers", []) if o.get("status") == "active"
    ] if m else []
    raw_offer = active_offers[0] if active_offers else None
    offer_str = _clean_entity_text(raw_offer, "an active promotion")

    policy = get_trigger_policy(kind)
    family = policy.family
    canonical_kind = policy.name

    if family == FAMILY_RESEARCH_KNOWLEDGE:
        return _render_research_knowledge(canonical_kind, payload, m_name, cat_name, offer_str)
    elif family == FAMILY_PERFORMANCE:
        return _render_performance(canonical_kind, payload, m_name, cat_name, offer_str, m, c)
    elif family == FAMILY_RECALL_LAPSE:
        return _render_recall_lapse(canonical_kind, payload, m_name, cat_name, offer_str, cust)
    elif family == FAMILY_OPPORTUNITY_EVENT:
        return _render_opportunity_event(canonical_kind, payload, m_name, cat_name, offer_str)
    elif family == FAMILY_MILESTONE:
        return _render_milestone(canonical_kind, payload, m_name, cat_name, offer_str)
    elif family == FAMILY_RELATIONSHIP:
        return _render_relationship(canonical_kind, payload, m_name, cat_name, offer_str)
    else:
        return _render_generic_fallback(kind, trg.get("id"), m_name, cat_name, offer_str)


__all__ = [
    "render_template",
    "_clean_entity_text",
    "_render_research_knowledge",
    "_render_performance",
    "_render_recall_lapse",
    "_render_opportunity_event",
    "_render_milestone",
    "_render_relationship",
    "_render_generic_fallback",
]
