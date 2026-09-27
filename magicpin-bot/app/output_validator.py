"""Output grounding validator for Magicpin Vera bot.

Enforces strict grounding against an evidence ledger extracted from compact context.
Rejects hallucinated metrics, prices, percentages, offers, names, locations, dates,
unsupported claims, multiple CTAs, duplicate messages, and generic filler,
triggering deterministic fallback.
"""

import hashlib
import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("magicpin-bot")

# ---------------------------------------------------------------------------
# Structural Language Tokens (Permitted Conversational Idioms)
# ---------------------------------------------------------------------------
# These represent conversational scaffolding rather than factual claims.
STRUCTURAL_PATTERNS = [
    # 1-page / 1 page checklist/guide/summary
    r"\b1[\s-]page\b",
    # 3-step / 3 step checklist/action plan
    r"\b3[\s-]step\b",
    # top 3 recommendations/opportunities/priorities
    r"\btop\s+3\b",
    # 1 quick question / 1 question
    r"\b1\s+(?:quick\s+)?question\b",
    # 2-minute / 2 min read/abstract/summary
    r"\b2[\s-]min(?:ute)?s?\b",
    # step 1 / step 2 / step 3
    r"\bstep\s+[1-3]\b",
]

# Major Indian cities and localities monitored for location grounding
MAJOR_LOCATIONS = [
    "mumbai", "delhi", "bangalore", "bengaluru", "pune", "hyderabad",
    "chennai", "kolkata", "jaipur", "ahmedabad", "chandigarh", "lucknow",
    "gurgaon", "gurugram", "noida", "bandra", "koramangala", "indiranagar",
    "lajpat nagar", "connaught place", "hauz khas", "jayanagar", "andheri"
]

# Universal prohibited deceptive claims and extreme health superlatives
GLOBAL_PROHIBITED_CLAIMS = {
    "guaranteed", "100% safe", "miracle", "permanent cure",
    "painless guaranteed", "100% guaranteed", "#1 rated",
    "best clinic in", "cure guaranteed", "risk-free"
}

# Standard calendar months monitored for date grounding
CALENDAR_MONTHS = [
    "january", "february", "march", "april", "june",
    "july", "august", "september", "october", "november", "december"
]


