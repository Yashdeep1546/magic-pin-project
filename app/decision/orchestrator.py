"""Tick decision orchestrator and proactive pipeline."""

import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.composer import build_compact_context, compose_message
from app.decision.resolution import resolve_context
from app.decision.scoring import is_trigger_expired, score_trigger
from app.decision.suppression import check_suppressed, mark_suppressed
from app.decision.templates import render_template
from app.models import ResolvedContext, TickAction

logger = logging.getLogger(__name__)

_process_tick_lock = threading.Lock()


def process_tick(
    available_trigger_ids: List[str],
    now: Optional[str],
    context_store: Any,
    conversation_store: Any,
) -> List[TickAction]:
    """
    Full pipeline for POST /v1/tick:
    1. Gather and deduplicate available triggers.
    2. Resolve merchant, category, and customer using resolve_context.
    3. Filter out expired or suppressed triggers.
    4. Group candidate triggers by merchant to select the strongest trigger per merchant.
    5. Rank all selected triggers by score_trigger descending.
    6. Cap at 20 actions.
    7. Render deterministic templates, mark suppression keys, create conversation records.
    8. Return list of TickAction models.
    """
    if not available_trigger_ids or not context_store:
        return []

    with _process_tick_lock:
        # Deduplicate trigger IDs preserving order
        seen_ids = set()
        deduped_ids = []
        for tid in available_trigger_ids:
            if tid and tid not in seen_ids:
                seen_ids.add(tid)
                deduped_ids.append(tid)

        # Gather triggers and resolve normalized context
        candidate_items = []
        for tid in deduped_ids:
            rc = resolve_context(context_store, tid)
            if not rc or not rc.trigger:
                continue

            trigger = rc.trigger
            if is_trigger_expired(trigger, now):
                continue

            # Fail closed on cross-merchant customer mismatch or category mismatch
            if rc.metadata.get("cross_merchant_customer_mismatch"):
                continue
            if rc.metadata.get("category_mismatch"):
                continue

            merchant = rc.merchant
            merchant_id = trigger.get("merchant_id")
            if merchant_id and not merchant:
                # Cannot act without merchant context
                continue

            suppression_key = trigger.get("suppression_key")
            trg_id = trigger.get("id") or tid
            effective_key = suppression_key or f"_trg:{merchant_id or 'all'}:{trg_id}"
            if check_suppressed(suppression_key, now=now) or check_suppressed(effective_key, now=now):
                continue

            category = rc.category
            customer = rc.customer

            # Intercept mismatched triggers in candidate scoring
            trigger_kind = trigger.get("kind", "")
            cat_slug = (category.get("slug") if isinstance(category, dict) else (merchant.get("category_slug") if isinstance(merchant, dict) else "")) or ""
            if trigger_kind == "chronic_refill_due" and "pharm" not in cat_slug.lower():
                trigger_kind = "regular_customer_re_engagement"
                trigger["kind"] = "regular_customer_re_engagement"
                if isinstance(trigger.get("payload"), dict) and trigger["payload"].get("metric_or_topic") == "chronic_refill_due":
                    trigger["payload"]["metric_or_topic"] = "regular_customer_re_engagement"

            # Check if customer object actually exists before scoring
            has_valid_customer = bool(
                customer
                and isinstance(customer, dict)
                and (customer.get("name") or (customer.get("identity", {}).get("name") if isinstance(customer.get("identity"), dict) else None))
                and trigger.get("scope") != "merchant"
            )
            if not has_valid_customer:
                customer = None
                rc.customer = None

            score = score_trigger(trigger, merchant, category, customer, now=now)
            if score > 0:
                candidate_items.append({
                    "score": score,
                    "resolved_context": rc,
                    "trigger": trigger,
                    "merchant": merchant,
                    "category": category,
                    "customer": customer,
                    "merchant_id": merchant_id or f"m_unknown_{tid}",
                })

        if not candidate_items:
            return []

        # Group by merchant to pick the single strongest signal per merchant
        merchant_groups: Dict[str, List[Dict[str, Any]]] = {}
        for item in candidate_items:
            mid = item["merchant_id"]
            merchant_groups.setdefault(mid, []).append(item)

        selected_per_merchant = []
        for mid, group in merchant_groups.items():
            # Best trigger for this merchant (tie-break by trigger id)
            group.sort(key=lambda x: (x["score"], str(x["trigger"].get("id", ""))), reverse=True)
            selected_per_merchant.append(group[0])

        # Rank all merchants' best triggers by score descending (tie-break by merchant_id)
        selected_per_merchant.sort(key=lambda x: (x["score"], str(x["merchant_id"])), reverse=True)

        # Cap at 20 actions per tick
        selected_actions_data = selected_per_merchant[:20]

        actions: List[TickAction] = []
        for item in selected_actions_data:
            rc: ResolvedContext = item["resolved_context"]
            trg = rc.trigger
            m = rc.merchant
            c = rc.category
            cust = rc.customer

            # Intercept and rewrite mismatched triggers before template and composer
            trigger_kind = trg.get("kind", "")
            cat_slug = (c.get("slug") if isinstance(c, dict) else (m.get("category_slug") if isinstance(m, dict) else "")) or ""
            if trigger_kind == "chronic_refill_due" and "pharm" not in cat_slug.lower():
                trigger_kind = "regular_customer_re_engagement"
                trg["kind"] = "regular_customer_re_engagement"
                if isinstance(trg.get("payload"), dict) and trg["payload"].get("metric_or_topic") == "chronic_refill_due":
                    trg["payload"]["metric_or_topic"] = "regular_customer_re_engagement"

            # Check if customer object actually exists before building that part
            has_cust = bool(
                cust
                and isinstance(cust, dict)
                and (cust.get("name") or (cust.get("identity", {}).get("name") if isinstance(cust.get("identity"), dict) else None))
                and trg.get("scope") != "merchant"
            )
            if not has_cust:
                cust = None
                rc.customer = None
                cust_id = None
            else:
                cust_id = trg.get("customer_id")

            m_id = trg.get("merchant_id") or "unknown_merchant"
            trg_id = trg.get("id")
            sup_key = trg.get("suppression_key")

            # Reuse existing conversation if present for merchant/trigger
            existing_conv_id = None
            if conversation_store is not None:
                with conversation_store._lock:
                    for cid, cdata in conversation_store._conversations.items():
                        if cdata.get("merchant_id") == m_id and (
                            cdata.get("trigger_id") == trg_id or (cust_id and cdata.get("customer_id") == cust_id)
                        ):
                            existing_conv_id = cid
                            break
            conv_id = existing_conv_id or f"conv_{uuid.uuid4().hex[:8]}"

            fallback_tuple = render_template(rc)
            compact_ctx = build_compact_context(
                resolved_context=rc,
                selected_signal=trg.get("kind"),
                now=now,
            )
            body, template_name, template_params, rationale = compose_message(
                compact_context=compact_ctx,
                fallback_data=fallback_tuple,
                conversation_store=conversation_store,
                conversation_id=conv_id,
            )

            action = TickAction(
                conversation_id=conv_id,
                merchant_id=m_id,
                customer_id=cust_id,
                send_as="vera",
                trigger_id=trg_id,
                template_name=template_name,
                template_params=template_params,
                body=body,
                cta="open_ended",
                suppression_key=sup_key,
                rationale=rationale,
            )
            actions.append(action)

            # Mark suppression key active
            if sup_key:
                mark_suppressed(
                    key=sup_key,
                    sent_at=now,
                    expires_at=trg.get("suppression_expires_at"),
                    window_seconds=trg.get("frequency_window")
                    or trg.get("window_seconds")
                    or trg.get("suppression_window"),
                )
            mark_suppressed(
                key=f"_trg:{m_id}:{trg_id}",
                sent_at=now,
                expires_at=trg.get("suppression_expires_at"),
                window_seconds=trg.get("frequency_window")
                or trg.get("window_seconds")
                or trg.get("suppression_window"),
            )

            # Record conversation in ConversationStore
            if conversation_store is not None:
                conversation_store.create_or_update(
                    conversation_id=conv_id,
                    merchant_id=m_id,
                    customer_id=cust_id,
                    trigger_id=trg_id,
                    last_action="sent",
                    state="waiting_for_reply",
                )
                conversation_store.add_sent_message(
                    conv_id,
                    {
                        "body": body,
                        "template_name": template_name,
                        "sent_at": now or datetime.now(timezone.utc).isoformat(),
                    },
                )

        return actions


__all__ = ["process_tick"]
