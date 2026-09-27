"""Conversation state machine and context-aware transition engine for Magicpin Vera bot.

Handles deterministic intent classification, conversation context resolution
(merchant, customer, trigger, previous bot message/action, previous turns, current state),
context-grounded positive campaign continuation, grounded question answering,
auto-reply loop detection, and state transitions for POST /v1/reply.
"""

import logging
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

from app.models import ReplyRequest, ReplyResponse, ResolvedContext
from app.decision.templates import _clean_entity_text

logger = logging.getLogger("magicpin-bot")


class ConversationState(str, Enum):
    NEW = "NEW"
    INITIAL_MESSAGE = "INITIAL_MESSAGE"
    SEND = "SEND"
    WAIT = "WAIT"
    END = "END"
    ANSWER = "ANSWER"
    REDIRECT = "REDIRECT"


class Intent(str, Enum):
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    DELAY = "DELAY"
    HOSTILE = "HOSTILE"
    OFF_TOPIC = "OFF_TOPIC"
    QUESTION = "QUESTION"


TERMINAL_STATES: Set[ConversationState] = {ConversationState.END}
ACTIVE_STATES: Set[ConversationState] = {s for s in ConversationState if s not in TERMINAL_STATES}
INITIAL_STATES: Set[ConversationState] = {ConversationState.NEW, ConversationState.INITIAL_MESSAGE}


def normalize_state(val: Optional[Any]) -> ConversationState:
    """Deterministically normalizes raw state strings and aliases to canonical ConversationState."""
    if not val:
        return ConversationState.NEW
    if isinstance(val, ConversationState):
        return val
    raw = val.value if hasattr(val, "value") else val
    val_str = str(raw).strip()
    val_upper = val_str.upper()
    if val_upper in ("WAITING_FOR_REPLY", "INITIAL_MESSAGE"):
        return ConversationState.INITIAL_MESSAGE
    for s in ConversationState:
        if s.value == val_str or s.name == val_upper:
            return s
    return ConversationState.NEW


@dataclass(frozen=True)
class TransitionRule:
    """Formal specification of a deterministic state transition rule."""
    from_state: ConversationState
    intent: Intent
    to_state: ConversationState
    allowed: bool
    action: str
    rationale_template: str


# ---------------------------------------------------------------------------
# Formal Deterministic Transition Matrix (§4.1 / §4.2)
# ---------------------------------------------------------------------------

TRANSITION_MATRIX: Dict[Tuple[ConversationState, Intent], TransitionRule] = {}


def _register_transition_rule(
    from_state: ConversationState,
    intent: Intent,
    to_state: ConversationState,
    allowed: bool,
    action: str,
    rationale_template: str,
) -> None:
    TRANSITION_MATRIX[(from_state, intent)] = TransitionRule(
        from_state=from_state,
        intent=intent,
        to_state=to_state,
        allowed=allowed,
        action=action,
        rationale_template=rationale_template,
    )


# 1. Register transitions from all ACTIVE_STATES
for _st in [
    ConversationState.NEW,
    ConversationState.INITIAL_MESSAGE,
    ConversationState.ANSWER,
    ConversationState.SEND,
    ConversationState.WAIT,
    ConversationState.REDIRECT,
]:
    _register_transition_rule(
        _st, Intent.POSITIVE, ConversationState.SEND, True, "send",
        "Merchant committed positively; continuing campaign and executing action mode without further qualification."
    )
    _register_transition_rule(
        _st, Intent.QUESTION, ConversationState.ANSWER, True, "send",
        "Answering merchant question from grounded conversation context."
    )
    _register_transition_rule(
        _st, Intent.DELAY, ConversationState.WAIT, True, "wait",
        "Merchant requested delay; backing off."
    )
    _register_transition_rule(
        _st, Intent.OFF_TOPIC, ConversationState.REDIRECT, True, "send",
        "Query was off-topic; gently redirecting to merchant growth capabilities."
    )
    _register_transition_rule(
        _st, Intent.NEGATIVE, ConversationState.END, True, "end",
        "Merchant expressed negative intent; gracefully ending conversation."
    )
    _register_transition_rule(
        _st, Intent.HOSTILE, ConversationState.END, True, "end",
        "Hostile message detected; terminating conversation immediately."
    )

# 2. Register transitions from TERMINAL_STATES (ConversationState.END)
# Only POSITIVE is allowed to re-open; all other intents are rejected as impossible transitions.
_register_transition_rule(
    ConversationState.END, Intent.POSITIVE, ConversationState.SEND, True, "send",
    "Reopened ended conversation upon explicit positive merchant opt-in."
)
_register_transition_rule(
    ConversationState.END, Intent.QUESTION, ConversationState.END, False, "end",
    "Rejected impossible transition: conversation is in terminal state END."
)
_register_transition_rule(
    ConversationState.END, Intent.DELAY, ConversationState.END, False, "end",
    "Rejected impossible transition: conversation is in terminal state END."
)
_register_transition_rule(
    ConversationState.END, Intent.OFF_TOPIC, ConversationState.END, False, "end",
    "Rejected impossible transition: conversation is in terminal state END."
)
_register_transition_rule(
    ConversationState.END, Intent.NEGATIVE, ConversationState.END, False, "end",
    "Rejected impossible transition: conversation is in terminal state END."
)
_register_transition_rule(
    ConversationState.END, Intent.HOSTILE, ConversationState.END, False, "end",
    "Rejected impossible transition: conversation is in terminal state END."
)


# ---------------------------------------------------------------------------
# Rule-Based Intent Classifier Patterns
# ---------------------------------------------------------------------------

HOSTILE_PATTERNS = [
    r"leave me alone",
    r"stop messaging",
    r"don'?t contact me",
    r"do not contact me",
    r"stop texting",
    r"useless spam",
    r"report spam",
    r"\bspam\b",
    r"unsubscribe",
    r"get lost",
    r"\bfuck\b",
    r"\bf\*\*\*\b",
    r"harassment",
    r"harassing",
    # Hindi / Hinglish hostile patterns
    r"pareshan mat karo",
    r"dimag mat khao",
    r"bakwas mat karo",
    r"परेशान मत करो",
    r"दिमाग मत खाओ",
    r"बकवास बंद करो",
]