# Security violation patterns covering:
# 1. Injected instructions
# 2. System-prompt extraction
# 3. Tool-invocation attempts
# 4. SQL fragments
# 5. Jailbreak text
SECURITY_VIOLATION_PATTERNS: List[Tuple[str, str]] = [
    # Injected instructions
    (r"(?i)\bignore\s+(?:all\s+)?(?:previous\s+)?instructions\b", "ignore previous"),
    (r"(?i)\bdisregard\s+(?:all\s+)?(?:previous\s+)?(?:instructions|rules|prompts)\b", "disregard instructions"),
    (r"(?i)\bforget\s+(?:all\s+)?(?:previous\s+)?(?:instructions|rules)\b", "forget instructions"),
    (r"(?i)\boverride\s+(?:all\s+)?instructions\b", "override instructions"),
    (r"(?i)\bnew\s+instructions\s*:", "new instructions:"),
    (r"(?i)\bsystem\s+override\b", "system override"),
    (r"(?i)\badmin\s+override\b", "admin override"),
    # System prompt extraction
    (r"(?i)\bsystem\s+prompt\b", "system prompt"),
    (r"(?i)\bsystem\s+instructions?\b", "system instructions"),
    (r"(?i)\binternal\s+reasoning\b", "internal reasoning"),
    (r"(?i)\binternal\s+instructions?\b", "internal instructions"),
    (r"(?i)\byou\s+are\s+vera\b", "you are vera"),
    (r"(?i)\brules\s*:", "rules:"),
    (r"(?i)\breveal\s+(?:your\s+)?(?:system\s+)?(?:prompt|instructions|rules)\b", "reveal your"),
    (r"(?i)\boutput\s+(?:your\s+)?(?:system\s+)?(?:prompt|instructions)\b", "output your prompt"),
    (r"(?i)\bprint\s+(?:your\s+)?(?:system\s+)?(?:prompt|instructions)\b", "print system prompt"),
    (r"(?i)\bshow\s+(?:your\s+)?(?:system\s+)?(?:prompt|instructions|rules)\b", "show your rules"),
    (r"(?i)\bwhat\s+is\s+your\s+system\s+prompt\b", "what is your system prompt"),
    (r"(?i)\bhere\s+is\s+(?:my|the)\s+system\s+prompt\b", "here is the system prompt"),
    # Tool invocation attempts
    (r"(?i)<\s*\/?\s*tool_call\s*>", "<tool_call>"),
    (r"(?i)<\s*\/?\s*tool_code\s*>", "<tool_code>"),
    (r"(?i)\bcall:default_api\b", "call:default_api"),
    (r"(?i)\bfunction_call\b", "function_call"),
    (r"(?i)\brun_command\b", "run_command"),
    (r"(?i)\bview_file\b", "view_file"),
    (r"(?i)\bwrite_to_file\b", "write_to_file"),
    (r"(?i)\breplace_file_content\b", "replace_file_content"),
    (r"(?i)\bmanage_task\b", "manage_task"),
    (r"(?i)\bexec\s*\(", "exec("),
    (r"(?i)\beval\s*\(", "eval("),
    (r"(?i)\b__import__\b", "__import__"),
    (r"(?i)\bsubprocess\b", "subprocess"),
    (r"(?i)```\s*(?:bash|sh|powershell|cmd|python)", "tool execution block"),
    # SQL fragments
    (r"(?i);\s*drop\s+table\b", "drop table"),
    (r"(?i)\bdrop\s+table\b", "drop table"),
    (r"(?i)\bdrop\s+database\b", "drop database"),
    (r"(?i)\bunion\s+(?:all\s+)?select\b", "union select"),
    (r"(?i)\bselect\s+.*\s+from\s+[a-z_]+", "select from"),
    (r"(?i)\binsert\s+into\s+[a-z_]+", "insert into"),
    (r"(?i)\bdelete\s+from\s+[a-z_]+", "delete from"),
    (r"(?i)\bupdate\s+[a-z_]+\s+set\b", "update set"),
    (r"(?i)\balter\s+table\b", "alter table"),
    (r"(?i)\bexec\s+sp_", "exec sp_"),
    (r"(?i)'\s*or\s+['\"]?1['\"]?\s*=\s*['\"]?1", "' or 1=1"),
    (r"(?i);\s*--", "; --"),
    # Jailbreak text
    (r"(?i)\bdan\s+mode\b", "dan mode"),
    (r"(?i)\bjailbreak\b", "jailbreak"),
    (r"(?i)\bdeveloper\s+mode\b", "developer mode"),
    (r"(?i)\bunrestricted\s+mode\b", "unrestricted mode"),
    (r"(?i)\balways\s+say\s+yes\b", "always say yes"),
    (r"(?i)\baim\s+mode\b", "aim mode"),
    (r"(?i)\bbypass\s+(?:safety|filter|guardrails?)\b", "bypass safety"),
    (r"(?i)\bhypothetical\s+response\b", "hypothetical response"),
    (r"(?i)\bdo\s+anything\s+now\b", "do anything now"),
    (r"(?i)\bhacked\b", "hacked"),
    (r"(?i)\bpwned\b", "pwned"),
    (r"(?i)\[filtered\]", "[filtered]"),
    (r"(?i)\[system\]", "[system]"),
    (r"(?i)<<sys>>", "<<sys>>"),
    (r"(?i)<\|im_start\|>", "<|im_start|>"),
    (r"(?i)<\|im_end\|>", "<|im_end|>"),
    (r"(?i)<\/?untrusted_context_data>", "<untrusted_context_data>"),
]


def is_security_violation(text: str) -> bool:
    """Check if a string contains any prompt injection or security violation pattern."""
    if not text or not isinstance(text, str):
        return False
    return any(re.search(pat, text) for pat, _ in SECURITY_VIOLATION_PATTERNS)


