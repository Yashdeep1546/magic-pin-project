"""Conversation state machine and rule-based intent classifier for Magicpin Vera bot.

Handles intent classification, state transitions, auto-reply loop detection,
and action determination for POST /v1/reply.
"""

import re
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from app.models import ReplyRequest, ReplyResponse


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


# ---------------------------------------------------------------------------
# Rule-Based Intent Classifier
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
    r"our team will respond shortly",
    r"out of office",
    r"auto-reply",
    r"automated response",
    r"we have received your message",
]


def classify_intent(message: str) -> Intent:
    """
    Classifies merchant/customer free-text message into an Intent:
    1. Hostile -> HOSTILE
    2. Delay -> DELAY
    3. Negative -> NEGATIVE
    4. Positive -> POSITIVE
    5. Off-Topic -> OFF_TOPIC
    6. Anything else / Question -> QUESTION
    """
    if not message:
        return Intent.QUESTION

    msg_clean = message.strip().lower()

    # 1. Hostile check
    for pat in HOSTILE_PATTERNS:
        if re.search(pat, msg_clean):
            return Intent.HOSTILE

    # 2. Delay check
    for pat in DELAY_PATTERNS:
        if re.search(pat, msg_clean):
            return Intent.DELAY

    # 3. Negative check
    for pat in NEGATIVE_PATTERNS:
        if re.search(pat, msg_clean):
            return Intent.NEGATIVE

    # 4. Off-topic check
    for pat in OFF_TOPIC_PATTERNS:
        if re.search(pat, msg_clean):
            return Intent.OFF_TOPIC

    # 5. Positive check
    for pat in POSITIVE_PATTERNS:
        if re.search(pat, msg_clean):
            return Intent.POSITIVE

    # 6. Fallback / Question check
    return Intent.QUESTION


def is_auto_reply_text(message: str) -> bool:
    """Detects standard canned auto-reply messages."""
    msg_clean = message.strip().lower()
    for pat in AUTO_REPLY_PATTERNS:
        if re.search(pat, msg_clean):
            return True
    return False


# ---------------------------------------------------------------------------
# State Machine & Turn Transition Logic
# ---------------------------------------------------------------------------

class ConversationStateMachine:
    """Manages conversation state transitions and produces reply actions."""

    def process_reply(
        self,
        request: ReplyRequest,
        conversation_store: Any,
    ) -> ReplyResponse:
        conv_id = request.conversation_id
        msg = request.message.strip()
        turn_number = request.turn_number or 1

        conv = conversation_store.get(conv_id) if conversation_store else None

        # 1. Stale / Ended Conversation Check
        if conv and conv.get("state") == ConversationState.END:
            return ReplyResponse(
                action="end",
                rationale="Conversation was already closed.",
            )

        # 2. Duplicate Turn Check
        if conv and conv.get("turns"):
            last_turn = conv["turns"][-1]
            if (
                last_turn.get("turn_number") == turn_number
                and last_turn.get("message") == msg
            ):
                return ReplyResponse(
                    action="wait",
                    wait_seconds=600,
                    rationale="Duplicate turn re-sent; waiting for new input.",
                )

        # 3. Repeated-Reply / Auto-Reply Loop Detection
        is_auto = is_auto_reply_text(msg)
        repeat_count = 0
        if conv and conv.get("turns"):
            for t in reversed(conv["turns"]):
                if t.get("role") in ("merchant", "customer", "user"):
                    if t.get("message", "").strip().lower() == msg.lower():
                        repeat_count += 1

        if is_auto or repeat_count >= 1:
            # Repeated text detected 2+ times total (current + past >= 1)
            # If repeated multiple times, end conversation; otherwise wait
            new_state = ConversationState.END if repeat_count >= 2 else ConversationState.WAIT
            wait_s = 1800 if new_state == ConversationState.WAIT else None

            if conversation_store:
                self._record_turn(conversation_store, conv_id, request, new_state, new_state.value.lower())

            return ReplyResponse(
                action="end" if new_state == ConversationState.END else "wait",
                wait_seconds=wait_s,
                rationale="Repeated reply or auto-responder detected; backing off.",
            )

        # 4. Out-of-order Turn Detection
        if conv and conv.get("turns"):
            highest_turn = max(t.get("turn_number", 0) for t in conv["turns"])
            if turn_number < highest_turn:
                # Still process gracefully, do not crash
                pass

        # 5. Classify Intent
        intent = classify_intent(msg)

        # 6. Intent-to-State Transition Logic
        if intent == Intent.HOSTILE:
            state = ConversationState.END
            action = "end"
            body = None
            wait_s = None
            rationale = "Hostile message detected; terminating conversation immediately."

        elif intent == Intent.NEGATIVE:
            state = ConversationState.END
            action = "end"
            body = None
            wait_s = None
            rationale = "Merchant expressed negative intent; gracefully ending conversation."

        elif intent == Intent.DELAY:
            state = ConversationState.WAIT
            action = "wait"
            body = None
            wait_s = 1800
            rationale = "Merchant requested delay; backing off."

        elif intent == Intent.OFF_TOPIC:
            state = ConversationState.REDIRECT
            action = "send"
            body = (
                "I focus on helping you grow store traffic and customer engagement on magicpin. "
                "Would you like to review recommendations for this week?"
            )
            wait_s = None
            rationale = "Query was off-topic; gently redirecting to merchant growth capabilities."

        elif intent == Intent.POSITIVE:
            # Positive commitment -> State SEND
            # Must switch directly to actioning, not asking another qualifying question!
            state = ConversationState.SEND
            action = "send"
            body = (
                "Done! Proceeding with this campaign now. I have prepared the draft and it is ready "
                "to proceed. Here is the confirmation for your next steps."
            )
            wait_s = None
            rationale = "Merchant committed positively; executing action mode without further qualification."

        else:
            # Intent.QUESTION / default inquiry -> State ANSWER
            state = ConversationState.ANSWER
            action = "send"
            body = (
                "We benchmark your store's visibility against category peers and create targeted "
                "promotions to drive more customer calls and visits. Would you like to review a draft?"
            )
            wait_s = None
            rationale = "Answering merchant question regarding platform and promotion capabilities."

        # 7. Record turn and update state in ConversationStore
        if conversation_store:
            self._record_turn(conversation_store, conv_id, request, state, action, body)

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
    ) -> None:
        conversation_store.create_or_update(
            conversation_id=conv_id,
            merchant_id=request.merchant_id,
            customer_id=request.customer_id,
            last_action=action,
            state=state.value,
        )
        conversation_store.add_turn(
            conv_id,
            {
                "role": request.from_role or "merchant",
                "message": request.message,
                "turn_number": request.turn_number,
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
                },
            )


conversation_state_machine = ConversationStateMachine()
