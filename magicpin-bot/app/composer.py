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

SYSTEM_PROMPT = """You are Vera, a merchant growth assistant. Write ONE concise WhatsApp-style message.
Rules:
1. Use ONLY facts present in the supplied context.
2. Never invent prices, metrics, dates, offers, customers, or claims.
3. Mention the strongest selected signal.
4. Make the message specific to this merchant.
5. Match the category voice.
6. Give exactly ONE CTA.
7. Keep the ask easy to answer.
8. Do not mention internal reasoning.
9. Do not repeat previous messages.
Return JSON: {"body": "...", "cta": "...", "rationale": "..."}"""

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
    Builds a small JSON object containing ONLY the facts the LLM is allowed to use,
    plus explicit 'allowed_facts' and 'forbidden_claims' lists.
    """
    m = merchant or {}
    c = category or {}
    t = trigger or {}
    cust = customer or {}

    m_identity = m.get("identity", {})
    m_name = sanitize_text(m_identity.get("name") or m.get("name", "Merchant Partner"))
    m_owner = sanitize_text(m_identity.get("owner_first_name"))
    m_city = sanitize_text(m_identity.get("city"))
    m_locality = sanitize_text(m_identity.get("locality"))

    perf = m.get("performance", {})
    m_ctr = perf.get("ctr")
    m_views = perf.get("views")
    m_calls = perf.get("calls")

    active_offers = [
        o.get("title") for o in m.get("offers", []) if o.get("status") == "active"
    ]

    cat_slug = c.get("slug") or m.get("category_slug", "general")
    cat_name = c.get("display_name", cat_slug)
    peer_stats = c.get("peer_stats", {})
    peer_ctr = peer_stats.get("avg_ctr")

    voice_policy = CATEGORY_POLICIES.get(
        cat_slug.lower(),
        "professional/practical/growth-oriented",
    )

    t_kind = t.get("kind", t.get("type", "notification"))
    t_urgency = t.get("urgency", 1)
    t_payload = t.get("payload", {})

    cust_name = cust.get("identity", {}).get("name") if cust else None

    # Construct allowed facts list
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
    if peer_ctr is not None:
        allowed_facts.append(f"Category peer avg CTR: {peer_ctr * 100:.1f}%")
    if active_offers:
        allowed_facts.append(f"Active offers: {', '.join(active_offers)}")
    if t_kind:
        allowed_facts.append(f"Primary trigger kind: {t_kind}")
    if selected_signal:
        allowed_facts.append(f"Selected signal: {selected_signal}")
    if cust_name:
        allowed_facts.append(f"Customer name: {cust_name}")

    # Specific payload details if available
    if "service_due" in t_payload:
        allowed_facts.append(f"Service due: {t_payload['service_due']}")
    if "festival" in t_payload:
        allowed_facts.append(f"Upcoming festival: {t_payload['festival']} (in {t_payload.get('days_until', 'few')} days)")
    if "top_item_id" in t_payload:
        allowed_facts.append(f"Research topic anchor: {t_payload['top_item_id']}")

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
            "performance": {"ctr": m_ctr, "views": m_views, "calls": m_calls},
            "active_offers": active_offers,
        },
        "category": {
            "slug": cat_slug,
            "name": cat_name,
            "peer_avg_ctr": peer_ctr,
            "voice_policy": voice_policy,
        },
        "trigger": {
            "kind": t_kind,
            "urgency": t_urgency,
            "details": t_payload,
        },
        "customer": {"name": cust_name} if cust_name else None,
        "selected_signal": selected_signal or t_kind,
        "category_voice_policy": voice_policy,
        "allowed_facts": allowed_facts,
        "forbidden_claims": forbidden_claims,
    }


# ---------------------------------------------------------------------------
# LLM Provider Call
# ---------------------------------------------------------------------------
def _default_llm_call(prompt: str, system: str, timeout: float = LLM_TIMEOUT_SECONDS) -> str:
    """
    Default HTTP client calling OpenAI-compatible chat completions if API key configured.
    Falls back gracefully if no API key is present.
    """
    gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    llm_key = os.environ.get("LLM_API_KEY", "").strip()

    # Valid Gemini API keys from Google AI Studio start with AIza
    valid_gemini_key = gemini_key if (gemini_key.startswith("AIza") or os.environ.get("LLM_PROVIDER") == "gemini") else None

    api_key = valid_gemini_key or openai_key or llm_key
    if not api_key:
        raise ValueError("No LLM API key configured.")

    is_gemini = bool(valid_gemini_key) or os.environ.get("LLM_PROVIDER") == "gemini"
    default_base_url = (
        "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
        if is_gemini
        else "https://api.openai.com/v1/chat/completions"
    )
    default_model = "gemini-1.5-flash" if is_gemini else (settings.MODEL or "gpt-4o")

    base_url = os.environ.get("LLM_BASE_URL", default_base_url)
    model = os.environ.get("LLM_MODEL", default_model)

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