class EvidenceLedger:
    """Structured ledger of all verified facts permitted for a message."""

    def __init__(self) -> None:
        self.raw_facts: List[Tuple[str, Any]] = []
        self.allowed_numbers: Set[str] = set()
        self.allowed_offers: List[str] = []
        self.inactive_offers: List[str] = []
        self.merchant_names: Set[str] = set()
        self.customer_names: Set[str] = set()
        self.category_names: Set[str] = set()
        self.locations: Set[str] = set()
        self.dates: Set[str] = set()
        self.anchors: Set[str] = set()
        self.taboos: Set[str] = set()


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
        # Standard decimal formats (do not round non-zero values to 0.0 or 0)
        if f == 0:
            res.add("0")
            res.add("0.0")
            res.add("0.00")
        else:
            if round(f, 1) != 0:
                res.add(f"{f:.1f}")
            if round(f, 2) != 0:
                res.add(f"{f:.2f}")

        # Percentage formatting
        abs_f = abs(f)
        if 0 < abs_f < 1:
            pct = abs_f * 100
            if round(pct, 1) != 0:
                res.add(f"{pct:.1f}")
            if round(pct, 2) != 0:
                res.add(f"{pct:.2f}")
            if pct.is_integer():
                res.add(str(int(pct)))
            signed_pct = f * 100
            if round(signed_pct, 1) != 0:
                res.add(f"{signed_pct:.1f}")
            if signed_pct.is_integer():
                res.add(str(int(signed_pct)))
    except (ValueError, TypeError):
        pass
    return res