DELAY_PATTERNS = [
    r"\blater\b",
    r"call me tomorrow",
    r"call tomorrow",
    r"not now",
    r"give me some time",
    r"busy right now",
    r"\bbusy\b",
    r"in a meeting",
    r"next week",
    r"ping me later",
    r"remind me later",
    r"after some time",
    r"\btomorrow\b",
    r"can'?t talk",
    r"some other time",
    # Hindi / Hinglish / Emoji delay patterns
    r"\bkal\b",
    r"kal baat karte",
    r"baad mein",
    r"baad me",
    r"abhi busy",
    r"abhi time nahi",
    r"बाद में",
    r"कल बात",
    r"व्यस्त हूँ",
    r"समय नहीं",
    r"⏰",
    r"⏳",
]

NEGATIVE_PATTERNS = [
    r"\bno thanks\b",
    r"\bnot interested\b",
    r"don'?t need this",
    r"dont need this",
    r"do not need",
    r"not required",
    r"\bno\b",
    r"\bnope\b",
    r"\bnah\b",
    r"\bstop\b",
    r"\bnever\b",
    r"\bcancel\b",
    r"\bdiscontinue\b",
    # Hindi / Hinglish / Emoji negative patterns
    r"\bnahi\b",
    r"\bnahin\b",
    r"nahi chahiye",
    r"mat bhejo",
    r"band karo",
    r"जरूरत नहीं",
    r"नहीं चाहिए",
    r"मत भेजो",
    r"बंद करो",
    r"\bनहीं\b",
    r"❌",
    r"👎",
    r"🚫",
]

POSITIVE_PATTERNS = [
    r"\byes\b",
    r"\bsure\b",
    r"\bokay\b",
    r"\bok\b",
    r"\bdo it\b",
    r"let'?s do it",
    r"lets do it",
    r"ok lets do it",
    r"ok let's do it",
    r"send it",
    r"go ahead",
    r"\byep\b",
    r"\byeah\b",
    r"\bproceed\b",
    r"\bshare it\b",
    r"\binterested\b",
    r"please do",
    r"sounds good",
    r"what'?s next",
    r"whats next",
    r"i want to join",
    r"ready to (?:start|proceed|launch|go)",
    r"count me in",
    # Hindi / Hinglish / Emoji positive patterns
    r"\bhaan\b",
    r"\bhaanji\b",
    r"\bhaaji\b",
    r"bhejo",
    r"bhej do",
    r"kar do",
    r"chalega",
    r"theek hai",
    r"sahi hai",
    r"हाँ",
    r"हाँजी",
    r"भेज दो",
    r"ज़रूर",
    r"बिल्कुल",
    r"ठीक है",
    r"👍",
    r"👌",
    r"✅",
    r"🙏",
]

OFF_TOPIC_PATTERNS = [
    r"\bweather\b",
    r"who is pm\b",
    r"prime minister",
    r"tell me a joke",
    r"sing a song",
    r"cricket score",
    r"what is 2\s*\+\s*2",
    r"how are you",
    r"who made you",
    r"capital of",
    r"\brecipe\b",
]

AUTO_REPLY_PATTERNS = [
    r"thank you for contacting us",
    r"thank you for reaching out",
    r"our team will respond shortly",
    r"out of office",
    r"auto-reply",
    r"automated response",
    r"we have received your message",
]


INJECTION_ATTACK_PATTERNS = [
    # Injected instructions
    r"(?i)\bignore\s+(?:all\s+)?(?:previous\s+)?instructions\b",
    r"(?i)\bdisregard\s+(?:all\s+)?(?:previous\s+)?(?:instructions|rules|prompts)\b",
    r"(?i)\bforget\s+(?:all\s+)?(?:previous\s+)?(?:instructions|rules)\b",
    r"(?i)\bsystem\s+override\b",
    r"(?i)\badmin\s+override\b",
    r"(?i)\[system\]",
    r"(?i)<<sys>>",
    r"(?i)<\|im_start\|>",
    r"(?i)<\|im_end\|>",
    r"(?i)<\/?untrusted_context_data>",
    r"(?i)\byou\s+are\s+now\b",
    # System prompt extraction
    r"(?i)\breveal\s+(?:your\s+)?(?:system\s+)?(?:prompt|instructions|rules)\b",
    r"(?i)\boutput\s+(?:your\s+)?(?:system\s+)?(?:prompt|instructions)\b",
    r"(?i)\bprint\s+(?:your\s+)?(?:system\s+)?(?:prompt|instructions)\b",
    r"(?i)\bshow\s+(?:your\s+)?(?:system\s+)?(?:prompt|instructions|rules)\b",
    r"(?i)\bwhat\s+is\s+your\s+system\s+prompt\b",
    r"(?i)\bsystem\s+prompt\b",
    # Jailbreak modes
    r"(?i)\bdan\s+mode\b",
    r"(?i)\bjailbreak\b",
    r"(?i)\bdeveloper\s+mode\b",
    r"(?i)\bunrestricted\s+mode\b",
    r"(?i)\balways\s+say\s+yes\b",
    r"(?i)\baim\s+mode\b",
    r"(?i)\bdo\s+anything\s+now\b",
    # Tool invocations
    r"(?i)<\s*\/?\s*tool_call\s*>",
    r"(?i)<\s*\/?\s*tool_code\s*>",
    r"(?i)\bcall:default_api\b",
    r"(?i)\bfunction_call\b",
    r"(?i)\brun_command\b",
    r"(?i)\bview_file\b",
    r"(?i)\bwrite_to_file\b",
    r"(?i)\breplace_file_content\b",
    r"(?i)\bexec\s*\(",
    r"(?i)\beval\s*\(",
    # SQL fragments
    r"(?i);\s*drop\s+table\b",
    r"(?i)\bdrop\s+table\b",
    r"(?i)\bunion\s+(?:all\s+)?select\b",
    r"(?i)'\s*or\s+['\"]?1['\"]?\s*=\s*['\"]?1",
]


