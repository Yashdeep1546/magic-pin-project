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
    """Generate string representations of a numeric value, supporting comma-formatting and percentages."""
    res = set()
    if val is None:
        return res
    s_val = str(val).strip().replace(",", "")
    try:
        f = float(s_val)
        # Raw string and clean string
        res.add(s_val)
        # Integer representation if whole
        if f.is_integer():
            int_val = int(f)
            res.add(str(int_val))
            res.add(f"{int_val:,}")  # e.g. "2,100"
        # Standard decimal formats
        res.add(f"{f:.1f}")
        res.add(f"{f:.2f}")
        # Percentage formatting
        abs_f = abs(f)
        if 0 < abs_f < 1:
            pct = abs_f * 100
            res.add(f"{pct:.1f}")
            res.add(f"{pct:.2f}")
            if pct.is_integer():
                res.add(str(int(pct)))
            signed_pct = f * 100
            res.add(f"{signed_pct:.1f}")
            if signed_pct.is_integer():
                res.add(str(int(signed_pct)))
    except (ValueError, TypeError):
        pass
    return res


def build_evidence_ledger(compact_context: Dict[str, Any]) -> EvidenceLedger:
    """
    Extracts an evidence ledger from compact context:
    - Every numeric fact from performance, delta_7d, customer_aggregate, peer_stats, digest
    - Active offer titles and prices
    - Merchant, owner, customer, and category names
    - Clinical/research/digest citations (source + trial_n) and anchors
    """
    ledger = EvidenceLedger()

    # Automatically unwrap if envelope was passed directly
    if isinstance(compact_context, dict) and "payload" in compact_context and isinstance(compact_context["payload"], dict):
        compact_context = compact_context["payload"]

    # 1. Allowed numbers
    # Common conversational structural counts (e.g. 1 question, 2-min abstract, 3-mo recall, 2 slots)
    ledger.allowed_numbers.update({"1", "2", "3", "4", "5"})

    # Merchant performance numbers (including delta_7d)
    merchant = compact_context.get("merchant", {})
    perf = merchant.get("performance", {})
    if isinstance(perf, dict):
        for k, v in perf.items():
            if k == "delta_7d" and isinstance(v, dict):
                ledger.allowed_numbers.add("7")
                for dk, dv in v.items():
                    if dv is not None:
                        ledger.raw_facts.append((f"perf_delta_{dk}", dv))
                        ledger.allowed_numbers.update(_normalize_num(dv))
            elif v is not None and isinstance(v, (int, float)):
                ledger.raw_facts.append((f"perf_{k}", v))
                ledger.allowed_numbers.update(_normalize_num(v))

    # Merchant customer aggregate numbers
    cust_agg = merchant.get("customer_aggregate", {})
    if isinstance(cust_agg, dict):
        for k, v in cust_agg.items():
            if isinstance(v, (int, float)):
                ledger.raw_facts.append((f"cust_agg_{k}", v))
                ledger.allowed_numbers.update(_normalize_num(v))
        if cust_agg.get("high_risk_adult_count") is not None:
            ledger.anchors.update({"high_risk_adults", "high-risk adult", "high-risk", "high risk"})

    # Merchant signals
    signals = merchant.get("signals", [])
    if isinstance(signals, list):
        for sig in signals:
            if isinstance(sig, str):
                ledger.anchors.add(sig.lower())
                ledger.anchors.add(sig.replace("_", " ").replace(":", " ").lower())
                for num in re.findall(r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", sig):
                    ledger.allowed_numbers.update(_normalize_num(num))

    # Peer stats numbers
    category = compact_context.get("category", {})
    peer_stats = category.get("peer_stats", {})
    if isinstance(peer_stats, dict):
        for pk, pv in peer_stats.items():
            if isinstance(pv, (int, float)):
                ledger.raw_facts.append((f"peer_{pk}", pv))
                ledger.allowed_numbers.update(_normalize_num(pv))
    peer_avg_ctr = category.get("peer_avg_ctr")
    if peer_avg_ctr is not None:
        ledger.raw_facts.append(("peer_avg_ctr", peer_avg_ctr))
        ledger.allowed_numbers.update(_normalize_num(peer_avg_ctr))

    # Category voice allowed vocab anchors
    voice = category.get("voice", {})
    if isinstance(voice, dict):
        vocab_allowed = voice.get("vocab_allowed", [])
        if isinstance(vocab_allowed, list):
            for term in vocab_allowed:
                if isinstance(term, str):
                    ledger.anchors.add(term.lower())
                    for num in re.findall(r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", term):
                        ledger.allowed_numbers.update(_normalize_num(num))

    # Digest citations and facts
    digests_to_scan = []
    if compact_context.get("digest"):
        digests_to_scan.append(compact_context["digest"])
    if isinstance(category.get("digest"), list):
        digests_to_scan.extend([d for d in category["digest"] if isinstance(d, dict)])
    for d in digests_to_scan:
        trial_n = d.get("trial_n")
        if trial_n is not None:
            ledger.raw_facts.append(("digest_trial_n", trial_n))
            ledger.allowed_numbers.update(_normalize_num(trial_n))
        source = d.get("source")
        if source and isinstance(source, str):
            ledger.anchors.add(source.lower())
            for tok in re.findall(r"\b[A-Za-z0-9\.\-]+\b", source):
                if len(tok) >= 2:
                    ledger.anchors.add(tok.lower())
            for num in re.findall(r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", source):
                ledger.allowed_numbers.update(_normalize_num(num))
        title = d.get("title")
        if title and isinstance(title, str):
            ledger.anchors.add(title.lower())
            for num in re.findall(r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", title):
                ledger.allowed_numbers.update(_normalize_num(num))
            for key_term in ("caries", "fluoride", "recall", "radiograph", "dose", "iopa", "rvg"):
                if key_term in title.lower():
                    ledger.anchors.add(key_term)
        summary = d.get("summary")
        if summary and isinstance(summary, str):
            for num in re.findall(r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", summary):
                ledger.allowed_numbers.update(_normalize_num(num))
        pseg = d.get("patient_segment")
        if pseg and isinstance(pseg, str):
            ledger.anchors.add(pseg.lower())
            ledger.anchors.add(pseg.replace("_", " ").lower())
            ledger.anchors.add(pseg.replace("_", "-").lower())

    # Trigger payload numbers & anchors
    trigger = compact_context.get("trigger", {})
    trg_details = trigger.get("details", {})
    if isinstance(trg_details, dict):
        for k, v in trg_details.items():
            if isinstance(v, (int, float)):
                ledger.raw_facts.append((f"trigger_{k}", v))
                ledger.allowed_numbers.update(_normalize_num(v))
            elif isinstance(v, str):
                for num in re.findall(r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", v):
                    ledger.allowed_numbers.update(_normalize_num(num))
                ledger.anchors.add(v.strip().lower())

    # Active offers and prices
    offers = merchant.get("active_offers", [])
    if not offers and isinstance(merchant.get("offers"), list):
        offers = [
            o.get("title")
            for o in merchant.get("offers", [])
            if isinstance(o, dict) and o.get("status") == "active" and o.get("title")
        ]
    for off in offers:
        if isinstance(off, str):
            ledger.allowed_offers.append(off.strip().lower())
            ledger.raw_facts.append(("offer", off))
            for num in re.findall(r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", off):
                ledger.allowed_numbers.update(_normalize_num(num))

    # 2. Proper Nouns & Names
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
            ledger.merchant_names.add(f"dr. {clean_owner.lower()}")

    m_city = merchant.get("city")
    if m_city and isinstance(m_city, str):
        ledger.anchors.add(m_city.strip().lower())

    m_locality = merchant.get("locality")
    if m_locality and isinstance(m_locality, str):
        ledger.anchors.add(m_locality.strip().lower())
        for word in re.findall(r"\b[A-Za-z]{3,}\b", m_locality):
            ledger.anchors.add(word.lower())

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

    # Customer name & relationship
    customer = compact_context.get("customer")
    if isinstance(customer, dict):
        cust_name = customer.get("name")
        if cust_name:
            cname_str = cust_name.strip()
            ledger.customer_names.add(cname_str.lower())
            for word in re.findall(r"\b[A-Za-z]{3,}\b", cname_str):
                ledger.customer_names.add(word.lower())
        rel = customer.get("relationship", {})
        if isinstance(rel, dict):
            for rk, rv in rel.items():
                if isinstance(rv, (int, float)):
                    ledger.allowed_numbers.update(_normalize_num(rv))
                elif isinstance(rv, str):
                    for num in re.findall(r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", rv):
                        ledger.allowed_numbers.update(_normalize_num(num))
                elif isinstance(rv, list):
                    for item in rv:
                        if isinstance(item, str):
                            ledger.anchors.add(item.lower())

    # General anchors and numbers from allowed_facts
    for fact in compact_context.get("allowed_facts", []):
        for num in re.findall(r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", fact):
            ledger.allowed_numbers.update(_normalize_num(num))

    return ledger


def extract_numbers_from_text(text: str) -> List[str]:
    """
    Extracts all numeric tokens from text, stripping currency and percent symbols.
    Supports comma-formatted numbers like '2,100'.
    Examples: '₹299' -> '299', '2.1%' -> '2.1', '2,100' -> '2,100'.
    """
    raw_tokens = re.findall(
        r"(?:₹|\$|Rs\.?\s*)?((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)(?:%)?",
        text,
        re.IGNORECASE,
    )
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
    q_count = text.count("?")

    imperatives = [
        r"\breply\s+(?:yes|confirm|now|start|proceed|go|launch)\b",
        r"\bsay\s+(?:yes|go)\b",
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
        num_variants = _normalize_num(num)
        if not (num_variants & ledger.allowed_numbers):
            return False, f"Unverified numeric claim '{num}' found in message."

    # 4. Invented Customer / Merchant Names check
    salutation_match = re.search(r"\b(?:hi|hello|dear|hey)\s+([A-Z][a-z]+)\b", body_clean)
    if salutation_match:
        addressed_name = salutation_match.group(1).lower()
        if addressed_name not in ledger.customer_names and addressed_name not in ledger.merchant_names:
            return False, f"Invented customer or third-party name '{addressed_name}' not in context."

    common_invented = ["priya", "rahul", "aanya", "sneha", "kavya", "rohit", "amit", "vikram", "dr. sharma", "dr. gupta"]
    for name in common_invented:
        if name in body_lower and name not in ledger.customer_names and name not in ledger.merchant_names:
            return False, f"Invented customer or third-party name '{name}' not in context."

    # 5. Offer / Product grounding check
    ungrounded_services = [
        "whitening", "root canal", "hair spa", "massage", "facial", "botox", "implants",
        "haircut", "buffet", "manicure", "pedicure", "laser"
    ]
    for srv in ungrounded_services:
        if srv in body_lower:
            if not any(srv in off for off in ledger.allowed_offers) and not any(srv in anchor for anchor in ledger.anchors):
                return False, f"Message references an offer or service not present in active_offers."

    offer_indicators = ["offer", "package", "deal", "discount", "promo", "special"]
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
    if any(m in body_lower for m in ledger.merchant_names if len(m) > 2):
        has_grounded_fact = True
    elif any(c in body_lower for c in ledger.category_names if len(c) > 2):
        has_grounded_fact = True
    elif any(cust in body_lower for cust in ledger.customer_names if len(cust) > 2):
        has_grounded_fact = True
    elif any(off in body_lower for off in ledger.allowed_offers):
        has_grounded_fact = True
    elif any(a in body_lower for a in ledger.anchors if len(a) > 2):
        has_grounded_fact = True
    elif any(num in body_clean for num in ledger.allowed_numbers if num not in ("1", "2", "3", "4", "5")):
        has_grounded_fact = True

    if not has_grounded_fact:
        return False, "Message contains generic filler with no grounded facts from context."

    return True, None
