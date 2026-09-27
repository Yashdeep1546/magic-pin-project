"""Grounded LLM message composer for Magicpin Vera bot.

Builds compact context with strict boundaries and allowed/forbidden claims,
invokes LLM with timeout, and falls back to deterministic template on any failure.
"""

import json
import logging
import os
import re
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib import error as urlerror, request as urlrequest

from app.config import settings
from app.output_validator import build_evidence_ledger, validate_message

logger = logging.getLogger("magicpin-bot")

# ---------------------------------------------------------------------------
# Category Voice Policies
# ---------------------------------------------------------------------------
CATEGORY_POLICIES: Dict[str, str] = {
    "dentist": "professional/clinical/specific/no exaggerated health claims",
    "dentists": "professional/clinical/specific/no exaggerated health claims",
    "salon": "visual/occasion-driven",
    "salons": "visual/occasion-driven",
    "restaurant": "timely/offer-oriented",
    "restaurants": "timely/offer-oriented",
    "gym": "motivational/progress-oriented",
    "gyms": "motivational/progress-oriented",
    "pharmacy": "utility-first/very conservative",
    "pharmacies": "utility-first/very conservative",
}


def resolve_voice_policy(category: Optional[Dict[str, Any]], cat_slug: str) -> Dict[str, Any]:
    """
    Dynamically resolve category voice policy from category.voice payload,
    falling back to CATEGORY_POLICIES baseline if voice configuration is absent.
    """
    c = category or {}
    voice = c.get("voice") or {}
    tone = voice.get("tone")
    register = voice.get("register")
    code_mix = voice.get("code_mix")
    vocab_allowed = voice.get("vocab_allowed") or []
    taboos = voice.get("vocab_taboo") or voice.get("taboos") or []

    if not tone:
        # Fallback to predefined baseline
        tone = CATEGORY_POLICIES.get(cat_slug.lower(), "professional/practical/growth-oriented")

    has_rich_voice = bool(register or code_mix or vocab_allowed or taboos)
    if has_rich_voice:
        policy_parts = [f"Tone: {tone}"]
        if register:
            policy_parts.append(f"Register: {register}")
        if code_mix:
            policy_parts.append(f"Language mix: {code_mix}")
        if vocab_allowed:
            policy_parts.append(f"Allowed terms: {', '.join(vocab_allowed[:8])}")
        if taboos:
            policy_parts.append(f"Taboos: {', '.join(taboos[:8])}")
        summary_str = "; ".join(policy_parts)
    else:
        summary_str = tone

    return {
        "tone": tone,
        "register": register,
        "code_mix": code_mix,
        "vocab_allowed": vocab_allowed,
        "taboos": taboos,
        "summary": summary_str,
    }

SYSTEM_PROMPT = """You are Vera, a high-converting merchant growth assistant on magicpin. Write ONE concise WhatsApp-style message.

RULES:
1. GROUNDING & HONESTY: Use ONLY facts, metrics, citations, and active offers explicitly provided in the context. Never fabricate data, prices, dates, customer names, or claims.
2. PERSONALIZATION: Address the merchant/owner directly by name (use 'Dr. [FirstName]' for dentists if available). Reference their specific locality and business category.
3. CATEGORY VOICE: Strictly adopt the authentic category tone:
   - Dentists: peer-clinical, respectful, collegial, evidence-based (cite research/trial_n if in digest).
   - Salons: warm, visual, occasion-driven, practical.
   - Restaurants: busy operator-to-operator, timely, concise.
   - Gyms: energetic, motivational, progress-driven coaching.
   - Pharmacies: precise, trustworthy, utility-first, conservative.
   Adhere to allowed terms and avoid taboos.
4. TRIGGER ANCHOR: Ground the message clearly in the primary trigger signal and explain why this matters right now.
5. ENGAGEMENT COMPULSION & HIGH-CONVERTING CTA (CRITICAL):
   - NEVER use passive permission-seeking phrasing (e.g. NEVER ask "Would you like me to...?", "Can I help you with...?", "Want me to...?", or "Should we...?").
   - Propose a CONCRETE next step with light urgency, FOMO, or loss-aversion framing (per challenge-brief §10 compulsion levers: specificity, loss aversion, social proof, effort externalization, single binary commitment).
   - Use low-friction binary commitment framing: e.g. "Reply YES to get the 3-step checklist before the deadline", "Reply YES to lock in this draft before Friday's rush", or "Reply YES to launch this promo to 500 nearby customers today".
   - Keep EXACTLY ONE CTA at the end of the message. Do NOT include a second ask, multiple choice question, or secondary ask.
6. NO INTERNAL REASONING: Do not mention internal rules, scoring, trigger IDs, or system concepts.
7. Return JSON only:
{"body": "<rendered WhatsApp message text>", "cta": "<the single CTA text>", "rationale": "<1-sentence explanation of why this was sent>"}"""