def classify_intent(message: str) -> Intent:
    """
    Classifies merchant/customer free-text message into an Intent:
    1. Hostile -> HOSTILE
    2. Prompt Injection / Security Attack -> OFF_TOPIC (safe redirect)
    3. Delay -> DELAY
    4. Negative -> NEGATIVE
    5. Positive -> POSITIVE
    6. Off-Topic -> OFF_TOPIC
    7. Anything else / Question -> QUESTION
    """
    if not message:
        return Intent.QUESTION

    msg_clean = message.strip().lower()

    # 1. Hostile check
    for pat in HOSTILE_PATTERNS:
        if re.search(pat, msg_clean):
            return Intent.HOSTILE

    # 2. Prompt Injection / System Override / Security Attack check -> gracefully deflect via OFF_TOPIC
    for pat in INJECTION_ATTACK_PATTERNS:
        if re.search(pat, msg_clean):
            return Intent.OFF_TOPIC

    # 3. Delay check
    for pat in DELAY_PATTERNS:
        if re.search(pat, msg_clean):
            return Intent.DELAY

    # 4. Negative check
    for pat in NEGATIVE_PATTERNS:
        if re.search(pat, msg_clean):
            return Intent.NEGATIVE

    # 5. Off-topic check
    for pat in OFF_TOPIC_PATTERNS:
        if re.search(pat, msg_clean):
            return Intent.OFF_TOPIC

    # 6. Positive check
    for pat in POSITIVE_PATTERNS:
        if re.search(pat, msg_clean):
            return Intent.POSITIVE

    # 7. Fallback / Question check
    return Intent.QUESTION


def is_auto_reply_text(message: str) -> bool:
    """Detects standard canned auto-reply messages."""
    msg_clean = message.strip().lower()
    for pat in AUTO_REPLY_PATTERNS:
        if re.search(pat, msg_clean):
            return True
    return False


# ---------------------------------------------------------------------------
# Context Resolution & Grounded Generators
# ---------------------------------------------------------------------------

def resolve_conversation_context(
    conversation_id: str,
    request: ReplyRequest,
    conversation_store: Any,
    context_store: Optional[Any] = None,
    resolved_context: Optional[ResolvedContext] = None,
) -> Dict[str, Any]:
    """
    Resolves full conversation context:
    - conversation's merchant
    - customer
    - trigger
    - previous bot message and action
    - previous turns
    - current state
    - resolved domain entities (merchant, category, customer, trigger)
    """
    conv = conversation_store.get(conversation_id) if conversation_store else None

    # Check cross-merchant isolation violation
    stored_merchant_id = conv.get("merchant_id") if conv else None
    incoming_merchant_id = request.merchant_id
    if stored_merchant_id and incoming_merchant_id and stored_merchant_id != incoming_merchant_id:
        return {
            "cross_merchant_violation": True,
            "stored_merchant_id": stored_merchant_id,
            "incoming_merchant_id": incoming_merchant_id,
            "conv": conv,
            "merchant_id": stored_merchant_id,
            "customer_id": conv.get("customer_id") if conv else None,
            "trigger_id": conv.get("trigger_id") if conv else None,
            "previous_bot_message": None,
            "previous_bot_action": None,
            "previous_turns": [],
            "current_state": conv.get("state") if conv else ConversationState.NEW.value,
            "resolved_context": None,
        }

    merchant_id = stored_merchant_id or incoming_merchant_id
    customer_id = (conv.get("customer_id") if conv else None) or request.customer_id
    trigger_id = conv.get("trigger_id") if conv else None

    # Check cross-merchant trigger ownership
    if trigger_id and context_store and merchant_id:
        trg_item = context_store.get("trigger", trigger_id)
        if trg_item and isinstance(trg_item.get("payload"), dict):
            trg_m_id = trg_item["payload"].get("merchant_id")
            if trg_m_id and trg_m_id != merchant_id:
                return {
                    "cross_merchant_violation": True,
                    "stored_merchant_id": merchant_id,
                    "incoming_merchant_id": trg_m_id,
                    "conv": conv,
                    "merchant_id": merchant_id,
                    "customer_id": customer_id,
                    "trigger_id": trigger_id,
                    "previous_bot_message": None,
                    "previous_bot_action": None,
                    "previous_turns": [],
                    "current_state": conv.get("state") if conv else ConversationState.NEW.value,
                    "resolved_context": None,
                }

    # Check cross-merchant customer ownership
    if customer_id and context_store and merchant_id:
        cust_item = context_store.get("customer", f"{merchant_id}:{customer_id}")
        if not cust_item:
            cust_item = context_store.get("customer", f"{customer_id}_for_{merchant_id}")
        if not cust_item:
            cust_item = context_store.get("customer", customer_id)

        if cust_item and isinstance(cust_item.get("payload"), dict):
            cust_payload = cust_item["payload"]
            cust_m_id = cust_payload.get("merchant_id") or cust_payload.get("relationship", {}).get("primary_merchant_id")
            if cust_m_id and cust_m_id != merchant_id:
                return {
                    "cross_merchant_violation": True,
                    "stored_merchant_id": merchant_id,
                    "incoming_merchant_id": cust_m_id,
                    "conv": conv,
                    "merchant_id": merchant_id,
                    "customer_id": customer_id,
                    "trigger_id": trigger_id,
                    "previous_bot_message": None,
                    "previous_bot_action": None,
                    "previous_turns": [],
                    "current_state": conv.get("state") if conv else ConversationState.NEW.value,
                    "resolved_context": None,
                }

    sent_messages = conv.get("sent_messages", []) if conv else []
    previous_bot_message = sent_messages[-1] if sent_messages else None
    previous_bot_action = conv.get("last_action") if conv else (previous_bot_message.get("action") if previous_bot_message else None)
    previous_turns = conv.get("turns", []) if conv else []
    current_state = conv.get("state") if conv else ConversationState.NEW.value

    rc = resolved_context
    if rc is None and context_store is not None:
        from app.decision_engine import resolve_context
        if trigger_id:
            rc = resolve_context(context_store, trigger_id)

        # If no trigger associated with conv yet, see if context_store has a candidate trigger for this merchant
        if rc is None and merchant_id:
            candidate_trigger = None
            if hasattr(context_store, "_data"):
                with context_store._lock:
                    for (scope, _), item in context_store._data.items():
                        if scope == "trigger" and isinstance(item.get("payload"), dict):
                            if item["payload"].get("merchant_id") == merchant_id:
                                candidate_trigger = item["payload"]
                                break
            if candidate_trigger:
                rc = resolve_context(context_store, candidate_trigger)

        # If still no rc, resolve merchant, category, customer directly into ResolvedContext
        if rc is None and merchant_id:
            m_item = context_store.get("merchant", merchant_id)
            m_payload = m_item["payload"] if m_item and isinstance(m_item.get("payload"), dict) else None
            if m_payload:
                cat_slug = m_payload.get("category_slug")
                c_item = context_store.get("category", cat_slug) if cat_slug else None
                c_payload = c_item["payload"] if c_item and isinstance(c_item.get("payload"), dict) else None
                cust_item = None
                if customer_id:
                    cust_item = context_store.get("customer", f"{merchant_id}:{customer_id}")
                    if not cust_item:
                        cust_item = context_store.get("customer", f"{customer_id}_for_{merchant_id}")
                    if not cust_item:
                        cust_item = context_store.get("customer", customer_id)
                cust_payload = cust_item["payload"] if cust_item and isinstance(cust_item.get("payload"), dict) else None
                rc = ResolvedContext(
                    trigger={},
                    merchant=m_payload,
                    category=c_payload,
                    customer=cust_payload,
                    versions={
                        "merchant": m_item.get("version"),
                        "category": c_item.get("version") if c_item else None,
                        "customer": cust_item.get("version") if cust_item else None,
                    },
                    metadata={},
                )

    # Attach customer if resolved separately and missing in rc
    if rc and not rc.customer and customer_id and context_store:
        cust_item = context_store.get("customer", f"{merchant_id}:{customer_id}") if merchant_id else None
        if not cust_item and merchant_id:
            cust_item = context_store.get("customer", f"{customer_id}_for_{merchant_id}")
        if not cust_item:
            cust_item = context_store.get("customer", customer_id)
        if cust_item and isinstance(cust_item.get("payload"), dict):
            rc.customer = cust_item["payload"]
            rc.versions["customer"] = cust_item.get("version")

    return {
        "cross_merchant_violation": False,
        "conversation_id": conversation_id,
        "merchant_id": merchant_id,
        "customer_id": customer_id,
        "trigger_id": trigger_id or (rc.trigger.get("id") if rc and rc.trigger else None),
        "previous_bot_message": previous_bot_message,
        "previous_bot_action": previous_bot_action,
        "previous_turns": previous_turns,
        "current_state": current_state,
        "resolved_context": rc,
        "conv": conv,
    }


