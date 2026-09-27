"""Tests for Normalized ResolvedContext resolution, edge cases, and cross-merchant isolation."""

import pytest
from app.decision_engine import resolve_context, select_strongest_signal, process_tick
from app.models import ResolvedContext
from app.store import ContextStore, ConversationStore


@pytest.fixture
def context_store():
    store = ContextStore()
    yield store
    store.clear()


@pytest.fixture
def conversation_store():
    store = ConversationStore()
    yield store
    store.clear()


def test_resolved_context_complete(context_store):
    """Happy path: All entities present, valid versions and relationships."""
    context_store.set("merchant", "m_001", 3, {
        "id": "m_001",
        "name": "Dr. Meera Clinic",
        "category_slug": "dentists",
        "subscription": {"status": "active"},
    })
    context_store.set("category", "dentists", 1, {
        "slug": "dentists",
        "display_name": "Dentists",
    })
    context_store.set("customer", "c_001", 2, {
        "id": "c_001",
        "merchant_id": "m_001",
        "identity": {"name": "Aanya Sharma"},
    })
    context_store.set("trigger", "trg_001", 1, {
        "id": "trg_001",
        "kind": "recall_due",
        "merchant_id": "m_001",
        "customer_id": "c_001",
        "urgency": 4,
        "payload": {"service_due": "cleaning"},
    })

    rc = resolve_context(context_store, "trg_001")
    assert isinstance(rc, ResolvedContext)
    assert rc.trigger["id"] == "trg_001"
    assert rc.merchant["name"] == "Dr. Meera Clinic"
    assert rc.category["display_name"] == "Dentists"
    assert rc.customer["identity"]["name"] == "Aanya Sharma"
    assert rc.versions["merchant"] == 3
    assert rc.versions["category"] == 1
    assert rc.versions["customer"] == 2
    assert rc.versions["trigger"] == 1
    assert rc.metadata.get("category_mismatch") is not True
    assert rc.metadata.get("cross_merchant_customer_mismatch") is not True


def test_resolved_context_missing_merchant(context_store):
    """Trigger referencing non-existent merchant returns None."""
    context_store.set("trigger", "trg_ghost_merchant", 1, {
        "id": "trg_ghost_merchant",
        "kind": "recall_due",
        "merchant_id": "m_does_not_exist",
        "payload": {},
    })

    rc = resolve_context(context_store, "trg_ghost_merchant")
    assert rc is None


def test_resolved_context_missing_category(context_store):
    """Trigger and merchant exist, but category is absent in store."""
    context_store.set("merchant", "m_002", 1, {
        "id": "m_002",
        "name": "Super Salon",
        "category_slug": "unregistered_category",
    })
    context_store.set("trigger", "trg_002", 1, {
        "id": "trg_002",
        "kind": "festive",
        "merchant_id": "m_002",
        "payload": {},
    })

    rc = resolve_context(context_store, "trg_002")
    assert rc is not None
    assert rc.merchant["id"] == "m_002"
    assert rc.category is None
    assert rc.versions.get("category") is None


def test_resolved_context_missing_customer(context_store):
    """Customer-scoped trigger where customer is missing in store."""
    context_store.set("merchant", "m_003", 1, {
        "id": "m_003",
        "name": "Iron Gym",
        "category_slug": "gyms",
    })
    context_store.set("trigger", "trg_003", 1, {
        "id": "trg_003",
        "kind": "recall_due",
        "merchant_id": "m_003",
        "customer_id": "c_missing",
        "payload": {},
    })

    rc = resolve_context(context_store, "trg_003")
    assert rc is not None
    assert rc.merchant["id"] == "m_003"
    assert rc.customer is None
    assert rc.versions.get("customer") is None


def test_resolved_context_stale_context(context_store):
    """Context version tracking accurately reflects currently stored versions after updates and stale attempts."""
    context_store.set("merchant", "m_004", 5, {"id": "m_004", "name": "V5 Merchant"})
    # Stale attempt: version 3 rejected
    gate_res, cur_ver = context_store.set("merchant", "m_004", 3, {"id": "m_004", "name": "V3 Stale"})
    assert gate_res.value == "stale"
    assert cur_ver == 5

    context_store.set("trigger", "trg_004", 2, {
        "id": "trg_004",
        "kind": "dormant_with_vera",
        "merchant_id": "m_004",
        "payload": {},
    })

    rc = resolve_context(context_store, "trg_004")
    assert rc is not None
    assert rc.merchant["name"] == "V5 Merchant"
    assert rc.versions["merchant"] == 5
    assert rc.versions["trigger"] == 2


