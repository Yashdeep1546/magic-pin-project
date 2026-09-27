"""Canonical trigger taxonomy, policy registry, and alias resolution.

Eliminates inconsistent legacy semantics across TRIGGER_PRIORITY, KIND_TO_FAMILY,
template name overrides, and trigger aliases by providing one deterministic
canonical policy per trigger kind.
"""

from dataclasses import dataclass
from typing import Dict, List, Literal, Optional, Set

# ---------------------------------------------------------------------------
# Canonical Families (§4.3)
# ---------------------------------------------------------------------------
FAMILY_RESEARCH_KNOWLEDGE = "research_knowledge"
FAMILY_PERFORMANCE = "performance"
FAMILY_RECALL_LAPSE = "recall_lapse"
FAMILY_OPPORTUNITY_EVENT = "opportunity_event"
FAMILY_MILESTONE = "milestone"
FAMILY_RELATIONSHIP = "relationship"
FAMILY_GENERIC = "generic_fallback"

CANONICAL_FAMILIES: Set[str] = {
    FAMILY_RESEARCH_KNOWLEDGE,
    FAMILY_PERFORMANCE,
    FAMILY_RECALL_LAPSE,
    FAMILY_OPPORTUNITY_EVENT,
    FAMILY_MILESTONE,
    FAMILY_RELATIONSHIP,
    FAMILY_GENERIC,
}


# ---------------------------------------------------------------------------
# Canonical Trigger Policy
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TriggerPolicy:
    """
    Deterministic specification for a trigger kind:
    - family: Target template family
    - scope: Primary entity scope (merchant vs customer)
    - source: Origin domain (external market event vs internal merchant data)
    - base_urgency: Default urgency level (1-5)
    - priority: Canonical priority score (0-100)
    - template_name: Canonical rendered template identifier
    - description: Human-readable documentation
    """
    name: str
    family: str
    scope: Literal["merchant", "customer"]
    source: Literal["external", "internal"]
    base_urgency: int
    priority: int
    template_name: str
    description: str = ""