def generate_positive_continuation(
    resolved_context: Optional[ResolvedContext],
    previous_bot_message: Optional[Dict[str, Any]] = None,
    previous_turns: Optional[List[Dict[str, Any]]] = None,
    request: Optional[ReplyRequest] = None,
    current_state: Optional[Any] = None,
    prior_action: Optional[str] = None,
) -> str:
    """
    Constructs a context-aware continuation for the active campaign/trigger
    when the merchant/customer responds positively.
    Switches directly to action mode with concrete deliverables and zero qualifying questions.
    """
    rc = resolved_context
    m = rc.merchant if rc else None
    c = rc.category if rc else None
    cust = rc.customer if rc else None
    trg = rc.trigger if rc else {}

    raw_m_name = (m.get("identity", {}).get("name") or m.get("name")) if m else None
    m_name = _clean_entity_text(raw_m_name, "Merchant Partner") if raw_m_name else None

    raw_m_owner = (m.get("identity", {}).get("owner_first_name") or m.get("owner")) if m else None
    m_owner = _clean_entity_text(raw_m_owner, "") if raw_m_owner else None

    raw_cust_name = (cust.get("identity", {}).get("name") or cust.get("name")) if cust else None
    cust_name = _clean_entity_text(raw_cust_name, "your customer") if raw_cust_name else None

    raw_cat_name = (c.get("display_name") or c.get("slug")) if c else (m.get("category_slug") if m else None)
    cat_name = _clean_entity_text(raw_cat_name, "your category") if raw_cat_name else None

    # Resolve active offers
    active_offers: List[str] = []
    if m:
        for off in m.get("offers", []):
            if isinstance(off, dict) and off.get("status") == "active" and off.get("title"):
                active_offers.append(off["title"].strip())
            elif isinstance(off, str) and off.strip():
                active_offers.append(off.strip())
        if not active_offers and isinstance(m.get("active_offers"), list):
            for ao in m["active_offers"]:
                if isinstance(ao, str) and ao.strip() and ao.strip() not in active_offers:
                    active_offers.append(ao.strip())
    raw_primary_offer = active_offers[0] if active_offers else None
    primary_offer = _clean_entity_text(raw_primary_offer, "special offer") if raw_primary_offer else None

    prev_body = (previous_bot_message.get("body") or "") if previous_bot_message else ""
    prev_lower = prev_body.lower()

    t_kind = str(trg.get("kind") or trg.get("type") or "").lower()

    norm_state = normalize_state(current_state) if current_state else None
    if norm_state == ConversationState.WAIT:
        state_note = " Resuming from delay."
    elif norm_state == ConversationState.ANSWER:
        state_note = " Moving forward with that."
    elif norm_state == ConversationState.SEND:
        state_note = " Confirmed and already in motion."
    elif norm_state == ConversationState.END:
        state_note = " Reopening conversation."
    elif norm_state == ConversationState.REDIRECT:
        state_note = " Following up on recommendations."
    else:
        state_note = ""

    # 1. Research Digest / Clinical briefing
    if (
        t_kind in ("research_digest", "category_research_digest_release", "compliance_alert", "regulation_change", "cde_opportunity")
        or "abstract" in prev_lower
        or "digest" in prev_lower
        or "research" in prev_lower
    ):
        owner_part = f", {m_owner}" if m_owner else ""
        store_part = f" for {m_name}" if m_name else ""
        return (
            f"Done{owner_part}!{state_note} Draft ready. Here's what's next: sending over the full research briefing and "
            f"patient communication draft{store_part}. Confirming dispatch and launching now."
        )

    # 2. Customer Recall / Lapsed Customer / Customer Winback
    if (
        t_kind in ("recall_due", "customer_lapsed_soft", "customer_winback", "dormant_customer", "lapse")
        or "recall" in prev_lower
        or (cust_name and "visit" in prev_lower)
    ):
        if cust_name:
            offer_part = f" featuring {primary_offer}" if primary_offer else ""
            return (
                f"Done!{state_note} Draft ready. Here's what's next: sending the recall outreach for {cust_name}{offer_part}. "
                "Confirming dispatch and proceeding now."
            )
        elif primary_offer:
            store_part = f" for {m_name}" if m_name else ""
            return (
                f"Done!{state_note} Draft ready. Here's what's next: launching the customer winback campaign featuring {primary_offer}{store_part}. "
                "Confirming dispatch and proceeding now."
            )
        else:
            store_part = f" for {m_name}" if m_name else ""
            return (
                f"Done!{state_note} Draft ready. Here's what's next: dispatching the customer recall campaign{store_part}. "
                "Confirming dispatch and proceeding now."
            )

    # 3. Performance Drop / Views / CTR Spike / Traffic Opportunity
    if (
        t_kind in ("performance_drop", "perf_spike", "traffic_surge", "low_ctr", "opportunity", "review_velocity")
        or "ctr" in prev_lower
        or "views" in prev_lower
        or "calls" in prev_lower
    ):
        if primary_offer:
            store_part = f" for {m_name}" if m_name else ""
            return (
                f"Done!{state_note} Draft ready. Here's what's next: launching the store visibility promotion featuring {primary_offer} "
                f"to drive customer calls and visits{store_part}. Confirming launch now."
            )
        else:
            store_part = f" for {m_name}" if m_name else ""
            cat_part = f" across {cat_name}" if cat_name else ""
            return (
                f"Done!{state_note} Draft ready. Here's what's next: activating the category visibility promotion{cat_part}{store_part} "
                "to drive store traffic. Confirming launch now."
            )

    # 4. Appointment / Refill / Renewal / Follow-up
    if (
        t_kind in ("appointment_tomorrow", "chronic_refill_due", "trial_followup", "renewal_due")
        or "appointment" in prev_lower
        or "refill" in prev_lower
    ):
        target = cust_name or (m_name or "the scheduled slot")
        return (
            f"Done!{state_note} Draft ready. Here's what's next: sending the appointment and reminder confirmation for {target}. "
            "Confirming dispatch now."
        )

    # 5. Festive / Event / Opportunity
    if (
        t_kind in ("festive", "festival", "event", "holiday", "weekend_rush", "ipl_match_today")
        or "festive" in prev_lower
        or "rush" in prev_lower
    ):
        offer_part = f" featuring {primary_offer}" if primary_offer else " featuring special store offers"
        store_part = f" for {m_name}" if m_name else ""
        return (
            f"Done!{state_note} Draft ready. Here's what's next: locking in the festive promotion{offer_part}{store_part}. "
            "Confirming schedule and launching now."
        )

    # 6. Fallback with Active Offer
    if primary_offer:
        store_part = f" for {m_name}" if m_name else ""
        return (
            f"Done!{state_note} Draft ready. Here's what's next: we've prepared your campaign deliverable featuring {primary_offer}{store_part} "
            "and it's ready to proceed. Sending confirmation and launching now."
        )

    # 7. Fallback with Merchant Name
    if m_name:
        return (
            f"Done!{state_note} Draft ready. Here's what's next: we've prepared your campaign deliverable for {m_name} "
            "and it's ready to proceed. Sending confirmation and launching now."
        )

    # 8. Baseline fallback
    return (
        f"Done!{state_note} Draft ready. Here's what's next: we've prepared your campaign deliverable and it's "
        "ready to proceed. Sending confirmation and launching now."
    )


