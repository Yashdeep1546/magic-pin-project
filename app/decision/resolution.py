"""Context resolution helpers for merchant, category, customer, and trigger."""

from typing import Any, Dict, Optional

from app.models import ResolvedContext


def resolve_merchant(context_store, merchant_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """Retrieve merchant payload from ContextStore."""
    if not merchant_id or not context_store:
        return None
    item = context_store.get("merchant", merchant_id)
    if not item or "payload" not in item:
        return None
    payload = item["payload"]
    return payload if isinstance(payload, dict) else None


def resolve_category(context_store, category_slug: Optional[str]) -> Optional[Dict[str, Any]]:
    """Retrieve category payload from ContextStore."""
    if not category_slug or not context_store:
        return None
    item = context_store.get("category", category_slug)
    if not item or "payload" not in item:
        return None
    payload = item["payload"]
    return payload if isinstance(payload, dict) else None


def resolve_customer(
    context_store,
    customer_id: Optional[str],
    merchant_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Retrieve customer payload from ContextStore with merchant scoping and ownership verification."""
    if not customer_id or not context_store:
        return None
    cust_item = None
    if merchant_id:
        cust_item = context_store.get("customer", f"{merchant_id}:{customer_id}")
        if not cust_item:
            cust_item = context_store.get("customer", f"{customer_id}_for_{merchant_id}")
    if not cust_item:
        cust_item = context_store.get("customer", customer_id)

    if not cust_item or "payload" not in cust_item:
        return None
    payload = cust_item["payload"]
    if isinstance(payload, dict):
        cust_m_id = payload.get("merchant_id") or payload.get("relationship", {}).get("primary_merchant_id")
        if cust_m_id and merchant_id and cust_m_id != merchant_id:
            return None
        return payload
    return None


def resolve_trigger(context_store, trigger_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """Retrieve trigger payload from ContextStore."""
    if not trigger_id or not context_store:
        return None
    item = context_store.get("trigger", trigger_id)
    if not item or "payload" not in item:
        return None
    payload = item["payload"]
    if isinstance(payload, dict):
        if "id" not in payload:
            payload["id"] = trigger_id
        return payload
    return None


def resolve_context(context_store, trigger_input: Any) -> Optional[ResolvedContext]:
    """
    Normalizes trigger, merchant, category, customer, versions, and relationship metadata
    into a single validated ResolvedContext object.
    Returns None if essential trigger or merchant context is missing.
    """
    if not trigger_input or not context_store:
        return None

    versions: Dict[str, Optional[int]] = {}
    metadata: Dict[str, Any] = {}

    if isinstance(trigger_input, str):
        trg_item = context_store.get("trigger", trigger_input)
        if not trg_item or not isinstance(trg_item.get("payload"), dict):
            return None
        trigger = dict(trg_item["payload"])
        if "id" not in trigger:
            trigger["id"] = trigger_input
        versions["trigger"] = trg_item.get("version")
    elif isinstance(trigger_input, dict):
        if "payload" in trigger_input and isinstance(trigger_input["payload"], dict) and "kind" not in trigger_input:
            trigger = dict(trigger_input["payload"])
            versions["trigger"] = trigger_input.get("version")
        else:
            trigger = dict(trigger_input)
            trg_id = trigger.get("id")
            if trg_id:
                t_raw = context_store.get("trigger", trg_id)
                if t_raw:
                    versions["trigger"] = t_raw.get("version")
    else:
        return None

    # Resolve merchant
    merchant_id = trigger.get("merchant_id")
    if not merchant_id:
        return None
    merchant: Optional[Dict[str, Any]] = None
    m_item = context_store.get("merchant", merchant_id)
    if m_item and isinstance(m_item.get("payload"), dict):
        merchant = m_item["payload"]
        versions["merchant"] = m_item.get("version")
    else:
        return None

    # Resolve category
    m_cat_slug = merchant.get("category_slug") if merchant else None
    trg_cat_slug = trigger.get("payload", {}).get("category")
    category_slug = m_cat_slug or trg_cat_slug

    category: Optional[Dict[str, Any]] = None
    if category_slug:
        c_item = context_store.get("category", category_slug)
        if c_item and isinstance(c_item.get("payload"), dict):
            category = c_item["payload"]
            versions["category"] = c_item.get("version")

    # Check category mismatch
    if m_cat_slug and trg_cat_slug and m_cat_slug != trg_cat_slug:
        metadata["category_mismatch"] = True
        metadata["merchant_category"] = m_cat_slug
        metadata["trigger_category"] = trg_cat_slug
        metadata["resolved_category"] = category_slug

    # Resolve customer
    customer_id = trigger.get("customer_id")
    customer: Optional[Dict[str, Any]] = None
    if customer_id:
        cust_item = None
        if merchant_id:
            cust_item = context_store.get("customer", f"{merchant_id}:{customer_id}")
            if not cust_item:
                cust_item = context_store.get("customer", f"{customer_id}_for_{merchant_id}")
        if not cust_item:
            cust_item = context_store.get("customer", customer_id)

        if cust_item and isinstance(cust_item.get("payload"), dict):
            customer = cust_item["payload"]
            versions["customer"] = cust_item.get("version")

    # Cross-merchant customer isolation check
    if customer and merchant_id:
        cust_m_id = customer.get("merchant_id") or customer.get("relationship", {}).get("primary_merchant_id")
        if cust_m_id and cust_m_id != merchant_id:
            metadata["cross_merchant_customer_mismatch"] = True

    return ResolvedContext(
        trigger=trigger,
        merchant=merchant,
        category=category,
        customer=customer,
        versions=versions,
        metadata=metadata,
    )


__all__ = [
    "resolve_merchant",
    "resolve_category",
    "resolve_customer",
    "resolve_trigger",
    "resolve_context",
]
