"""Tests for Canonical Trigger Taxonomy, deterministic resolution, and dataset alignment."""

import glob
import json
import os
from typing import Any, Dict, Set

import pytest

from app.decision.scoring import score_trigger
from app.decision.taxonomy import (
    CANONICAL_FAMILIES,
    CANONICAL_TRIGGER_POLICIES,
    DEFAULT_TRIGGER_POLICY,
    FAMILY_GENERIC,
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
from app.decision.templates import render_template


def _load_all_dataset_triggers():
    """Helper to load all trigger definitions from dataset/ directory."""
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "dataset"))
    triggers = []

    # 1. Individual trigger files
    trg_files = glob.glob(os.path.join(base_dir, "triggers", "*.json"))
    for fpath in trg_files:
        with open(fpath, "r", encoding="utf-8") as f:
            data = json.load(f)
            data["_source_file"] = os.path.basename(fpath)
            triggers.append(data)

    # 2. Seed triggers file
    seed_path = os.path.join(base_dir, "triggers_seed.json")
    if os.path.exists(seed_path):
        with open(seed_path, "r", encoding="utf-8") as f:
            seed_data = json.load(f)
            items = seed_data.get("triggers", []) if isinstance(seed_data, dict) else seed_data
            for item in items:
                item_copy = dict(item)
                item_copy["_source_file"] = "triggers_seed.json"
                triggers.append(item_copy)

    return triggers


def test_every_dataset_trigger_resolves_to_valid_canonical_policy():
    """
    Assert that every trigger kind in dataset/triggers/*.json and dataset/triggers_seed.json
    maps deterministically to a valid canonical TriggerPolicy.
    """
    dataset_triggers = _load_all_dataset_triggers()
    assert len(dataset_triggers) > 0, "No dataset triggers loaded"

    distinct_kinds: Set[str] = set()

    for trg in dataset_triggers:
        kind = trg.get("kind")
        assert kind is not None, f"Trigger missing 'kind' in {trg.get('_source_file')}"
        distinct_kinds.add(kind)

        # 1. Deterministic canonical resolution
        canonical_kind = resolve_canonical_kind(kind)
        assert canonical_kind in CANONICAL_TRIGGER_POLICIES, (
            f"Dataset trigger kind '{kind}' from {trg.get('_source_file')} resolved to "
            f"unknown canonical kind '{canonical_kind}'"
        )

        # 2. Policy retrieval
        policy = get_trigger_policy(kind)
        assert policy is not None
        assert policy is not DEFAULT_TRIGGER_POLICY, (
            f"Dataset trigger kind '{kind}' fell back to DEFAULT_TRIGGER_POLICY"
        )
        assert policy.name == canonical_kind

        # 3. Canonical family validity
        assert policy.family in CANONICAL_FAMILIES
        assert policy.family != FAMILY_GENERIC

        # 4. Rendering policy validity
        assert isinstance(policy.template_name, str)
        assert policy.template_name.startswith("template_")

        # 5. Scoring policy validity
        assert 1 <= policy.base_urgency <= 5
        assert 0 <= policy.priority <= 100

        # 6. Scope & Source invariants
        assert policy.scope in ("merchant", "customer")
        assert policy.source in ("external", "internal")

        # 7. Alignment with trigger definition scope if provided
        expected_scope = trg.get("scope")
        if expected_scope:
            assert policy.scope == expected_scope, (
                f"Scope mismatch for '{kind}': policy has '{policy.scope}', "
                f"dataset trigger specifies '{expected_scope}'"
            )

    # Verify expected count of unique dataset trigger kinds
    assert len(distinct_kinds) >= 26


def test_explicit_alias_resolution_and_parity():
    """Assert all legacy and dataset aliases resolve to their canonical targets identically."""
    assert len(TRIGGER_ALIASES) > 0

    for alias, target in TRIGGER_ALIASES.items():
        assert target in CANONICAL_TRIGGER_POLICIES, (
            f"Alias '{alias}' points to non-existent canonical target '{target}'"
        )
        assert is_known_trigger_kind(alias) is True
        assert resolve_canonical_kind(alias) == target

        alias_policy = get_trigger_policy(alias)
        target_policy = get_trigger_policy(target)
        assert alias_policy == target_policy

        # Verify parity across synchronized compatibility mappings
        assert KIND_TO_FAMILY[alias] == target_policy.family
        assert TRIGGER_PRIORITY[alias] == target_policy.priority
        assert TEMPLATE_NAME_OVERRIDES[alias] == target_policy.template_name


def test_unknown_and_malformed_inputs_fallback_to_default_policy():
    """Assert invalid, empty, or unmapped trigger kinds resolve safely to generic default."""
    for bad_input in [None, "", "   ", "completely_unknown_kind_404", 12345]:
        assert is_known_trigger_kind(bad_input) is False  # type: ignore[arg-type]
        assert resolve_canonical_kind(bad_input) == DEFAULT_TRIGGER_POLICY.name  # type: ignore[arg-type]
        policy = get_trigger_policy(bad_input)  # type: ignore[arg-type]
        assert policy == DEFAULT_TRIGGER_POLICY
        assert policy.family == FAMILY_GENERIC
        assert policy.base_urgency == 1
        assert policy.priority == 30
        assert policy.template_name == "template_default_v1"