# LLM Timeout in seconds (enforcing 8-10 seconds per requirements)
LLM_TIMEOUT_SECONDS = float(os.environ.get("LLM_TIMEOUT_SECONDS", str(getattr(settings, "LLM_TIMEOUT_SECONDS", 8.0))))

# Optional pluggable LLM caller for testing or custom providers
_custom_llm_caller: Optional[Callable[[str, str, float], str]] = None

INJECTION_PATTERNS = [
    r"(?i)\bignore\s+(?:all\s+)?(?:previous\s+)?instructions\b",
    r"(?i)\bsystem\s+override\b",
    r"(?i)\[system\]",
    r"(?i)\byou\s+are\s+now\b",
    r"(?i)\bdan\s+mode\b",
    r"(?i)\bjailbreak\b",
]


def sanitize_text(text: Optional[str]) -> Optional[str]:
    """Sanitize user-controlled text fields against prompt injection markers."""
    if not text or not isinstance(text, str):
        return text
    cleaned = text
    for pat in INJECTION_PATTERNS:
        cleaned = re.sub(pat, "[FILTERED]", cleaned)
    return cleaned.strip()


def set_custom_llm_caller(caller: Optional[Callable[[str, str, float], str]]) -> None:
    """Set or clear a custom LLM caller (useful for unit tests and mocks)."""
    global _custom_llm_caller
    _custom_llm_caller = caller