# ---------------------------------------------------------------------------
# Canonical Trigger Registry
# ---------------------------------------------------------------------------
CANONICAL_TRIGGER_POLICIES: Dict[str, TriggerPolicy] = {
    # -----------------------------------------------------------------------
    # 1. Research & Knowledge
    # -----------------------------------------------------------------------
    "category_research_digest_release": TriggerPolicy(
        name="category_research_digest_release",
        family=FAMILY_RESEARCH_KNOWLEDGE,
        scope="merchant",
        source="external",
        base_urgency=2,
        priority=70,
        template_name="template_research_knowledge_v1",
        description="Category research paper or digest release",
    ),
    "research_digest": TriggerPolicy(
        name="research_digest",
        family=FAMILY_RESEARCH_KNOWLEDGE,
        scope="merchant",
        source="external",
        base_urgency=2,
        priority=70,
        template_name="template_research_digest_v1",
        description="Weekly or monthly research digest",
    ),
    "regulation_change": TriggerPolicy(
        name="regulation_change",
        family=FAMILY_RESEARCH_KNOWLEDGE,
        scope="merchant",
        source="external",
        base_urgency=5,
        priority=100,
        template_name="template_research_knowledge_v1",
        description="Regulatory or compliance deadline update",
    ),
    "compliance_alert": TriggerPolicy(
        name="compliance_alert",
        family=FAMILY_RESEARCH_KNOWLEDGE,
        scope="merchant",
        source="external",
        base_urgency=5,
        priority=100,
        template_name="template_compliance_alert_v1",
        description="Compliance requirement notification",
    ),
    "category_trend_movement": TriggerPolicy(
        name="category_trend_movement",
        family=FAMILY_RESEARCH_KNOWLEDGE,
        scope="merchant",
        source="external",
        base_urgency=3,
        priority=65,
        template_name="template_research_knowledge_v1",
        description="Search demand surge in category",
    ),
    "cde_opportunity": TriggerPolicy(
        name="cde_opportunity",
        family=FAMILY_RESEARCH_KNOWLEDGE,
        scope="merchant",
        source="external",
        base_urgency=2,
        priority=60,
        template_name="template_research_knowledge_v1",
        description="Continuing dental/professional education opportunity",
    ),
    "supply_alert": TriggerPolicy(
        name="supply_alert",
        family=FAMILY_RESEARCH_KNOWLEDGE,
        scope="merchant",
        source="external",
        base_urgency=3,
        priority=75,
        template_name="template_research_knowledge_v1",
        description="Supply chain or material cost advisory",
    ),

    # -----------------------------------------------------------------------
    # 2. Performance
    # -----------------------------------------------------------------------
    "perf_spike": TriggerPolicy(
        name="perf_spike",
        family=FAMILY_PERFORMANCE,
        scope="merchant",
        source="internal",
        base_urgency=2,
        priority=85,
        template_name="template_performance_v1",
        description="Traffic or views surge",
    ),
    "perf_dip": TriggerPolicy(
        name="perf_dip",
        family=FAMILY_PERFORMANCE,
        scope="merchant",
        source="internal",
        base_urgency=4,
        priority=90,
        template_name="template_performance_drop_v1",
        description="Conversion, views, or calls drop below peer median",
    ),
    "performance_drop": TriggerPolicy(
        name="performance_drop",
        family=FAMILY_PERFORMANCE,
        scope="merchant",
        source="internal",
        base_urgency=4,
        priority=90,
        template_name="template_performance_drop_v1",
        description="Performance metric dropped",
    ),
    "seasonal_perf_dip": TriggerPolicy(
        name="seasonal_perf_dip",
        family=FAMILY_PERFORMANCE,
        scope="merchant",
        source="internal",
        base_urgency=3,
        priority=40,
        template_name="template_performance_drop_v1",
        description="Seasonal downturn in merchant footfall/traffic",
    ),

    # -----------------------------------------------------------------------
    # 3. Recall & Lapse (Customer Engagement Loops)
    # -----------------------------------------------------------------------
    "recall_due": TriggerPolicy(
        name="recall_due",
        family=FAMILY_RECALL_LAPSE,
        scope="customer",
        source="internal",
        base_urgency=4,
        priority=95,
        template_name="template_recall_due_v1",
        description="Customer routine service or recall window opens",
    ),
    "customer_lapsed_soft": TriggerPolicy(
        name="customer_lapsed_soft",
        family=FAMILY_RECALL_LAPSE,
        scope="customer",
        source="internal",
        base_urgency=3,
        priority=80,
        template_name="template_recall_lapse_v1",
        description="Customer overdue for regular visit (soft lapse)",
    ),
    "customer_lapsed_hard": TriggerPolicy(
        name="customer_lapsed_hard",
        family=FAMILY_RECALL_LAPSE,
        scope="customer",
        source="internal",
        base_urgency=4,
        priority=85,
        template_name="template_recall_lapse_v1",
        description="Customer long overdue (hard lapse)",
    ),
    "appointment_tomorrow": TriggerPolicy(
        name="appointment_tomorrow",
        family=FAMILY_RECALL_LAPSE,
        scope="customer",
        source="internal",
        base_urgency=3,
        priority=85,
        template_name="template_recall_lapse_v1",
        description="Appointment reminder for next day",
    ),
    "unplanned_slot_open": TriggerPolicy(
        name="unplanned_slot_open",
        family=FAMILY_RECALL_LAPSE,
        scope="customer",
        source="internal",
        base_urgency=4,
        priority=85,
        template_name="template_recall_lapse_v1",
        description="Cancellation or open capacity tomorrow",
    ),
    "chronic_refill_due": TriggerPolicy(
        name="chronic_refill_due",
        family=FAMILY_RECALL_LAPSE,
        scope="customer",
        source="internal",
        base_urgency=3,
        priority=80,
        template_name="template_recall_lapse_v1",
        description="Chronic medication or recurring service refill due",
    ),
    "customer_winback": TriggerPolicy(
        name="customer_winback",
        family=FAMILY_RECALL_LAPSE,
        scope="customer",
        source="internal",
        base_urgency=3,
        priority=80,
        template_name="template_customer_winback_v1",
        description="Re-engagement campaign for lapsed customers",
    ),
    "winback_eligible": TriggerPolicy(
        name="winback_eligible",
        family=FAMILY_RECALL_LAPSE,
        scope="merchant",
        source="internal",
        base_urgency=3,
        priority=80,
        template_name="template_customer_winback_v1",
        description="Customer eligible for winback outreach",
    ),
    "trial_followup": TriggerPolicy(
        name="trial_followup",
        family=FAMILY_RECALL_LAPSE,
        scope="customer",
        source="internal",
        base_urgency=3,
        priority=75,
        template_name="template_recall_lapse_v1",
        description="Follow-up after completed trial service",
    ),
    "wedding_package_followup": TriggerPolicy(
        name="wedding_package_followup",
        family=FAMILY_RECALL_LAPSE,
        scope="customer",
        source="internal",
        base_urgency=2,
        priority=70,
        template_name="template_recall_lapse_v1",
        description="Bridal / wedding package milestone followup",
    ),

    # -----------------------------------------------------------------------
    # 4. Opportunity & Events
    # -----------------------------------------------------------------------
    "festival_upcoming": TriggerPolicy(
        name="festival_upcoming",
        family=FAMILY_OPPORTUNITY_EVENT,
        scope="merchant",
        source="external",
        base_urgency=2,
        priority=60,
        template_name="template_opportunity_event_v1",
        description="Approaching festive shopping/dining season",
    ),
    "festival": TriggerPolicy(
        name="festival",
        family=FAMILY_OPPORTUNITY_EVENT,
        scope="merchant",
        source="external",
        base_urgency=2,
        priority=60,
        template_name="template_festival_v1",
        description="Festival campaign opportunity",
    ),
    "weather_heatwave": TriggerPolicy(
        name="weather_heatwave",
        family=FAMILY_OPPORTUNITY_EVENT,
        scope="merchant",
        source="external",
        base_urgency=3,
        priority=70,
        template_name="template_opportunity_event_v1",
        description="Weather-triggered footfall/service promotion",
    ),
    "local_news_event": TriggerPolicy(
        name="local_news_event",
        family=FAMILY_OPPORTUNITY_EVENT,
        scope="merchant",
        source="external",
        base_urgency=3,
        priority=65,
        template_name="template_opportunity_event_v1",
        description="Local community or news event",
    ),
    "competitor_opened": TriggerPolicy(
        name="competitor_opened",
        family=FAMILY_OPPORTUNITY_EVENT,
        scope="merchant",
        source="external",
        base_urgency=3,
        priority=75,
        template_name="template_opportunity_event_v1",
        description="New competitor opened in proximity",
    ),
    "ipl_match_today": TriggerPolicy(
        name="ipl_match_today",
        family=FAMILY_OPPORTUNITY_EVENT,
        scope="merchant",
        source="external",
        base_urgency=3,
        priority=65,
        template_name="template_opportunity_event_v1",
        description="Sports matchday promotion",
    ),
    "category_seasonal": TriggerPolicy(
        name="category_seasonal",
        family=FAMILY_OPPORTUNITY_EVENT,
        scope="merchant",
        source="external",
        base_urgency=2,
        priority=40,
        template_name="template_seasonal_v1",
        description="Category seasonal change/trends",
    ),
    "seasonal": TriggerPolicy(
        name="seasonal",
        family=FAMILY_OPPORTUNITY_EVENT,
        scope="merchant",
        source="external",
        base_urgency=2,
        priority=40,
        template_name="template_seasonal_v1",
        description="Seasonal trend adaptation",
    ),
    "active_planning_intent": TriggerPolicy(
        name="active_planning_intent",
        family=FAMILY_OPPORTUNITY_EVENT,
        scope="merchant",
        source="internal",
        base_urgency=3,
        priority=75,
        template_name="template_opportunity_event_v1",
        description="Customer actively searching/planning high-intent service",
    ),

    # -----------------------------------------------------------------------
    # 5. Milestone
    # -----------------------------------------------------------------------
    "milestone_reached": TriggerPolicy(
        name="milestone_reached",
        family=FAMILY_MILESTONE,
        scope="merchant",
        source="internal",
        base_urgency=2,
        priority=80,
        template_name="template_milestone_v1",
        description="Merchant crossed review, revenue, or customer milestone",
    ),

    # -----------------------------------------------------------------------
    # 6. Relationship & Retention
    # -----------------------------------------------------------------------
    "dormant_with_vera": TriggerPolicy(
        name="dormant_with_vera",
        family=FAMILY_RELATIONSHIP,
        scope="merchant",
        source="internal",
        base_urgency=2,
        priority=55,
        template_name="template_relationship_v1",
        description="No merchant activity with bot for 14+ days",
    ),
    "review_theme_emerged": TriggerPolicy(
        name="review_theme_emerged",
        family=FAMILY_RELATIONSHIP,
        scope="merchant",
        source="internal",
        base_urgency=3,
        priority=70,
        template_name="template_relationship_v1",
        description="Multiple reviews highlight recurring customer topic",
    ),
    "curious_ask_due": TriggerPolicy(
        name="curious_ask_due",
        family=FAMILY_RELATIONSHIP,
        scope="merchant",
        source="internal",
        base_urgency=1,
        priority=50,
        template_name="template_curious_ask_v1",
        description="Scheduled conversational probe on merchant business trends",
    ),
    "curious_ask": TriggerPolicy(
        name="curious_ask",
        family=FAMILY_RELATIONSHIP,
        scope="merchant",
        source="internal",
        base_urgency=1,
        priority=50,
        template_name="template_curious_ask_v1",
        description="Scheduled conversational check-in",
    ),
    "scheduled_recurring": TriggerPolicy(
        name="scheduled_recurring",
        family=FAMILY_RELATIONSHIP,
        scope="merchant",
        source="internal",
        base_urgency=1,
        priority=50,
        template_name="template_curious_ask_v1",
        description="Cadenced recurring check-in",
    ),
    "gbp_unverified": TriggerPolicy(
        name="gbp_unverified",
        family=FAMILY_RELATIONSHIP,
        scope="merchant",
        source="internal",
        base_urgency=3,
        priority=65,
        template_name="template_relationship_v1",
        description="Google Business Profile missing verification",
    ),
    "renewal_due": TriggerPolicy(
        name="renewal_due",
        family=FAMILY_RELATIONSHIP,
        scope="merchant",
        source="internal",
        base_urgency=4,
        priority=80,
        template_name="template_relationship_v1",
        description="Magicpin subscription renewal due soon",
    ),
}