def generate_grounded_answer(
    resolved_context: Optional[ResolvedContext],
    previous_bot_message: Optional[Dict[str, Any]] = None,
    previous_turns: Optional[List[Dict[str, Any]]] = None,
    message: str = "",
    current_state: Optional[Any] = None,
    prior_action: Optional[str] = None,
) -> str:
    """
    Constructs a grounded, factual answer to the merchant/customer question
    using the resolved conversation context.
    """
    rc = resolved_context
    m = rc.merchant if rc else None
    c = rc.category if rc else None
    cust = rc.customer if rc else None

    raw_m_name = (m.get("identity", {}).get("name") or m.get("name")) if m else None
    m_name = _clean_entity_text(raw_m_name, "your store") if raw_m_name else None

    raw_cat_name = (c.get("display_name") or c.get("slug")) if c else (m.get("category_slug") if m else None)
    cat_name = _clean_entity_text(raw_cat_name, "your category") if raw_cat_name else "your category"

    raw_cust_name = (cust.get("identity", {}).get("name") or cust.get("name")) if cust else None
    cust_name = _clean_entity_text(raw_cust_name, "the customer") if raw_cust_name else None

    # Performance
    perf = m.get("performance") if (m and isinstance(m.get("performance"), dict)) else {}
    m_ctr = perf.get("ctr") if perf.get("ctr") is not None else (m.get("ctr") if m else None)
    m_views = perf.get("views") if perf.get("views") is not None else (m.get("views") if m else None)
    m_calls = perf.get("calls") if perf.get("calls") is not None else (m.get("calls") if m else None)

    # Category peer stats
    peer_stats = c.get("peer_stats") if (c and isinstance(c.get("peer_stats"), dict)) else {}
    peer_ctr = peer_stats.get("avg_ctr") if peer_stats.get("avg_ctr") is not None else (c.get("peer_avg_ctr") if c else None)

    # Active offers
    active_offers: List[str] = []
    if m:
        for off in m.get("offers", []):
            if isinstance(off, dict) and off.get("status") == "active" and off.get("title"):
                active_offers.append(off["title"].strip())
            elif isinstance(off, str) and off.strip():
                active_offers.append(off.strip())
        if not active_offers and isinstance(m.get("active_offers"), list):
            for ao in m["active_offers"]:
                if isinstance(ao, str) and ao.strip() and ao.strip() not in active_offers:
                    active_offers.append(ao.strip())
    raw_primary_offer = active_offers[0] if active_offers else None
    primary_offer = _clean_entity_text(raw_primary_offer, "an active promotion") if raw_primary_offer else None

    # Digest
    digests = c.get("digest") if (c and isinstance(c.get("digest"), list)) else []
    matched_digest = digests[0] if digests and isinstance(digests[0], dict) else {}
    digest_source = matched_digest.get("source")
    digest_title = matched_digest.get("title")

    # Customer relationship
    cust_rel = cust.get("relationship") if (cust and isinstance(cust.get("relationship"), dict)) else {}

    msg_lower = (message or "").lower()

    # 1. Performance / Benchmark / CTR question
    if any(k in msg_lower for k in ("benchmark", "peer", "ctr", "metric", "calculate", "stat", "view", "call", "traffic", "number")):
        ctr_str = f"{m_ctr * 100:.1f}%" if m_ctr is not None else "your 30d CTR"
        if peer_ctr is not None:
            peer_str = f"{peer_ctr * 100:.1f}%"
            return (
                f"We calculate the peer benchmark by aggregating median performance across active {cat_name} "
                f"in your locality (averaging {peer_str} CTR). Your store currently records {ctr_str}. "
                f"We create targeted promotions to help close this gap. Would you like to review a draft?"
            )
        elif m_views is not None or m_calls is not None:
            stats = []
            if m_views is not None:
                stats.append(f"{m_views} views")
            if m_calls is not None:
                stats.append(f"{m_calls} calls")
            stats_str = " and ".join(stats)
            return (
                f"Your store recorded {stats_str} over the last 30 days. We benchmark your visibility "
                f"against {cat_name} peers to uncover high-intent demand. Would you like to review a draft?"
            )
        else:
            return (
                f"We benchmark your store's visibility against category peers in {cat_name} and create targeted "
                "promotions to drive more customer calls and visits. Would you like to review a draft?"
            )

    # 2. Offer / Pricing / Discount question
    if any(k in msg_lower for k in ("offer", "price", "pricing", "cost", "discount", "deal", "charge", "rate", "fee", "term", "package")):
        if primary_offer:
            store_part = f" for {m_name}" if m_name else ""
            return (
                f"We are featuring your active promotion: {primary_offer}{store_part}. We highlight this to "
                "high-intent local shoppers on magicpin with transparent pricing and no creative fees. "
                "Would you like to review the draft?"
            )
        else:
            store_part = f" for {m_name}" if m_name else " for your store"
            return (
                f"We help set up targeted promotions{store_part} that drive customer visits while protecting your "
                "margins. Would you like to review a draft?"
            )

    # 3. Research / Study / Source question
    if any(k in msg_lower for k in ("research", "study", "paper", "source", "journal", "digest", "clinical", "evidence", "citation")):
        if digest_source or digest_title:
            src = f" from {digest_source}" if digest_source else ""
            tit = f" ({digest_title})" if digest_title else ""
            return (
                f"This clinical briefing is grounded in peer-reviewed research{src}{tit}. "
                "We drafted a patient-friendly 90-second educational summary for your clinic. Would you like to review it?"
            )
        else:
            return (
                "We source verified category research and peer clinical insights to help healthcare providers "
                "communicate preventative care to patients. Would you like to review the draft?"
            )

    # 4. Customer / Patient question
    if any(k in msg_lower for k in ("customer", "patient", "who", "whom", "last visit", "history", "appointment", "due")):
        if cust_name:
            last_v = cust_rel.get("last_visit")
            visit_part = f", whose last recorded visit was {last_v}" if last_v else ""
            return (
                f"This outreach is prepared for {cust_name}{visit_part}. We have set up a personalized service "
                "reminder to encourage a timely return. Would you like to review the message draft?"
            )
        else:
            store_part = f" for {m_name}" if m_name else ""
            return (
                f"We monitor customer visit intervals and service cadences{store_part} to identify customers due "
                "for their next visit. Would you like to review the cohort draft?"
            )

    # 5. Default grounded answer
    if m_name or cat_name:
        name_str = m_name or "your store"
        return (
            f"We benchmark {name_str}'s visibility against {cat_name} peers and create targeted "
            "promotions to drive more customer calls and visits. Would you like to review a draft?"
        )

    # 6. Baseline fallback
    return (
        "We benchmark your store's visibility against category peers and create targeted "
        "promotions to drive more customer calls and visits. Would you like to review a draft?"
    )