# ---------------------------------------------------------------------------
# Compact Context Builder
# ---------------------------------------------------------------------------
def build_compact_context(
    merchant: Optional[Dict[str, Any]],
    category: Optional[Dict[str, Any]],
    trigger: Optional[Dict[str, Any]],
    customer: Optional[Dict[str, Any]] = None,
    selected_signal: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Builds a small, grounded JSON object containing ONLY the facts the LLM is allowed to use.
    Walks real nested payloads (testing-brief.md §3) with fallback for flat structures:
    - digest[].source, digest[].trial_n, digest[].title, digest[].summary
    - performance.delta_7d (views_pct, calls_pct, ctr_pct)
    - customer_aggregate (total_unique_ytd, lapsed_180d_plus, retention_6mo_pct, high_risk_adult_count)
    - signals[] for framing strengths/weaknesses
    - category.voice (tone, register, vocab_allowed, taboos)
    - offers[].title where status == 'active'
    """
    m_raw = merchant or {}
    c_raw = category or {}
    t_raw = trigger or {}
    cust_raw = customer or {}

    # Automatically unwrap ContextStore envelope if passed directly
    m = m_raw.get("payload") if (isinstance(m_raw, dict) and "payload" in m_raw and isinstance(m_raw["payload"], dict)) else m_raw
    c = c_raw.get("payload") if (isinstance(c_raw, dict) and "payload" in c_raw and isinstance(c_raw["payload"], dict)) else c_raw
    t = t_raw.get("payload") if (isinstance(t_raw, dict) and "payload" in t_raw and isinstance(t_raw["payload"], dict) and "kind" not in t_raw) else t_raw
    cust = cust_raw.get("payload") if (isinstance(cust_raw, dict) and "payload" in cust_raw and isinstance(cust_raw["payload"], dict)) else cust_raw

    # 1. Merchant Identity
    m_identity = m.get("identity") if isinstance(m.get("identity"), dict) else {}
    m_name = sanitize_text(m_identity.get("name") or m.get("name", "Merchant Partner"))
    m_owner = sanitize_text(m_identity.get("owner_first_name") or m.get("owner"))
    m_city = sanitize_text(m_identity.get("city") or m.get("city"))
    m_locality = sanitize_text(m_identity.get("locality") or m.get("locality"))

    # 2. Performance Metrics
    perf = m.get("performance") if isinstance(m.get("performance"), dict) else {}
    m_ctr = perf.get("ctr") if perf.get("ctr") is not None else m.get("ctr")
    m_views = perf.get("views") if perf.get("views") is not None else m.get("views")
    m_calls = perf.get("calls") if perf.get("calls") is not None else m.get("calls")
    m_directions = perf.get("directions") if perf.get("directions") is not None else m.get("directions")
    m_leads = perf.get("leads") if perf.get("leads") is not None else m.get("leads")

    delta_7d = perf.get("delta_7d") if isinstance(perf.get("delta_7d"), dict) else {}

    # 3. Customer Aggregate & Signals
    cust_agg = m.get("customer_aggregate") if isinstance(m.get("customer_aggregate"), dict) else {}
    signals = m.get("signals") if isinstance(m.get("signals"), list) else []

    # 4. Active Offers
    active_offers: List[str] = []
    raw_offers = m.get("offers", [])
    if isinstance(raw_offers, list):
        for o in raw_offers:
            if isinstance(o, dict):
                if o.get("status") == "active" and o.get("title"):
                    active_offers.append(o["title"].strip())
            elif isinstance(o, str) and o.strip():
                active_offers.append(o.strip())
    # Backwards compatibility with active_offers key if passed directly
    if isinstance(m.get("active_offers"), list):
        for ao in m["active_offers"]:
            if isinstance(ao, str) and ao.strip() and ao.strip() not in active_offers:
                active_offers.append(ao.strip())

    # 5. Category Context & Dynamic Voice
    cat_slug = c.get("slug") or m.get("category_slug", "general")
    cat_name = c.get("display_name", cat_slug)
    peer_stats = c.get("peer_stats") if isinstance(c.get("peer_stats"), dict) else {}
    peer_ctr = peer_stats.get("avg_ctr") if peer_stats.get("avg_ctr") is not None else c.get("peer_avg_ctr")

    voice_info = resolve_voice_policy(c, cat_slug)
    voice_policy_str = voice_info["summary"]

    # 6. Trigger Context & Digest Matching
    t_kind = t.get("kind", t.get("type", "notification"))
    t_urgency = t.get("urgency", 1)
    t_payload = t.get("payload") if isinstance(t.get("payload"), dict) else {}

    # Match relevant digest item for research / compliance / educational triggers
    matched_digest: Optional[Dict[str, Any]] = None
    digests = c.get("digest") if isinstance(c.get("digest"), list) else []
    top_item_id = t_payload.get("top_item_id") or t_payload.get("top_item") or t_payload.get("digest_id")

    if top_item_id:
        for item in digests:
            if isinstance(item, dict) and item.get("id") == top_item_id:
                matched_digest = item
                break

    if not matched_digest and t_kind in (
        "research_digest",
        "category_research_digest_release",
        "compliance_alert",
        "regulation_change",
    ):
        for item in digests:
            if isinstance(item, dict):
                matched_digest = item
                break

    digest_dict: Optional[Dict[str, Any]] = None
    if matched_digest:
        digest_dict = {
            "id": matched_digest.get("id"),
            "kind": matched_digest.get("kind"),
            "title": matched_digest.get("title"),
            "source": matched_digest.get("source"),
            "trial_n": matched_digest.get("trial_n"),
            "patient_segment": matched_digest.get("patient_segment"),
            "summary": matched_digest.get("summary"),
            "actionable": matched_digest.get("actionable"),
        }

    # 7. Customer Context (if scope=customer)
    cust_identity = cust.get("identity") if isinstance(cust.get("identity"), dict) else {}
    cust_name = sanitize_text(cust_identity.get("name") or cust.get("name")) if cust else None
    cust_lang = cust_identity.get("language_pref") if cust else None
    cust_rel = cust.get("relationship") if isinstance(cust.get("relationship"), dict) else {}
    cust_state = cust.get("state") if cust else None
    cust_pref = cust.get("preferences") if isinstance(cust.get("preferences"), dict) else {}

    # 8. Construct Allowed Facts List
    allowed_facts = [
        f"Merchant name: {m_name}",
        f"Category: {cat_name}",
    ]
    if m_owner:
        allowed_facts.append(f"Owner first name: {m_owner}")
    if m_city:
        allowed_facts.append(f"City: {m_city}")
    if m_locality:
        allowed_facts.append(f"Locality: {m_locality}")
    if m_ctr is not None:
        allowed_facts.append(f"Merchant 30d CTR: {m_ctr * 100:.1f}%")
    if m_views is not None:
        allowed_facts.append(f"Merchant 30d views: {m_views}")
    if m_calls is not None:
        allowed_facts.append(f"Merchant 30d calls: {m_calls}")
    if peer_ctr is not None:
        allowed_facts.append(f"Category peer avg CTR: {peer_ctr * 100:.1f}%")

    # 7-day performance deltas
    views_pct = delta_7d.get("views_pct")
    calls_pct = delta_7d.get("calls_pct")
    ctr_pct = delta_7d.get("ctr_pct")
    if views_pct is not None:
        allowed_facts.append(f"7d views trend: {views_pct * 100:+.1f}%")
    if calls_pct is not None:
        allowed_facts.append(f"7d calls trend: {calls_pct * 100:+.1f}%")
    if ctr_pct is not None:
        allowed_facts.append(f"7d CTR trend: {ctr_pct * 100:+.1f}%")

    # Customer aggregates
    high_risk_adults = cust_agg.get("high_risk_adult_count")
    total_ytd = cust_agg.get("total_unique_ytd")
    lapsed_180d = cust_agg.get("lapsed_180d_plus")
    retention_6mo = cust_agg.get("retention_6mo_pct")
    if high_risk_adults is not None:
        allowed_facts.append(f"High-risk adult patient cohort: {high_risk_adults} patients")
    if total_ytd is not None:
        allowed_facts.append(f"Total unique customers YTD: {total_ytd}")
    if lapsed_180d is not None:
        allowed_facts.append(f"Lapsed customers (180d+): {lapsed_180d}")
    if retention_6mo is not None:
        allowed_facts.append(f"6-month customer retention: {retention_6mo * 100:.1f}%")

    # Signals
    if signals:
        allowed_facts.append(f"Active merchant signals: {', '.join(signals)}")

    # Active offers
    if active_offers:
        allowed_facts.append(f"Active offers: {', '.join(active_offers)}")

    # Digest citation details
    if digest_dict:
        if digest_dict.get("title"):
            allowed_facts.append(f"Digest topic: {digest_dict['title']}")
        if digest_dict.get("source"):
            allowed_facts.append(f"Digest source citation: {digest_dict['source']}")
        if digest_dict.get("trial_n"):
            tn = digest_dict["trial_n"]
            allowed_facts.append(f"Clinical trial sample: {tn:,} patients ({tn})")
        if digest_dict.get("patient_segment"):
            allowed_facts.append(f"Relevant patient segment: {digest_dict['patient_segment']}")
        if digest_dict.get("summary"):
            allowed_facts.append(f"Research summary: {digest_dict['summary']}")
        if digest_dict.get("actionable"):
            allowed_facts.append(f"Actionable takeaway: {digest_dict['actionable']}")

    # Customer-facing details
    if cust_name:
        allowed_facts.append(f"Customer name: {cust_name}")
    if cust_lang:
        allowed_facts.append(f"Customer language preference: {cust_lang}")
    if cust_state:
        allowed_facts.append(f"Customer status: {cust_state}")
    if cust_rel.get("last_visit"):
        allowed_facts.append(f"Customer last visit: {cust_rel['last_visit']}")
    if cust_rel.get("visits_total"):
        allowed_facts.append(f"Customer total visits: {cust_rel['visits_total']}")
    if cust_rel.get("services_received"):
        allowed_facts.append(f"Services received: {', '.join(cust_rel['services_received'])}")
    if cust_pref.get("preferred_slots"):
        allowed_facts.append(f"Preferred slots: {cust_pref['preferred_slots']}")

    # Trigger payload specific facts
    if t_kind:
        allowed_facts.append(f"Primary trigger kind: {t_kind}")
    if selected_signal:
        allowed_facts.append(f"Selected signal: {selected_signal}")
    if "service_due" in t_payload:
        allowed_facts.append(f"Service due: {t_payload['service_due']}")
    if "festival" in t_payload:
        allowed_facts.append(f"Upcoming festival: {t_payload['festival']} (in {t_payload.get('days_until', 'few')} days)")
    if "top_item_id" in t_payload and not digest_dict:
        allowed_facts.append(f"Research topic anchor: {t_payload['top_item_id']}")
    if "delta_pct" in t_payload:
        dp = t_payload["delta_pct"]
        dp_pct = dp * 100 if abs(dp) < 1 else dp
        allowed_facts.append(f"Performance delta: {dp_pct:+.1f}%")

    forbidden_claims = [
        "Do not invent discounts, prices, or free services not in active_offers.",
        "Do not fabricate customer names, dates, or quantitative claims not in allowed_facts.",
        "Do not make exaggerated or guaranteed health, clinical, or revenue claims.",
        "Do not promise specific view counts or sales increases.",
        "Do not mention internal system concepts, trigger IDs, or scoring rules.",
    ]

    return {
        "merchant": {
            "name": m_name,
            "owner": m_owner,
            "city": m_city,
            "locality": m_locality,
            "performance": {
                "ctr": m_ctr,
                "views": m_views,
                "calls": m_calls,
                "directions": m_directions,
                "leads": m_leads,
                "delta_7d": delta_7d,
            },
            "customer_aggregate": cust_agg,
            "signals": signals,
            "active_offers": active_offers,
        },
        "category": {
            "slug": cat_slug,
            "name": cat_name,
            "peer_stats": peer_stats,
            "peer_avg_ctr": peer_ctr,
            "voice": voice_info,
            "voice_policy": voice_policy_str,
        },
        "digest": digest_dict,
        "trigger": {
            "id": t.get("id"),
            "kind": t_kind,
            "urgency": t_urgency,
            "details": t_payload,
        },
        "customer": {
            "name": cust_name,
            "language_pref": cust_lang,
            "state": cust_state,
            "relationship": cust_rel,
            "preferences": cust_pref,
        } if cust_name else None,
        "selected_signal": selected_signal or t_kind,
        "category_voice_policy": voice_policy_str,
        "allowed_facts": allowed_facts,
        "forbidden_claims": forbidden_claims,
    }


# ---------------------------------------------------------------------------
# LLM Provider Call
# ---------------------------------------------------------------------------
def _default_llm_call(prompt: str, system: str, timeout: float = LLM_TIMEOUT_SECONDS) -> str:
    """
    Default HTTP client calling Gemini or OpenAI chat completions if API key configured.
    Falls back gracefully if no API key is present or during unit test runner.
    """
    # Prevent accidental external network calls and latency spikes during automated pytest execution
    if os.environ.get("PYTEST_CURRENT_TEST") and not os.environ.get("ENABLE_LIVE_LLM_TESTS"):
        raise ValueError("Live LLM calls disabled during pytest execution.")

    gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    llm_key = os.environ.get("LLM_API_KEY", "").strip()

    # Prefer Gemini if GEMINI_API_KEY is configured
    if gemini_key:
        model = os.environ.get("LLM_MODEL", "gemini-3.5-flash-lite")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={gemini_key}"
        payload = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
                "responseMimeType": "application/json",
            },
        }
        req = urlrequest.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlrequest.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["candidates"][0]["content"]["parts"][0]["text"]

    # Otherwise fallback to OpenAI-compatible provider if key configured
    api_key = openai_key or llm_key
    if not api_key:
        raise ValueError("No LLM API key configured.")

    base_url = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1/chat/completions")
    model = os.environ.get("LLM_MODEL", settings.MODEL or "gpt-4o")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    req = urlrequest.Request(
        base_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urlrequest.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"]


# ---------------------------------------------------------------------------
# Message Composer with Fallback
# ---------------------------------------------------------------------------
def compose_message(
    compact_context: Dict[str, Any],
    fallback_data: Tuple[str, str, List[str], str],
    conversation_store: Optional[Any] = None,
    conversation_id: Optional[str] = None,
) -> Tuple[str, str, List[str], str]:
    """
    Attempts LLM message composition using compact_context.
    Enforces 8s timeout and strict evidence ledger validation.
    On timeout, malformed JSON, ungrounded hallucination, or any error:
    immediately returns fallback_data.

    Returns: (body, template_name, template_params, rationale)
    """
    fallback_body, fallback_template, fallback_params, fallback_rationale = fallback_data

    user_prompt = f"Grounded Context:\n{json.dumps(compact_context, indent=2)}\n\nCompose the WhatsApp message."

    try:
        if _custom_llm_caller is not None:
            raw_response = _custom_llm_caller(user_prompt, SYSTEM_PROMPT, LLM_TIMEOUT_SECONDS)
        else:
            raw_response = _default_llm_call(user_prompt, SYSTEM_PROMPT, LLM_TIMEOUT_SECONDS)

        # Parse JSON
        if not raw_response or not isinstance(raw_response, str):
            logger.warning("Empty or non-string LLM response; using deterministic fallback.")
            return fallback_data

        match = re.search(r"\{[\s\S]*\}", raw_response)
        if not match:
            logger.warning("No JSON object found in LLM response; using deterministic fallback.")
            return fallback_data

        data = json.loads(match.group())
        body = data.get("body")
        if not body or not isinstance(body, str) or not body.strip():
            logger.warning("LLM response missing 'body'; using deterministic fallback.")
            return fallback_data

        body = body.strip()
        cta_val = data.get("cta")
        from app.output_validator import count_ctas
        if cta_val and isinstance(cta_val, str) and cta_val.strip():
            cta_clean = cta_val.strip()
            if count_ctas(body) == 0 and cta_clean.lower() not in body.lower():
                body = f"{body} {cta_clean}"

        # Grounding validation against evidence ledger
        ledger = build_evidence_ledger(compact_context)
        is_valid, rejection_reason = validate_message(
            body=body.strip(),
            ledger=ledger,
            conversation_store=conversation_store,
            conversation_id=conversation_id,
        )
        if not is_valid:
            logger.warning(
                f"LLM output rejected by grounding validator: {rejection_reason}. Falling back to deterministic template."
            )
            return fallback_data

        cta = data.get("cta", "open_ended")
        rationale = data.get("rationale") or f"LLM grounded composition based on {compact_context.get('selected_signal')}."

        return (
            body.strip(),
            "llm_grounded_composer",
            [cta],
            rationale,
        )

    except TimeoutError:
        logger.warning("LLM call timed out; using deterministic fallback.")
        return fallback_data
    except Exception as exc:
        logger.warning(f"LLM composition failed ({exc}); using deterministic fallback.")
        return fallback_data
