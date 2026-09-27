"""
magicpin AI Challenge - Submission Module: bot.py

Canonical submission entrypoint providing the required compose() function.
"""
from typing import Optional, Dict, Any
from app.composer import compose as app_compose


def compose(
    category: Dict[str, Any],
    merchant: Dict[str, Any],
    trigger: Dict[str, Any],
    customer: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Inputs are the dicts loaded from the dataset JSON.
    Returns a dict with keys: body, cta, send_as, suppression_key, rationale.
    """
    return app_compose(
        category=category,
        merchant=merchant,
        trigger=trigger,
        customer=customer,
    )