def test_resolved_context_cross_merchant_isolation(context_store):
    """Customer belongs to merchant A, but trigger for merchant B references that customer -> flagged in metadata."""
    context_store.set("merchant", "m_A", 1, {"id": "m_A", "name": "Merchant A"})
    context_store.set("merchant", "m_B", 1, {"id": "m_B", "name": "Merchant B"})
    context_store.set("customer", "c_custA", 1, {
        "id": "c_custA",
        "merchant_id": "m_A",
        "identity": {"name": "Customer A"},
    })
    context_store.set("trigger", "trg_leak", 1, {
        "id": "trg_leak",
        "kind": "recall_due",
        "merchant_id": "m_B",
        "customer_id": "c_custA",
        "payload": {},
    })

    rc = resolve_context(context_store, "trg_leak")
    assert rc is not None
    assert rc.merchant["id"] == "m_B"
    assert rc.customer["id"] == "c_custA"
    assert rc.metadata.get("cross_merchant_customer_mismatch") is True


def test_resolved_context_category_mismatch(context_store):
    """Merchant category_slug differs from category specified by trigger -> flagged in metadata."""
    context_store.set("merchant", "m_dentist", 1, {
        "id": "m_dentist",
        "name": "Dr. Dent",
        "category_slug": "dentists",
    })
    context_store.set("category", "salon", 1, {
        "slug": "salon",
        "display_name": "Salons",
    })
    context_store.set("trigger", "trg_mismatch", 1, {
        "id": "trg_mismatch",
        "kind": "trend",
        "merchant_id": "m_dentist",
        "payload": {"category": "salon"},
    })

    rc = resolve_context(context_store, "trg_mismatch")
    assert rc is not None
    assert rc.metadata.get("category_mismatch") is True
    assert rc.metadata.get("merchant_category") == "dentists"
    assert rc.metadata.get("trigger_category") == "salon"
    assert rc.metadata.get("resolved_category") == "dentists"


def test_tick_and_composer_consume_resolved_context(context_store, conversation_store):
    """Verifies process_tick seamlessly resolves ResolvedContext and produces grounded TickActions."""
    context_store.set("merchant", "m_010", 1, {
        "id": "m_010",
        "name": "Apollo Clinic",
        "category_slug": "dentists",
        "identity": {"name": "Apollo Clinic"},
        "offers": [{"title": "Dental Cleaning Promo", "status": "active"}],
        "performance": {"views": 1200, "ctr": 0.035},
    })
    context_store.set("category", "dentists", 1, {
        "slug": "dentists",
        "display_name": "Dentists",
    })
    context_store.set("trigger", "trg_010", 1, {
        "id": "trg_010",
        "kind": "perf_spike",
        "merchant_id": "m_010",
        "urgency": 3,
        "payload": {"metric": "views", "delta_pct": 0.20},
    })

    actions = process_tick(["trg_010"], now="2026-04-26T12:00:00Z", context_store=context_store, conversation_store=conversation_store)
    assert len(actions) == 1
    act = actions[0]
    assert act.merchant_id == "m_010"
    assert act.trigger_id == "trg_010"
    assert "Apollo Clinic" in act.body
    assert "+20%" in act.body


def test_decision_submodules_imports_and_exports():
    """Verify focused decision submodules can be imported directly and match compatibility façade."""
    import app.decision as decision
    import app.decision_engine as decision_engine
    from app.decision.orchestrator import process_tick as orch_tick
    from app.decision.resolution import resolve_context as res_ctx
    from app.decision.scoring import score_trigger as sc_trg
    from app.decision.suppression import suppression_engine as supp_eng
    from app.decision.taxonomy import KIND_TO_FAMILY as tax_k2f
    from app.decision.templates import render_template as tmpl_render

    # Verify identical instances / functions
    assert supp_eng is decision_engine.suppression_engine
    assert supp_eng is decision.suppression_engine
    assert res_ctx is decision_engine.resolve_context
    assert sc_trg is decision_engine.score_trigger
    assert tmpl_render is decision_engine.render_template
    assert orch_tick is decision_engine.process_tick
    assert tax_k2f is decision_engine.KIND_TO_FAMILY