# ---------------------------------------------------------------------------
# Default Generic Fallback Policy
# ---------------------------------------------------------------------------
DEFAULT_TRIGGER_POLICY = TriggerPolicy(
    name="generic_fallback",
    family=FAMILY_GENERIC,
    scope="merchant",
    source="internal",
    base_urgency=1,
    priority=30,
    template_name="template_default_v1",
    description="Generic fallback policy for unmapped or custom triggers",
)

# ---------------------------------------------------------------------------
# Explicit Alias Mapping (Legacy & Dataset Backwards Compatibility)
# ---------------------------------------------------------------------------
TRIGGER_ALIASES: Dict[str, str] = {
    # Research / Knowledge aliases
    "research": "research_digest",
    "compliance": "compliance_alert",
    "cde": "cde_opportunity",
    "trend": "category_trend_movement",
    "supply": "supply_alert",

    # Performance aliases
    "performance_dip": "perf_dip",
    "seasonal_acquisition_dip": "seasonal_perf_dip",

    # Opportunity / Event aliases
    "festive": "festival",
    "seasonal_beat": "category_seasonal",

    # Recall / Winback aliases
    "winback": "customer_winback",
}


# ---------------------------------------------------------------------------
# Deterministic Taxonomy Resolution Helpers
# ---------------------------------------------------------------------------
def resolve_canonical_kind(raw_kind: Optional[str]) -> str:
    """
    Deterministically resolves any trigger kind, alias, or legacy name
    to its canonical trigger kind.
    """
    if not raw_kind or not isinstance(raw_kind, str):
        return DEFAULT_TRIGGER_POLICY.name
    k = raw_kind.strip().lower()
    # 1. Direct canonical match
    if k in CANONICAL_TRIGGER_POLICIES:
        return k
    # 2. Explicit alias match
    if k in TRIGGER_ALIASES:
        target = TRIGGER_ALIASES[k]
        if target in CANONICAL_TRIGGER_POLICIES:
            return target
    # 3. Fallback
    return DEFAULT_TRIGGER_POLICY.name


