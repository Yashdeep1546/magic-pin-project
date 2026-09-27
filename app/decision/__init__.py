"""Decision package combining taxonomy, suppression, resolution, scoring, templates, and orchestrator."""

from app.decision.orchestrator import process_tick
from app.decision.resolution import (
    resolve_category,
    resolve_context,
    resolve_customer,
    resolve_merchant,
    resolve_trigger,
)
from app.decision.scoring import (
    is_trigger_expired,
    score_trigger,
    select_strongest_signal,
)
from app.decision.suppression import (
    DEFAULT_SUPPRESSION_WINDOW_SECONDS,
    SuppressionEngine,
    _parse_suppression_dt,
    check_suppressed,
    get_suppression_record,
    infer_frequency_window,
    mark_suppressed,
    suppression_engine,
)
from app.decision.taxonomy import (
    CANONICAL_FAMILIES,
    CANONICAL_TRIGGER_POLICIES,
    DEFAULT_TRIGGER_POLICY,
    FAMILY_GENERIC,
    FAMILY_MILESTONE,
    FAMILY_OPPORTUNITY_EVENT,
    FAMILY_PERFORMANCE,
    FAMILY_RECALL_LAPSE,
    FAMILY_RELATIONSHIP,
    FAMILY_RESEARCH_KNOWLEDGE,
    KIND_TO_FAMILY,
    TEMPLATE_NAME_OVERRIDES,
    TRIGGER_ALIASES,
    TRIGGER_PRIORITY,
    TriggerPolicy,
    get_all_canonical_kinds,
    get_trigger_policy,
    is_known_trigger_kind,
    resolve_canonical_kind,
)
from app.decision.templates import (
    _clean_entity_text,
    _render_generic_fallback,
    _render_milestone,
    _render_opportunity_event,
    _render_performance,
    _render_recall_lapse,
    _render_relationship,
    _render_research_knowledge,
    render_template,
)

__all__ = [
    # Taxonomy
    "FAMILY_RESEARCH_KNOWLEDGE",
    "FAMILY_PERFORMANCE",
    "FAMILY_RECALL_LAPSE",
    "FAMILY_OPPORTUNITY_EVENT",
    "FAMILY_MILESTONE",
    "FAMILY_RELATIONSHIP",
    "FAMILY_GENERIC",
    "CANONICAL_FAMILIES",
    "TriggerPolicy",
    "CANONICAL_TRIGGER_POLICIES",
    "TRIGGER_ALIASES",
    "DEFAULT_TRIGGER_POLICY",
    "resolve_canonical_kind",
    "get_trigger_policy",
    "is_known_trigger_kind",
    "get_all_canonical_kinds",
    "KIND_TO_FAMILY",
    "TEMPLATE_NAME_OVERRIDES",
    "TRIGGER_PRIORITY",
    # Suppression
    "DEFAULT_SUPPRESSION_WINDOW_SECONDS",
    "SuppressionEngine",
    "suppression_engine",
    "check_suppressed",
    "mark_suppressed",
    "get_suppression_record",
    "infer_frequency_window",
    "_parse_suppression_dt",
    # Resolution
    "resolve_merchant",
    "resolve_category",
    "resolve_customer",
    "resolve_trigger",
    "resolve_context",
    # Scoring
    "is_trigger_expired",
    "score_trigger",
    "select_strongest_signal",
    # Templates
    "_clean_entity_text",
    "_render_research_knowledge",
    "_render_performance",
    "_render_recall_lapse",
    "_render_opportunity_event",
    "_render_milestone",
    "_render_relationship",
    "_render_generic_fallback",
    "render_template",
    # Orchestrator
    "process_tick",
]
