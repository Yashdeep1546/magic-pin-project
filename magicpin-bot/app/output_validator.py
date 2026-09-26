"""Output grounding validator for Magicpin Vera bot.

Enforces strict grounding against an evidence ledger extracted from compact context.
Rejects hallucinated metrics, prices, offers, names, multiple CTAs, duplicate messages,
and generic filler, triggering deterministic fallback.
"""

import hashlib
import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("magicpin-bot")


class EvidenceLedger:
    """Structured ledger of all verified facts permitted for a message."""

    def __init__(self) -> None:
        self.raw_facts: List[Tuple[str, Any]] = []
        self.allowed_numbers: Set[str] = set()
        self.allowed_offers: List[str] = []
        self.merchant_names: Set[str] = set()
        self.customer_names: Set[str] = set()
        self.category_names: Set[str] = set()
        self.anchors: Set[str] = set()


def _normalize_num(val: Any) -> Set[str]:
    """Generate string representations of a numeric value."""
    res = set()
    try:
        f = float(val)
        # Raw string
        res.add(str(val).strip())
        # Integer representation if whole
        if f.is_integer():
            res.add(str(int(f)))
        # Standard decimal formats
        res.add(f"{f:.1f}")
        res.add(f"{f:.2f}")
        # If float represents a percentage (e.g. 0.021 -> 2.1, 2.1%)
        if 0 < f < 1:
            pct = f * 100
            res.add(f"{pct:.1f}")
            res.add(f"{pct:.2f}")
            if pct.is_integer():
                res.add(str(int(pct)))
    except (ValueError, TypeError):
        pass
    return res


def build_evidence_ledger(compact_context: Dict[str, Any]) -> EvidenceLedger:
    """
    Extracts an evidence ledger from compact context:
    - Every numeric fact (metrics, percentages, counts, prices, days)
    - Active offer titles
    - Merchant, owner, customer, and category names
    - Clinical/research/festival anchor terms
    """
    ledger = EvidenceLedger()

    # 1. Allowed numbers
    # Single-digit common structural counts (e.g. 1 question, 1 page)
    ledger.allowed_numbers.add("1")

    # Merchant performance numbers
    merchant = compact_context.get("merchant", {})
    perf = merchant.get("performance", {})
    for k, v in perf.items():
        if v is not None:
            ledger.raw_facts.append((f"perf_{k}", v))
            ledger.allowed_numbers.update(_normalize_num(v))

    # Peer stats numbers
    category = compact_context.get("category", {})
    peer_avg_ctr = category.get("peer_avg_ctr")
    if peer_avg_ctr is not None:
        ledger.raw_facts.append(("peer_avg_ctr", peer_avg_ctr))
        ledger.allowed_numbers.update(_normalize_num(peer_avg_ctr))

    # Trigger payload numbers & anchors
    trigger = compact_context.get("trigger", {})
    trg_details = trigger.get("details", {})
    for k, v in trg_details.items():
        if isinstance(v, (int, float)):
            ledger.raw_facts.append((f"trigger_{k}", v))
            ledger.allowed_numbers.update(_normalize_num(v))
        elif isinstance(v, str):
            # Extract numbers from string values (e.g. "6_month_cleaning" -> 6)
            for num in re.findall(r"\b\d+(?:\.\d+)?\b", v):
                ledger.allowed_numbers.update(_normalize_num(num))
            ledger.anchors.add(v.strip().lower())

    # Active offers and prices
    offers = merchant.get("active_offers", [])
    for off in offers:
        ledger.allowed_offers.append(off.strip().lower())
        ledger.raw_facts.append(("offer", off))
        # Extract prices / numbers from offer string (e.g. ₹299 -> 299)
        for num in re.findall(r"\d+(?:\.\d+)?", off):
            ledger.allowed_numbers.update(_normalize_num(num))

    # 2. Proper Nouns & Names
    # Merchant & owner names
    STOP_NAME_WORDS = {
        "system", "prompt", "instructions", "ignore", "reveal", "override",
        "drop", "table", "merchants", "rules", "reasoning", "internal", "hacked",
        "pwned", "jailbreak", "filtered", "test", "your", "and", "the", "for", "with"
    }

    m_name = merchant.get("name")
    if m_name:
        clean_m_name = re.sub(r"(?i)(?:ignore\s+previous.*|system\s+override.*|\[system\].*|drop\s+table.*|--.*)", "", m_name).strip()
        if clean_m_name:
            ledger.merchant_names.add(clean_m_name.lower())
            for word in re.findall(r"\b[A-Za-z]{3,}\b", clean_m_name):
                w_lower = word.lower()
                if w_lower not in STOP_NAME_WORDS:
                    ledger.merchant_names.add(w_lower)

    m_owner = merchant.get("owner")
    if m_owner:
        clean_owner = re.sub(r"(?i)(?:system\s+override|\[system\])", "", m_owner).strip()
        if clean_owner:
            ledger.merchant_names.add(clean_owner.lower())

    # Category name
    c_name = category.get("name")
    if c_name:
        clean_c_name = re.sub(r"(?i)(?:;\s*drop\s+table.*|\[system\].*)", "", c_name).strip()
        if clean_c_name:
            ledger.category_names.add(clean_c_name.lower())
            for word in re.findall(r"\b[A-Za-z]{3,}\b", clean_c_name):
                w_lower = word.lower()
                if w_lower not in STOP_NAME_WORDS:
                    ledger.category_names.add(w_lower)
    c_slug = category.get("slug")
    if c_slug:
        ledger.category_names.add(c_slug.strip().lower())

    # Customer name
    customer = compact_context.get("customer")
    if customer and customer.get("name"):
        cust_name = customer["name"].strip()
        ledger.customer_names.add(cust_name.lower())
        for word in re.findall(r"\b[A-Za-z]{3,}\b", cust_name):
            ledger.customer_names.add(word.lower())

    # General anchors from allowed_facts
    for fact in compact_context.get("allowed_facts", []):
        for num in re.findall(r"\b\d+(?:\.\d+)?\b", fact):
            ledger.allowed_numbers.update(_normalize_num(num))

    return ledger