def get_trigger_policy(raw_kind: Optional[str]) -> TriggerPolicy:
    """
    Returns the canonical TriggerPolicy for any trigger kind or alias.
    Guarantees deterministic family, scoring policy, and template policy.
    """
    canonical_name = resolve_canonical_kind(raw_kind)
    return CANONICAL_TRIGGER_POLICIES.get(canonical_name, DEFAULT_TRIGGER_POLICY)


def is_known_trigger_kind(raw_kind: Optional[str]) -> bool:
    """Returns True if the trigger kind is canonical or a known alias."""
    if not raw_kind or not isinstance(raw_kind, str):
        return False
    k = raw_kind.strip().lower()
    return k in CANONICAL_TRIGGER_POLICIES or k in TRIGGER_ALIASES


def get_all_canonical_kinds() -> List[str]:
    """Returns sorted list of all canonical trigger kinds."""
    return sorted(CANONICAL_TRIGGER_POLICIES.keys())


# ---------------------------------------------------------------------------
# Unified Compatibility Mappings (Synchronized with Policies)
# ---------------------------------------------------------------------------
KIND_TO_FAMILY: Dict[str, str] = {
    k: policy.family for k, policy in CANONICAL_TRIGGER_POLICIES.items()
}
for alias, target in TRIGGER_ALIASES.items():
    if target in CANONICAL_TRIGGER_POLICIES:
        KIND_TO_FAMILY[alias] = CANONICAL_TRIGGER_POLICIES[target].family

