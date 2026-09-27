"""
Tests for hard Python guardrails:
1. Intercept and rewrite mismatched triggers (e.g. chronic_refill_due for gyms/restaurants -> regular_customer_re_engagement).
2. Customer context isolation when customer is None vs present.
3. bot.py compose interface adherence.
"""
import pytest
from app.composer import compose, build_compact_context
from bot import compose as bot_compose


def test_chronic_refill_due_rewritten_for_gym():
    merchant = {
        "merchant_id": "m_036_roshni_gym_ahmedabad",
        "category_slug": "gyms",
        "identity": {"name": "Active Life Gym", "owner_first_name": "Roshni"},
    }
    category = {"slug": "gyms", "display_name": "Gyms"}
    trigger = {
        "id": "trg_082_chronic_refill_due_m_036_roshni_gym_ahm",
        "kind": "chronic_refill_due",
        "merchant_id": "m_036_roshni_gym_ahmedabad",
        "payload": {"metric_or_topic": "chronic_refill_due"},
    }
    customer = {
        "identity": {"name": "Ira"},
        "relationship": {"last_visit": "2026-04-01"},
    }

    result = bot_compose(
        category=category,
        merchant=merchant,
        trigger=trigger,
        customer=customer,
    )
    assert result["body"]
    # Check that it did NOT use pharmacy phrasing like prescription refill
    assert "refill" not in result["body"].lower() or "renewal" in result["body"].lower()
    assert "Active Life Gym" in result["body"]


def test_customer_none_isolation():
    merchant = {
        "merchant_id": "m_039_karan_gym_jaipur",
        "category_slug": "gyms",
        "identity": {"name": "FitLife Jaipur", "owner_first_name": "Karan"},
    }
    category = {"slug": "gyms", "display_name": "Gyms"}
    trigger = {
        "id": "trg_095_renewal_due_m_039_karan_gym_jaip",
        "kind": "renewal_due",
        "merchant_id": "m_039_karan_gym_jaipur",
        "scope": "merchant",
        "customer_id": None,
        "payload": {"metric_or_topic": "renewal_due"},
    }

    compact = build_compact_context(
        merchant=merchant,
        category=category,
        trigger=trigger,
        customer=None,
    )
    assert compact["customer"] is None
    assert "NO SPECIFIC CUSTOMER" in compact["customer_context"]

    result = bot_compose(
        category=category,
        merchant=merchant,
        trigger=trigger,
        customer=None,
    )
    assert result["body"]
    assert "Ira" not in result["body"]
    assert "2026-04-01" not in result["body"]