# ---------------------------------------------------------------------------
# State Machine & Turn Transition Logic
# ---------------------------------------------------------------------------

class ConversationStateMachine:
    """Manages conversation state transitions and produces context-aware reply actions."""

    def __init__(self) -> None:
        self._auto_reply_tracker: Dict[str, int] = {}
        self._lock = threading.Lock()

    def reset(self) -> None:
        """Reset auto-reply tracker."""
        self._auto_reply_tracker.clear()

    def is_transition_allowed(self, from_state: Any, intent: Intent) -> bool:
        """Returns True if the transition from from_state with intent is allowed by the formal grammar."""
        norm_state = normalize_state(from_state)
        rule = TRANSITION_MATRIX.get((norm_state, intent))
        return rule.allowed if rule else False

    def get_allowed_transitions(self, from_state: Any) -> List[Intent]:
        """Returns the list of allowed Intents from the given state."""
        norm_state = normalize_state(from_state)
        return [i for i in Intent if self.is_transition_allowed(norm_state, i)]

    def evaluate_transition(
        self,
        current_state: Any,
        intent: Intent,
        prior_action: Optional[str] = None,
    ) -> TransitionRule:
        """
        Evaluates transition deterministically against the formal matrix.
        Transition behavior depends on current conversation state and prior action.
        """
        norm_state = normalize_state(current_state)
        base_rule = TRANSITION_MATRIX.get((norm_state, intent))
        if not base_rule:
            return TransitionRule(
                from_state=norm_state,
                intent=intent,
                to_state=ConversationState.END,
                allowed=False,
                action="end",
                rationale_template=f"Rejected impossible transition from state '{norm_state.value}' with intent '{intent.value}'.",
            )

        contextual_rationale = base_rule.rationale_template
        if base_rule.allowed:
            if norm_state == ConversationState.WAIT and intent == Intent.POSITIVE:
                contextual_rationale = "Resuming campaign from delayed state upon positive merchant response."
            elif norm_state == ConversationState.ANSWER and intent == Intent.POSITIVE:
                contextual_rationale = "Merchant accepted proposal following grounded inquiry answer; advancing to campaign execution."
            elif norm_state == ConversationState.SEND and intent == Intent.POSITIVE:
                contextual_rationale = "Merchant reaffirmed commitment; confirming campaign dispatch is underway."
            elif norm_state == ConversationState.REDIRECT and intent == Intent.POSITIVE:
                contextual_rationale = "Merchant opted in after off-topic redirection; launching growth campaign."
            elif norm_state == ConversationState.END and intent == Intent.POSITIVE:
                contextual_rationale = "Reopened ended conversation upon explicit positive merchant opt-in."
            elif norm_state == ConversationState.SEND and intent == Intent.QUESTION:
                contextual_rationale = "Answering post-commitment merchant question regarding active campaign."
            elif norm_state == ConversationState.WAIT and intent == Intent.DELAY:
                contextual_rationale = "Merchant requested extended delay; maintaining wait state."
            elif norm_state == ConversationState.REDIRECT and intent == Intent.OFF_TOPIC:
                contextual_rationale = "Subsequent off-topic message detected; maintaining growth redirection."

        return TransitionRule(
            from_state=base_rule.from_state,
            intent=base_rule.intent,
            to_state=base_rule.to_state,
            allowed=base_rule.allowed,
            action=base_rule.action,
            rationale_template=contextual_rationale,
        )

    def get_all_states(self) -> List[ConversationState]:
        """Returns all formal conversation states."""
        return list(ConversationState)

    def get_terminal_states(self) -> Set[ConversationState]:
        """Returns terminal conversation states."""
        return set(TERMINAL_STATES)

    def get_active_states(self) -> Set[ConversationState]:
        """Returns active conversation states."""
        return set(ACTIVE_STATES)

    def process_reply(
        self,
        request: ReplyRequest,
        conversation_store: Any,
        context_store: Optional[Any] = None,
        resolved_context: Optional[Any] = None,
    ) -> ReplyResponse:
        with self._lock:
            return self._process_reply_locked(
                request=request,
                conversation_store=conversation_store,
                context_store=context_store,
                resolved_context=resolved_context,
            )

    def _process_reply_locked(
        self,
        request: ReplyRequest,
        conversation_store: Any,
        context_store: Optional[Any] = None,
        resolved_context: Optional[Any] = None,
    ) -> ReplyResponse:
        conv_id = request.conversation_id
        msg = request.message.strip()
        turn_number = request.turn_number or 1

        # 1. Resolve conversation context
        c_ctx = resolve_conversation_context(
            conversation_id=conv_id,
            request=request,
            conversation_store=conversation_store,
            context_store=context_store,
            resolved_context=resolved_context,
        )

        # Cross-merchant security violation guard
        if c_ctx.get("cross_merchant_violation"):
            logger.warning(
                f"Cross-merchant conversation access blocked: conv {conv_id} owned by "
                f"{c_ctx.get('stored_merchant_id')} but requested by {c_ctx.get('incoming_merchant_id')}"
            )
            return ReplyResponse(
                action="end",
                rationale="Cross-merchant conversation access rejected.",
            )

        conv = c_ctx["conv"]
        merchant_id = c_ctx["merchant_id"]
        customer_id = c_ctx["customer_id"]
        trigger_id = c_ctx["trigger_id"]
        previous_bot_message = c_ctx["previous_bot_message"]
        previous_turns = c_ctx["previous_turns"]
        current_state = c_ctx["current_state"]
        rc = c_ctx["resolved_context"]

        # 2. Duplicate Turn Check
        is_duplicate = False
        if previous_turns:
            req_turn_norm = 1 if request.turn_number is None else request.turn_number
            for t in previous_turns:
                t_turn = t.get("turn_number")
                t_turn_norm = 1 if t_turn is None else t_turn
                if t_turn_norm == req_turn_norm and (t.get("message") or "").strip() == msg:
                    is_duplicate = True
                    break
        if is_duplicate:
            return ReplyResponse(
                action="wait",
                wait_seconds=600,
                rationale="Duplicate turn re-sent; waiting for new input.",
            )

        # 3. Repeated-Reply / Auto-Reply Loop Detection
        is_auto = is_auto_reply_text(msg)
        repeat_count = 0
        if previous_turns:
            for t in reversed(previous_turns):
                if t.get("role") in ("merchant", "customer", "user"):
                    if t.get("message", "").strip().lower() == msg.lower():
                        repeat_count += 1

        # Isolate tracking key by merchant_id and conversation family
        family_id = re.sub(r'_\d+$', '', conv_id)
        tracking_key = f"{merchant_id or 'none'}:{family_id}"
        if is_auto:
            self._auto_reply_tracker[tracking_key] = self._auto_reply_tracker.get(tracking_key, 0) + 1
            auto_count = self._auto_reply_tracker[tracking_key]
        else:
            self._auto_reply_tracker.pop(tracking_key, None)
            auto_count = 0

        # After 2-3 consecutive detected auto-replies, end conversation gracefully
        # (Turn 1: wait, Turn 2: wait, Turn 3+: end)
        effective_repeats = max(repeat_count, (auto_count - 1) if auto_count > 0 else 0)

        if is_auto or repeat_count >= 1:
            should_end = effective_repeats >= 2 or auto_count >= 3
            new_state = ConversationState.END if should_end else ConversationState.WAIT
            wait_s = 1800 if new_state == ConversationState.WAIT else None

            if conversation_store:
                self._record_turn(
                    conversation_store=conversation_store,
                    conv_id=conv_id,
                    request=request,
                    state=new_state,
                    action=new_state.value.lower(),
                    merchant_id=merchant_id,
                    customer_id=customer_id,
                    trigger_id=trigger_id,
                )

            return ReplyResponse(
                action="end" if should_end else "wait",
                wait_seconds=wait_s,
                rationale="Consecutive auto-reply loop detected; exiting gracefully." if should_end else "Repeated reply or auto-responder detected; backing off.",
            )

        # 4. Deterministic Intent Classification
        intent = classify_intent(msg)

        # 5. Formal Transition Evaluation & Rejection of Impossible Transitions
        norm_state = normalize_state(current_state)
        rule = self.evaluate_transition(
            current_state=norm_state,
            intent=intent,
            prior_action=c_ctx.get("previous_bot_action"),
        )

        if not rule.allowed:
            # Reject impossible transition instead of silently processing it
            logger.warning(
                "Rejected impossible transition: %s + %s -> %s",
                norm_state.value,
                intent.value,
                rule.to_state.value,
            )
            if conversation_store:
                turn_num = 1 if request.turn_number is None else request.turn_number
                conversation_store.add_turn(
                    conv_id,
                    {
                        "role": request.from_role or "merchant",
                        "message": request.message,
                        "turn_number": turn_num,
                        "received_at": request.received_at,
                    },
                )
            return ReplyResponse(
                action=rule.action,
                wait_seconds=None,
                rationale=rule.rationale_template,
                body=None,
                cta=None,
            )

        # 6. Intent & Context Aware State Transition Execution
        state = rule.to_state
        action = rule.action
        rationale = rule.rationale_template
        wait_s = 1800 if state == ConversationState.WAIT else None
        body = None

        if intent == Intent.HOSTILE:
            body = None
            wait_s = None

        elif intent == Intent.NEGATIVE:
            body = None
            wait_s = None

        elif intent == Intent.DELAY:
            body = None
            wait_s = 1800

        elif intent == Intent.OFF_TOPIC:
            m = rc.merchant if rc else None
            raw_m_name = (m.get("identity", {}).get("name") or m.get("name")) if m else None
            clean_m = _clean_entity_text(raw_m_name, "") if raw_m_name else ""
            if clean_m:
                body = (
                    f"I focus on helping {clean_m} grow store traffic and customer engagement on magicpin. "
                    "Would you like to review recommendations for this week?"
                )
            else:
                body = (
                    "I focus on helping you grow store traffic and customer engagement on magicpin. "
                    "Would you like to review recommendations for this week?"
                )
            wait_s = None

        elif intent == Intent.POSITIVE:
            # Positive commitment -> State SEND
            # Switch directly to action mode with concrete campaign continuation and zero qualifying questions
            body = generate_positive_continuation(
                resolved_context=rc,
                previous_bot_message=previous_bot_message,
                previous_turns=previous_turns,
                request=request,
                current_state=norm_state,
                prior_action=c_ctx.get("previous_bot_action"),
            )
            wait_s = None

        else:
            # Intent.QUESTION / default inquiry -> State ANSWER
            body = generate_grounded_answer(
                resolved_context=rc,
                previous_bot_message=previous_bot_message,
                previous_turns=previous_turns,
                message=msg,
                current_state=norm_state,
                prior_action=c_ctx.get("previous_bot_action"),
            )
            wait_s = None

        # 7. Record turn and update state in ConversationStore
        if conversation_store:
            self._record_turn(
                conversation_store=conversation_store,
                conv_id=conv_id,
                request=request,
                state=state,
                action=action,
                bot_body=body,
                merchant_id=merchant_id,
                customer_id=customer_id,
                trigger_id=trigger_id,
            )

        return ReplyResponse(
            action=action,
            wait_seconds=wait_s,
            rationale=rationale,
            body=body,
            cta="open_ended" if body else None,
        )

    def _record_turn(
        self,
        conversation_store: Any,
        conv_id: str,
        request: ReplyRequest,
        state: ConversationState,
        action: str,
        bot_body: Optional[str] = None,
        merchant_id: Optional[str] = None,
        customer_id: Optional[str] = None,
        trigger_id: Optional[str] = None,
    ) -> None:
        conversation_store.create_or_update(
            conversation_id=conv_id,
            merchant_id=merchant_id or request.merchant_id,
            customer_id=customer_id or request.customer_id,
            trigger_id=trigger_id,
            last_action=action,
            state=state.value,
        )
        turn_num = 1 if request.turn_number is None else request.turn_number
        conversation_store.add_turn(
            conv_id,
            {
                "role": request.from_role or "merchant",
                "message": request.message,
                "turn_number": turn_num,
                "received_at": request.received_at,
            },
        )
        if bot_body:
            conversation_store.add_sent_message(
                conv_id,
                {
                    "body": bot_body,
                    "action": action,
                    "state": state.value,
                    "sent_at": datetime.now(timezone.utc).isoformat(),
                },
            )


conversation_state_machine = ConversationStateMachine()

__all__ = [
    "ConversationState",
    "Intent",
    "TERMINAL_STATES",
    "ACTIVE_STATES",
    "INITIAL_STATES",
    "TransitionRule",
    "TRANSITION_MATRIX",
    "normalize_state",
    "classify_intent",
    "is_auto_reply_text",
    "resolve_conversation_context",
    "generate_positive_continuation",
    "generate_grounded_answer",
    "ConversationStateMachine",
    "conversation_state_machine",
]