TEMPLATE_NAME_OVERRIDES: Dict[str, str] = {
    k: policy.template_name for k, policy in CANONICAL_TRIGGER_POLICIES.items()
}
for alias, target in TRIGGER_ALIASES.items():
    if target in CANONICAL_TRIGGER_POLICIES:
        TEMPLATE_NAME_OVERRIDES[alias] = CANONICAL_TRIGGER_POLICIES[target].template_name

TRIGGER_PRIORITY: Dict[str, int] = {
    k: policy.priority for k, policy in CANONICAL_TRIGGER_POLICIES.items()
}
for alias, target in TRIGGER_ALIASES.items():
    if target in CANONICAL_TRIGGER_POLICIES:
        TRIGGER_PRIORITY[alias] = CANONICAL_TRIGGER_POLICIES[target].priority


__all__ = [
    # Constants & Families
    "FAMILY_RESEARCH_KNOWLEDGE",
    "FAMILY_PERFORMANCE",
    "FAMILY_RECALL_LAPSE",
    "FAMILY_OPPORTUNITY_EVENT",
    "FAMILY_MILESTONE",
    "FAMILY_RELATIONSHIP",
    "FAMILY_GENERIC",
    "CANONICAL_FAMILIES",
    # Policies & Registry
    "TriggerPolicy",
    "CANONICAL_TRIGGER_POLICIES",
    "TRIGGER_ALIASES",
    "DEFAULT_TRIGGER_POLICY",
    # Functions
    "resolve_canonical_kind",
    "get_trigger_policy",
    "is_known_trigger_kind",
    "get_all_canonical_kinds",
    # Compatibility Mappings
    "KIND_TO_FAMILY",
    "TEMPLATE_NAME_OVERRIDES",
    "TRIGGER_PRIORITY",
]