def extract_numbers_from_text(text: str) -> List[str]:
    """
    Extracts all numeric tokens from text, stripping currency and percent symbols.
    Examples: '₹299' -> '299', '2.1%' -> '2.1', '15' -> '15'.
    """
    # Match patterns like ₹499, Rs. 299, 2.1%, 15, etc.
    raw_tokens = re.findall(r"(?:₹|\$|Rs\.?\s*)?(\d+(?:\.\d+)?)(?:%)?", text, re.IGNORECASE)
    cleaned = []
    for tok in raw_tokens:
        tok_clean = tok.strip()
        if tok_clean:
            cleaned.append(tok_clean)
    return cleaned


def count_ctas(text: str) -> int:
    """
    Counts distinct CTAs in message.
    Considers questions ('?') and imperative action invitations.
    """
    # Primary CTA indicator: question mark
    q_count = text.count("?")

    # Additional imperative / command CTA phrases without '?'
    imperatives = [
        r"\breply\s+(?:yes|confirm|now)\b",
        r"\bclick\s+here\b",
        r"\bcall\s+us\s+now\b",
    ]
    imp_count = 0
    text_lower = text.lower()
    for imp in imperatives:
        if re.search(imp, text_lower):
            imp_count += 1

    return max(q_count, imp_count)