def build_evidence_ledger(compact_context: Any) -> EvidenceLedger:
    """
    Extracts an evidence ledger from compact context or ResolvedContext:
    - Every numeric fact from performance, delta_7d, customer_aggregate, peer_stats, digest
    - Active offer titles and prices (inactive offers tracked separately for negative filtering)
    - Merchant, owner, customer, and category names
    - Explicit locations (city, locality, state)
    - Clinical/research/digest citations, dates, sources, trial_n, and anchors
    - Taboos and prohibited claims
    """
    ledger = EvidenceLedger()

    # If a ResolvedContext is passed, convert to compact_context first
    if hasattr(compact_context, "trigger") and hasattr(compact_context, "merchant"):
        from app.composer import build_compact_context
        compact_context = build_compact_context(resolved_context=compact_context)

    # Automatically unwrap if envelope was passed directly
    if isinstance(compact_context, dict) and "payload" in compact_context and isinstance(compact_context["payload"], dict):
        compact_context = compact_context["payload"]

    # Global prohibited claims
    ledger.taboos.update(GLOBAL_PROHIBITED_CLAIMS)

    # 1. Merchant performance numbers (including delta_7d)
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
            # Extract numbers embedded in key names, e.g. lapsed_180d_plus -> 180, retention_6mo_pct -> 6
            for num in re.findall(r"\d+", k):
                ledger.allowed_numbers.add(num)
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

    # Category voice allowed vocab anchors and taboos
    voice = category.get("voice", {})
    if isinstance(voice, dict):
        vocab_allowed = voice.get("vocab_allowed", [])
        if isinstance(vocab_allowed, list):
            for term in vocab_allowed:
                if isinstance(term, str):
                    ledger.anchors.add(term.lower())
                    for num in re.findall(r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", term):
                        ledger.allowed_numbers.update(_normalize_num(num))
        # Taboos
        for tab in voice.get("taboos", []) + voice.get("vocab_taboo", []):
            if isinstance(tab, str) and tab.strip():
                ledger.taboos.add(tab.strip().lower())

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
            # Extract month/year dates from source
            for m_match in re.findall(r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b", source, re.IGNORECASE):
                ledger.dates.add(m_match.lower())
            for y_match in re.findall(r"\b(20\d{2})\b", source):
                ledger.dates.add(y_match)
                ledger.allowed_numbers.add(y_match)
        title = d.get("title")
        if title and isinstance(title, str):
            ledger.anchors.add(title.lower())
            for num in re.findall(r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", title):
                ledger.allowed_numbers.update(_normalize_num(num))
            for key_term in ("caries", "fluoride", "recall", "radiograph", "dose", "iopa", "rvg"):
                if key_term in title.lower():
                    ledger.anchors.add(key_term)
            for m_match in re.findall(r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b", title, re.IGNORECASE):
                ledger.dates.add(m_match.lower())
            for y_match in re.findall(r"\b(20\d{2})\b", title):
                ledger.dates.add(y_match)
                ledger.allowed_numbers.add(y_match)
        summary = d.get("summary")
        if summary and isinstance(summary, str):
            for num in re.findall(r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", summary):
                ledger.allowed_numbers.update(_normalize_num(num))
            for m_match in re.findall(r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b", summary, re.IGNORECASE):
                ledger.dates.add(m_match.lower())
            for y_match in re.findall(r"\b(20\d{2})\b", summary):
                ledger.dates.add(y_match)
                ledger.allowed_numbers.add(y_match)
        pseg = d.get("patient_segment")
        if pseg and isinstance(pseg, str):
            ledger.anchors.add(pseg.lower())
            ledger.anchors.add(pseg.replace("_", " ").lower())
            ledger.anchors.add(pseg.replace("_", "-").lower())

    # Trigger payload numbers, anchors, and dates
    trigger = compact_context.get("trigger", {})
    trg_details = trigger.get("details") or trigger.get("payload") or {}
    if isinstance(trg_details, dict):
        for k, v in trg_details.items():
            if k == "placeholder" or k.startswith("_"):
                continue
            if isinstance(v, (int, float)):
                ledger.raw_facts.append((f"trigger_{k}", v))
                ledger.allowed_numbers.update(_normalize_num(v))
                if k.endswith("_pct") or k.endswith("_percentage"):
                    ledger.allowed_numbers.update(_normalize_num(v * 100))
            elif isinstance(v, str):
                for num in re.findall(r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", v):
                    ledger.allowed_numbers.update(_normalize_num(num))
                clean_str = v.strip().lower()
                if clean_str:
                    ledger.anchors.add(clean_str)
                    for word in re.findall(r"\b[A-Za-z0-9_-]{3,}\b", clean_str):
                        ledger.anchors.add(word)
                for m_match in re.findall(r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b", clean_str, re.IGNORECASE):
                    ledger.dates.add(m_match.lower())
                for y_match in re.findall(r"\b(20\d{2})\b", clean_str):
                    ledger.dates.add(y_match)
                    ledger.allowed_numbers.add(y_match)
            elif isinstance(v, (list, tuple)):
                for item in v:
                    if isinstance(item, (int, float)):
                        ledger.allowed_numbers.update(_normalize_num(item))
                    elif isinstance(item, str):
                        for num in re.findall(r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", item):
                            ledger.allowed_numbers.update(_normalize_num(num))
                        clean_item = item.strip().lower()
                        if clean_item:
                            ledger.anchors.add(clean_item)
                            for word in re.findall(r"\b[A-Za-z0-9_-]{3,}\b", clean_item):
                                ledger.anchors.add(word)
                    elif isinstance(item, dict):
                        for sub_k, sub_v in item.items():
                            if isinstance(sub_v, (int, float)):
                                ledger.allowed_numbers.update(_normalize_num(sub_v))
                            elif isinstance(sub_v, str):
                                for num in re.findall(r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", sub_v):
                                    ledger.allowed_numbers.update(_normalize_num(num))
                                clean_sub = sub_v.strip().lower()
                                if clean_sub:
                                    ledger.anchors.add(clean_sub)
                                    for word in re.findall(r"\b[A-Za-z0-9_-]{3,}\b", clean_sub):
                                        ledger.anchors.add(word)

    # Dates from trigger headers
    for d_src in (trigger.get("expires_at"), trigger.get("created_at"), trigger.get("timestamp")):
        if d_src and isinstance(d_src, str):
            for m_match in re.findall(r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b", d_src, re.IGNORECASE):
                ledger.dates.add(m_match.lower())
            for y_match in re.findall(r"\b(20\d{2})\b", d_src):
                ledger.dates.add(y_match)
                ledger.allowed_numbers.add(y_match)
    if compact_context.get("now"):
        now_s = str(compact_context["now"])
        for y_match in re.findall(r"\b(20\d{2})\b", now_s):
            ledger.dates.add(y_match)
            ledger.allowed_numbers.add(y_match)

    # Active vs Inactive Offers and prices
    offers_raw = merchant.get("offers", [])
    if isinstance(offers_raw, list):
        for o in offers_raw:
            if isinstance(o, dict):
                title = o.get("title")
                status = o.get("status", "active")
                if title and isinstance(title, str):
                    clean_title = title.strip()
                    if status == "active":
                        if clean_title.lower() not in ledger.allowed_offers:
                            ledger.allowed_offers.append(clean_title.lower())
                            ledger.raw_facts.append(("offer", clean_title))
                            for num in re.findall(r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", clean_title):
                                ledger.allowed_numbers.update(_normalize_num(num))
                    elif status in ("expired", "inactive", "paused", "disabled"):
                        ledger.inactive_offers.append(clean_title.lower())

    for off in merchant.get("active_offers", []):
        if isinstance(off, str):
            clean_off = off.strip()
            if clean_off.lower() not in ledger.allowed_offers:
                ledger.allowed_offers.append(clean_off.lower())
                ledger.raw_facts.append(("offer", clean_off))
                for num in re.findall(r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", clean_off):
                    ledger.allowed_numbers.update(_normalize_num(num))

    # Proper Nouns & Names
    STOP_NAME_WORDS = {
        "system", "prompt", "instructions", "ignore", "reveal", "override",
        "drop", "table", "merchants", "rules", "reasoning", "internal", "hacked",
        "pwned", "jailbreak", "filtered", "test", "your", "and", "the", "for", "with",
        "select", "union", "delete", "insert", "update", "tool", "call", "eval", "exec",
        "function", "admin", "developer", "mode", "bypass", "untrusted", "context",
        "database", "alter", "disregard", "forget"
    }

    m_name = merchant.get("name") or (merchant.get("identity", {}).get("name") if isinstance(merchant.get("identity"), dict) else None)
    if m_name:
        clean_m_name = re.sub(
            r"(?i)(?:ignore\s+previous.*|system\s+override.*|\[system\].*|drop\s+table.*|--.*|union\s+select.*|<\s*\/?tool_call.*|call:default_api.*|exec\s*\(.*|eval\s*\(.*|dan\s+mode.*|jailbreak.*)",
            "",
            m_name,
        ).strip()
        if clean_m_name:
            ledger.merchant_names.add(clean_m_name.lower())
            for word in re.findall(r"\b[A-Za-z]{3,}\b", clean_m_name):
                w_lower = word.lower()
                if w_lower not in STOP_NAME_WORDS:
                    ledger.merchant_names.add(w_lower)

    m_owner = (
        merchant.get("owner")
        or merchant.get("owner_first_name")
        or (merchant.get("identity", {}).get("owner_first_name") if isinstance(merchant.get("identity"), dict) else None)
        or (merchant.get("identity", {}).get("owner") if isinstance(merchant.get("identity"), dict) else None)
    )
    if m_owner:
        clean_owner = re.sub(
            r"(?i)(?:system\s+override|\[system\]|ignore\s+previous.*|drop\s+table.*|--.*|union\s+select.*|<\s*\/?tool_call.*|call:default_api.*)",
            "",
            str(m_owner),
        ).strip()
        if clean_owner:
            owner_lower = clean_owner.lower()
            ledger.merchant_names.add(owner_lower)
            ledger.merchant_names.add(f"dr. {owner_lower}")
            for word in re.findall(r"\b[A-Za-z]{3,}\b", clean_owner):
                w_lower = word.lower()
                if w_lower not in STOP_NAME_WORDS:
                    ledger.merchant_names.add(w_lower)
                    ledger.merchant_names.add(f"dr. {w_lower}")

    # Locations
    m_city = merchant.get("city") or (merchant.get("identity", {}).get("city") if isinstance(merchant.get("identity"), dict) else None)
    if m_city and isinstance(m_city, str):
        c_clean = m_city.strip().lower()
        ledger.locations.add(c_clean)
        ledger.anchors.add(c_clean)
        for w in re.findall(r"\b[A-Za-z]{3,}\b", c_clean):
            ledger.locations.add(w)

    m_locality = merchant.get("locality") or (merchant.get("identity", {}).get("locality") if isinstance(merchant.get("identity"), dict) else None)
    if m_locality and isinstance(m_locality, str):
        l_clean = m_locality.strip().lower()
        ledger.locations.add(l_clean)
        ledger.anchors.add(l_clean)
        for w in re.findall(r"\b[A-Za-z]{3,}\b", l_clean):
            ledger.locations.add(w)

    m_state = merchant.get("state") or (merchant.get("identity", {}).get("state") if isinstance(merchant.get("identity"), dict) else None)
    if m_state and isinstance(m_state, str):
        s_clean = m_state.strip().lower()
        ledger.locations.add(s_clean)
        ledger.anchors.add(s_clean)
        for w in re.findall(r"\b[A-Za-z]{3,}\b", s_clean):
            ledger.locations.add(w)

    # Trigger city (e.g. weather/event city)
    trg_city = trg_details.get("city")
    if trg_city and isinstance(trg_city, str):
        tc_clean = trg_city.strip().lower()
        ledger.locations.add(tc_clean)
        ledger.anchors.add(tc_clean)
        for w in re.findall(r"\b[A-Za-z]{3,}\b", tc_clean):
            ledger.locations.add(w)

    # Category name
    c_name = category.get("name") or category.get("display_name")
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
        cust_name = customer.get("name") or (customer.get("identity", {}).get("name") if isinstance(customer.get("identity"), dict) else None)
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


def extract_numbers_from_text(text: str, exclude_structural: bool = False) -> List[str]:
    """
    Extracts all numeric tokens from text, stripping currency and percent symbols.
    Supports comma-formatted numbers like '2,100'.
    Examples: '₹299' -> '299', '2.1%' -> '2.1', '2,100' -> '2,100'.

    If exclude_structural is True, numbers that are strictly part of structural
    scaffolding tokens (e.g. '1-page', '3-step', 'top 3', '2-min') are excluded.
    """
    structural_spans: List[Tuple[int, int]] = []
    if exclude_structural:
        for pat in STRUCTURAL_PATTERNS:
            for sm in re.finditer(pat, text, re.IGNORECASE):
                structural_spans.append((sm.start(), sm.end()))

    matches = list(re.finditer(
        r"(?:₹|\$|Rs\.?\s*)?((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)(?:%)?",
        text,
        re.IGNORECASE,
    ))
    cleaned = []
    for m in matches:
        tok_clean = m.group(1).strip()
        if not tok_clean:
            continue
        if exclude_structural:
            m_start, m_end = m.start(1), m.end(1)
            if any(s_start <= m_start and m_end <= s_end for s_start, s_end in structural_spans):
                continue
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

    # Prompt injection, system-prompt extraction, tool-invocation, SQL fragment, and jailbreak check
    for pat, label in SECURITY_VIOLATION_PATTERNS:
        if re.search(pat, body_clean):
            return False, f"Message contains prompt injection or security violation marker '{label}'."

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

    # 3. Category Taboos & Deceptive Claims check
    for tab in ledger.taboos:
        if re.search(rf"\b{re.escape(tab)}\b", body_lower):
            return False, f"Message contains prohibited claim or taboo '{tab}'."

    # 4. Inactive / Expired Offers check
    for inact in ledger.inactive_offers:
        clean_inact = inact.lower()
        # Strip price portion, e.g. "deep cleaning @ ₹499" -> "deep cleaning"
        inact_name = re.sub(r"@.*|₹.*|\$.*", "", clean_inact).strip()
        if (inact_name and re.search(rf"\b{re.escape(inact_name)}\b", body_lower)) or (clean_inact in body_lower):
            if not any(inact_name in active_off for active_off in ledger.allowed_offers):
                return False, f"Message references an inactive or expired offer: '{inact}'."

    # 5. Offer / Product grounding check
    ungrounded_services = [
        "whitening", "root canal", "hair spa", "massage", "facial", "botox", "implants",
        "haircut", "buffet", "manicure", "pedicure", "laser"
    ]
    for srv in ungrounded_services:
        if re.search(rf"\b{srv}\b", body_lower):
            if not any(srv in off for off in ledger.allowed_offers) and not any(srv in anchor for anchor in ledger.anchors):
                return False, f"Message references an offer or service not present in active_offers."

    offer_indicators = [
        r"\boffer(?:ing|s)?\b",
        r"\bfree\b",
        r"\bdeal(?:s)?\b",
        r"\bpackage(?:s)?\b",
        r"\bdiscount(?:s)?\b",
        r"\bpromo(?:tion)?(?:s)?\b",
        r"\bspecial(?:s)?\b",
    ]
    mentioned_offer = any(re.search(p, body_lower) for p in offer_indicators)
    if mentioned_offer:
        if ledger.allowed_offers:
            matched_any = False
            for off in ledger.allowed_offers:
                off_words = [w for w in re.findall(r"\b[a-zA-Z]{4,}\b", off) if w not in ("with", "from", "your", "offer")]
                if any(w in body_lower for w in off_words):
                    matched_any = True
                    break
            if not matched_any:
                return False, "Message references an offer or service not present in active_offers."

    # 6. Location Grounding check
    for loc in MAJOR_LOCATIONS:
        if re.search(rf"\b{re.escape(loc)}\b", body_lower):
            if loc not in ledger.locations and not any(loc in l for l in ledger.locations) and loc not in ledger.anchors:
                return False, f"Unsupported location or city '{loc}' not in merchant context."

    # 7. Dates & Months Grounding check
    for m in CALENDAR_MONTHS:
        if re.search(rf"\b{m}\b", body_lower):
            if m not in ledger.dates and not any(m.startswith(d) or d.startswith(m) for d in ledger.dates) and m not in ledger.anchors:
                return False, f"Unsupported date or month claim '{m}' not in context."

    if re.search(r"\b(?:in\s+may|of\s+may|may\s+\d{1,2}|\d{1,2}(?:st|nd|rd|th)?\s+may|may\s+20\d{2})\b", body_lower):
        if "may" not in ledger.dates and not any("may" in d for d in ledger.dates) and "may" not in ledger.anchors:
            return False, "Unsupported date or month claim 'may' not in context."

    for yr in re.findall(r"\b(20\d{2})\b", body_clean):
        if yr not in ledger.dates and yr not in ledger.allowed_numbers and yr not in ledger.anchors:
            return False, f"Unsupported year '{yr}' not in context."

    # 8. Invented Customer / Merchant Names check
    salutation_match = re.search(r"\b(?:hi|hello|dear|hey)\s+(?:dr\.?\s+)?([A-Za-z]+(?:\s+[A-Za-z]+)?)\b", body_clean, re.IGNORECASE)
    if salutation_match:
        addressed_full = salutation_match.group(1).lower().strip()
        addressed_words = addressed_full.split()
        if not any(w in ledger.customer_names or w in ledger.merchant_names for w in addressed_words):
            return False, f"Invented customer or third-party name '{addressed_full}' not in context."

    for dr_m in re.finditer(r"\bdr\.?\s+([A-Za-z]+)\b", body_clean, re.IGNORECASE):
        doc_name = dr_m.group(1).lower().strip()
        if doc_name not in ledger.merchant_names and doc_name not in ledger.anchors and f"dr. {doc_name}" not in ledger.merchant_names:
            return False, f"Invented customer or third-party name 'dr. {doc_name}' not in context."

    common_invented = [
        "priya", "rahul", "aanya", "sneha", "kavya", "rohit", "amit", "vikram",
        "arjun", "neha", "pooja", "rajesh", "suresh", "ananya", "rohan", "neelam",
        "karan", "sunil", "anil", "deepak", "manish", "sanjay", "gupta", "sharma",
        "patel", "verma", "singh", "kapoor", "mehta", "reddy"
    ]
    for name in common_invented:
        if re.search(rf"\b{re.escape(name)}\b", body_lower):
            if name not in ledger.customer_names and name not in ledger.merchant_names and name not in ledger.anchors:
                return False, f"Invented customer or third-party name '{name}' not in context."

    # 9. Numeric, Price, Percentage, Metric Grounding check
    # Exclude structurally justified numbers (1-page, 3-step, top 3, 2-min, 1 question)
    extracted_nums = extract_numbers_from_text(body_clean, exclude_structural=True)
    for num in extracted_nums:
        num_variants = _normalize_num(num)
        if not (num_variants & ledger.allowed_numbers):
            return False, f"Unverified numeric claim '{num}' found in message."

    # 10. Exactly one CTA check
    cta_count = count_ctas(body_clean)
    if cta_count == 0:
        return False, "Message must contain exactly one CTA (none found)."
    if cta_count > 1:
        return False, f"Message must contain exactly one CTA (found {cta_count})."

    # 11. Generic filler check (must contain at least one grounded fact from ledger)
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
    elif any(num in body_clean for num in ledger.allowed_numbers):
        has_grounded_fact = True

    if not has_grounded_fact:
        return False, "Message contains generic filler with no grounded facts from context."

    return True, None