def test_case_and_whitespace_insensitivity():
    """Assert trigger resolution is resilient to leading/trailing whitespace and case."""
    assert resolve_canonical_kind("  PERF_DIP  ") == "perf_dip"
    assert resolve_canonical_kind("  Festive  ") == "festival"
    assert resolve_canonical_kind("RESEARCH_DIGEST") == "research_digest"
    assert get_trigger_policy("  Regulation_Change  ").priority == 100


def test_compatibility_dictionaries_fully_synchronized():
    """Verify legacy dictionaries KIND_TO_FAMILY, TEMPLATE_NAME_OVERRIDES, and TRIGGER_PRIORITY match policies."""
    all_canonical = get_all_canonical_kinds()
    assert len(all_canonical) == len(CANONICAL_TRIGGER_POLICIES)

    for kind in all_canonical:
        policy = CANONICAL_TRIGGER_POLICIES[kind]
        assert KIND_TO_FAMILY[kind] == policy.family
        assert TEMPLATE_NAME_OVERRIDES[kind] == policy.template_name
        assert TRIGGER_PRIORITY[kind] == policy.priority

    # Verify legacy keys asserted by existing tests
    assert TRIGGER_PRIORITY["compliance_alert"] == 100
    assert TRIGGER_PRIORITY["recall_due"] == 95
    assert TRIGGER_PRIORITY["performance_drop"] == 90
    assert TRIGGER_PRIORITY["festival"] == 60
    assert TRIGGER_PRIORITY["seasonal"] == 40
    assert TRIGGER_PRIORITY["curious_ask"] == 50


def test_scoring_policy_integration_with_base_urgency():
    """Verify score_trigger uses policy.base_urgency when trigger urgency is omitted."""
    # regulation_change has base_urgency 5 -> 5 * 20 = 100 + 5 (merchant-level customer_relevance) = 105
    score_reg = score_trigger({"id": "t_reg", "kind": "regulation_change"})
    assert score_reg == 105

    # curious_ask_due has base_urgency 1 -> 1 * 20 = 20 + 5 = 25
    score_curious = score_trigger({"id": "t_cur", "kind": "curious_ask_due"})
    assert score_curious == 25

    # Explicit urgency overrides base_urgency
    score_reg_override = score_trigger({"id": "t_reg", "kind": "regulation_change", "urgency": 2})
    assert score_reg_override == 45

    # Unmapped trigger defaults to base_urgency 1
    score_unmapped = score_trigger({"id": "t_unmapped", "kind": "unknown_signal"})
    assert score_unmapped == 25


def test_deterministic_template_rendering_for_aliases():
    """Verify alias triggers render with the identical template and family as canonical triggers."""
    merchant = {
        "identity": {"name": "Test Clinic"},
        "offers": [{"title": "10% Off", "status": "active"}],
        "category_slug": "dentists",
    }
    category = {"slug": "dentists", "display_name": "Dentists"}

    # Canonical 'festival' vs alias 'festive'
    body_canon, tmpl_canon, _, _ = render_template(
        {"id": "t_c", "kind": "festival", "payload": {"festival": "Diwali", "days_until": 5}},
        merchant,
        category,
    )
    body_alias, tmpl_alias, _, _ = render_template(
        {"id": "t_a", "kind": "festive", "payload": {"festival": "Diwali", "days_until": 5}},
        merchant,
        category,
    )
    assert tmpl_canon == tmpl_alias == "template_festival_v1"
    assert body_canon == body_alias

    # Canonical 'perf_dip' vs alias 'performance_dip'
    b_c, t_c, _, _ = render_template(
        {"id": "t_p1", "kind": "perf_dip", "payload": {"metric": "views", "delta_pct": -0.25}},
        merchant,
        category,
    )
    b_a, t_a, _, _ = render_template(
        {"id": "t_p2", "kind": "performance_dip", "payload": {"metric": "views", "delta_pct": -0.25}},
        merchant,
        category,
    )
    assert t_c == t_a == "template_performance_drop_v1"
    assert b_c == b_a

    # Canonical 'research_digest' vs alias 'research'
    b_c, t_c, _, _ = render_template(
        {"id": "t_r1", "kind": "research_digest", "payload": {"title": "AI in Dentistry"}},
        merchant,
        category,
    )
    b_a, t_a, _, _ = render_template(
        {"id": "t_r2", "kind": "research", "payload": {"title": "AI in Dentistry"}},
        merchant,
        category,
    )
    assert t_c == t_a == "template_research_digest_v1"
    assert b_c == b_a

    # Canonical 'compliance_alert' vs alias 'compliance'
    b_c, t_c, _, _ = render_template(
        {"id": "t_c1", "kind": "compliance_alert", "payload": {"topic": "Bio-waste norms"}},
        merchant,
        category,
    )
    b_a, t_a, _, _ = render_template(
        {"id": "t_c2", "kind": "compliance", "payload": {"topic": "Bio-waste norms"}},
        merchant,
        category,
    )
    assert t_c == t_a == "template_compliance_alert_v1"
    assert b_c == b_a

    # Canonical 'customer_winback' vs alias 'winback'
    b_c, t_c, _, _ = render_template(
        {"id": "t_w1", "kind": "customer_winback", "payload": {}},
        merchant,
        category,
    )
    b_a, t_a, _, _ = render_template(
        {"id": "t_w2", "kind": "winback", "payload": {}},
        merchant,
        category,
    )
    assert t_c == t_a == "template_customer_winback_v1"
    assert b_c == b_a