def validate_message(
    body: str,
    ledger: EvidenceLedger,
    conversation_store: Optional[Any] = None,
    conversation_id: Optional[str] = None,
) -> Tuple[bool, Optional[str]]:
    """
    Validates LLM-generated message body against the evidence ledger.

    Returns:
        (is_valid: bool, rejection_reason: Optional[str])
    """
    # 1. Non-empty check
    if not body or not body.strip():
        return False, "Message body cannot be empty."

    body_clean = body.strip()
    body_lower = body_clean.lower()

    # Prompt injection and jailbreak artifact check
    injection_artifacts = [
        "system override", "jailbreak", "dan mode", "ignore previous", "hacked", "pwned",
        "[filtered]", "drop table", "admin override", "system prompt", "system instructions",
        "internal reasoning", "rules:", "you are vera", "reveal your"
    ]
    for art in injection_artifacts:
        if art in body_lower:
            return False, f"Message contains prompt injection or security violation marker '{art}'."

    # 2. Duplicate check against ConversationStore
    if conversation_store is not None and conversation_id:
        conv = conversation_store.get(conversation_id)
        if conv and conv.get("sent_messages"):
            norm_body = " ".join(body_lower.split())
            body_hash = hashlib.sha256(norm_body.encode("utf-8")).hexdigest()

            for sent in conv["sent_messages"]:
                sent_body = sent.get("body", "") if isinstance(sent, dict) else str(sent)
                sent_norm = " ".join(sent_body.strip().lower().split())
                sent_hash = hashlib.sha256(sent_norm.encode("utf-8")).hexdigest()
                if body_hash == sent_hash:
                    return False, "Message is a duplicate of a previously sent message in this conversation."

    # 3. Numeric & Price grounding check
    extracted_nums = extract_numbers_from_text(body_clean)
    for num in extracted_nums:
        # Check if number matches ledger
        # Support both '3' matching '3.0' or '3.0' matching '3'
        num_variants = _normalize_num(num)
        if not (num_variants & ledger.allowed_numbers):
            return False, f"Unverified numeric claim '{num}' found in message."

    # 4. Invented Customer / Merchant Names check
    # Check for customer salutations addressing a third party (e.g. "Hi Rahul,", "Dear Priya,")
    salutation_match = re.search(r"\b(?:hi|hello|dear|hey)\s+([A-Z][a-z]+)\b", body_clean)
    if salutation_match:
        addressed_name = salutation_match.group(1).lower()
        if addressed_name not in ledger.customer_names and addressed_name not in ledger.merchant_names:
            return False, f"Invented customer or third-party name '{addressed_name}' not in context."

    # Common fabricated names test
    common_invented = ["priya", "rahul", "aanya", "sneha", "kavya", "rohit", "amit", "vikram", "dr. sharma", "dr. gupta"]
    for name in common_invented:
        if name in body_lower and name not in ledger.customer_names and name not in ledger.merchant_names:
            return False, f"Invented customer or third-party name '{name}' not in context."

    # 5. Offer / Product grounding check
    # Check for specific ungrounded services/procedures
    ungrounded_services = [
        "whitening", "root canal", "hair spa", "massage", "facial", "botox", "implants",
        "haircut", "buffet", "manicure", "pedicure", "laser"
    ]
    for srv in ungrounded_services:
        if srv in body_lower:
            if not any(srv in off for off in ledger.allowed_offers):
                return False, f"Message references an offer or service not present in active_offers."

    # If the message mentions an offer, package, service, or promotional phrase
    offer_indicators = ["offer", "package", "service", "deal", "discount", "treatment", "procedure", "promo", "special"]
    mentioned_offer = any(w in body_lower for w in offer_indicators)
    if mentioned_offer and ledger.allowed_offers:
        matched_any = False
        for off in ledger.allowed_offers:
            off_words = [w for w in re.findall(r"\b[a-zA-Z]{4,}\b", off) if w not in ("with", "from", "your")]
            if any(w in body_lower for w in off_words):
                matched_any = True
                break
        if not matched_any:
            return False, "Message references an offer or service not present in active_offers."

    # 6. Exactly one CTA check
    cta_count = count_ctas(body_clean)
    if cta_count == 0:
        return False, "Message must contain exactly one CTA (none found)."
    if cta_count > 1:
        return False, f"Message must contain exactly one CTA (found {cta_count})."

    # 7. Generic filler check (must contain at least one grounded fact from ledger)
    has_grounded_fact = False
    # Check for merchant name
    if any(m in body_lower for m in ledger.merchant_names if len(m) > 2):
        has_grounded_fact = True
    # Check for category name
    elif any(c in body_lower for c in ledger.category_names if len(c) > 2):
        has_grounded_fact = True
    # Check for customer name
    elif any(cust in body_lower for cust in ledger.customer_names if len(cust) > 2):
        has_grounded_fact = True
    # Check for offer title
    elif any(off in body_lower for off in ledger.allowed_offers):
        has_grounded_fact = True
    # Check for anchor terms (e.g. festival name, research topic)
    elif any(a in body_lower for a in ledger.anchors if len(a) > 2):
        has_grounded_fact = True
    # Check for allowed numeric metrics (excluding common '1')
    elif any(num in body_clean for num in ledger.allowed_numbers if num != "1"):
        has_grounded_fact = True

    if not has_grounded_fact:
        return False, "Message contains generic filler with no grounded facts from context."

    return True, None
