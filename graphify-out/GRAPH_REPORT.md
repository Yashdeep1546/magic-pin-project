# Graph Report - magicpin-ai-challenge  (2026-09-27)

## Corpus Check
- 410 files · ~102,507 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 3 file(s) not represented in the graph (top: (none) 2, .example 1)

## Summary
- 1088 nodes · 2175 edges · 56 communities (45 shown, 11 thin omitted)
- Extraction: 94% EXTRACTED · 6% INFERRED · 0% AMBIGUOUS · INFERRED: 140 edges (avg confidence: 0.9)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `78810912`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- test_tick.py
- set_custom_llm_caller
- validate_message
- test_reply.py
- test_models_strictness.py
- test_output_validator_adversarial.py
- ConversationStateMachine
- ConversationState
- judge_simulator.py
- orchestrator.py
- test_adversarial.py
- Phase 2 — Test window (T0 → T0 + 60 min)
- generate_dataset.py
- global_exception_handler
- app/replay_harness.py
- test_adversarial_matrix.py
- test_context.py
- suppression.py
- BotClient
- compose
- 10 Case Studies — What "Good" Looks Like
- Judge Harness
- test_resolved_context.py
- test_phase6_checklist.py
- Vera (Merchant AI Assistant)
- Aryan Client
- test_idempotency.py
- test_phase1_endpoints.py
- decision/__init__.py
- Architecture Documentation
- Render Blueprint
- decision_engine.py
- Any
- context_store
- ContextStore
- ConversationStore
- store.py
- test_isolation_audit.py
- test_determinism.py
- ContextRequest
- tick
- main.py
- EvidenceLedger
- clean_state
- test_concurrent_duplicate_context_requests
- typing
- build_compact_context
- test_concurrent_duplicate_reply_requests
- clean_state
- clean_stores
- reset_stores
- test_matrix_e2e_reply_execution
- app/__init__.py
- tests/__init__.py
- test_reply_hostile_intent
- test_multiturn_out_of_order_turn

## God Nodes (most connected - your core abstractions)
1. `set_custom_llm_caller()` - 52 edges
2. `validate_message()` - 50 edges
3. `build_evidence_ledger()` - 48 edges
4. `ConversationState` - 43 edges
5. `build_compact_context()` - 38 edges
6. `render_template()` - 34 edges
7. `process_tick()` - 23 edges
8. `compose_message()` - 22 edges
9. `resolve_context()` - 22 edges
10. `Intent` - 20 edges

## Surprising Connections (you probably didn't know these)
- `test_select_strongest_signal_unit()` --calls--> `select_strongest_signal()`  [INFERRED]
  tests/test_tick.py → app/decision/scoring.py
- `test_matrix_7_reply_state_machine_and_auto_reply_loop()` --uses--> `ConversationState`  [INFERRED]
  tests/test_adversarial_matrix.py → app/conversation.py
- `test_matrix_8_parallel_conversations_and_out_of_order_turns()` --uses--> `ConversationState`  [INFERRED]
  tests/test_adversarial_matrix.py → app/conversation.py
- `test_concurrent_duplicate_reply_requests()` --uses--> `ConversationState`  [INFERRED]
  tests/test_idempotency.py → app/conversation.py
- `test_reply_repeated_identical_requests()` --uses--> `ConversationState`  [INFERRED]
  tests/test_idempotency.py → app/conversation.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Candidate Bot API Contract** — challenge_testing_brief_context_endpoint, challenge_testing_brief_tick_endpoint, challenge_testing_brief_reply_endpoint [EXTRACTED 1.00]
- **Vera 4-Context Architecture** — challenge_brief_category_context, challenge_brief_merchant_context, challenge_brief_trigger_context, challenge_brief_customer_context [EXTRACTED 1.00]

## Communities (56 total, 11 thin omitted)

### Community 0 - "test_tick.py"
Cohesion: 0.05
Nodes (52): clean_state(), fixture, Tests for deterministic decision engine and /v1/tick endpoint., Identical trigger kind/content with a new time bucket sends again., One valid trigger returns 1 action rendered with deterministic fallback and…, Multiple available triggers are resolved, scored, and ranked., Zero triggers in request returns empty actions list., Expired triggers are skipped and not acted upon. (+44 more)

### Community 1 - "set_custom_llm_caller"
Cohesion: 0.05
Nodes (45): Set or clear a custom LLM caller (useful for unit tests and mocks)., set_custom_llm_caller(), clean_stores(), fixture, Tests for grounded LLM message composer, timeout handling, and deterministic…, When LLM composes research digest message citing real trial numbers, evidence…, Verify that category.voice dynamically configures tone and taboos without…, On LLM timeout, immediately falls back to deterministic template. (+37 more)

### Community 2 - "validate_message"
Cohesion: 0.08
Nodes (47): build_evidence_ledger(), _normalize_num(), Any, Generate string representations of a numeric value, supporting comma-formatting…, Extracts an evidence ledger from compact context or ResolvedContext: - Every…, Validates LLM-generated message body against the evidence ledger. Returns:…, validate_message(), parametrize (+39 more)

### Community 3 - "test_reply.py"
Cohesion: 0.05
Nodes (45): classify_intent(), Intent, str, Classifies merchant/customer free-text message into an Intent: 1. Hostile ->…, Adversarial inputs to POST /v1/reply are deterministically deflected to…, test_prompt_injection_in_v1_reply_endpoint(), clean_stores(), fixture (+37 more)

### Community 4 - "test_models_strictness.py"
Cohesion: 0.05
Nodes (41): ReplyRequest, TickRequest, Regression tests for strict Pydantic models, type enforcement, and payload…, Non-dict payload values (string, integer, list, bool) are rejected with 400…, Endpoint /v1/tick rejects non-list triggers or list with non-string elements., Endpoint /v1/reply rejects non-string conversation_id/message or string…, Endpoint /v1/context strictly rejects boolean True or False for version without…, ReplyRequest model and endpoint strictly reject turn_number=True/False. (+33 more)

### Community 5 - "test_output_validator_adversarial.py"
Cohesion: 0.09
Nodes (30): clean_state(), fixture, Adversarial and boundary tests for output_validator.py hardening. Tests…, Arbitrary number 4 must be rejected when not in context and not structural., Hallucinated percentage 7.2% must be rejected when not in evidence., Hallucinated price ₹99 must be rejected when active offer is ₹299., Invented names in salutations or body text must be rejected., Messages referencing inactive/expired offers must be rejected. (+22 more)

### Community 6 - "ConversationStateMachine"
Cohesion: 0.11
Nodes (18): ConversationStateMachine, generate_grounded_answer(), generate_positive_continuation(), normalize_state(), Any, Returns all formal conversation states., Returns terminal conversation states., Returns active conversation states. (+10 more)

### Community 7 - "ConversationState"
Cohesion: 0.11
Nodes (26): ConversationState, is_auto_reply_text(), Enum, Conversation state machine and context-aware transition engine for Magicpin…, Detects standard canned auto-reply messages., Formal specification of a deterministic state transition rule., _register_transition_rule(), TransitionRule (+18 more)

### Community 8 - "judge_simulator.py"
Cohesion: 0.05
Nodes (37): ABC, AnthropicProvider, Colors, create_provider(), DatasetLoader, DeepSeekProvider, GeminiProvider, GroqProvider (+29 more)

### Community 9 - "orchestrator.py"
Cohesion: 0.13
Nodes (24): process_tick(), Any, Tick decision orchestrator and proactive pipeline., Full pipeline for POST /v1/tick: 1. Gather and deduplicate available triggers.…, is_trigger_expired(), Any, Trigger expiration, scoring, and signal selection logic., Returns True if the trigger has passed its expires_at timestamp. Per testing-… (+16 more)

### Community 10 - "test_adversarial.py"
Cohesion: 0.09
Nodes (23): Any, Request validation helpers for Magicpin Vera bot endpoints., Validates the request payload for POST /v1/context. Raises…, validate_context_request(), fastapi, clean_state(), fixture, parametrize (+15 more)

### Community 11 - "Phase 2 — Test window (T0 → T0 + 60 min)"
Cohesion: 0.06
Nodes (31): API Call Examples — Judge ↔ Candidate Bot, Curl examples (for local testing), Example 1.1 — `GET /v1/healthz`, Example 1.2 — `GET /v1/metadata`, Example 1.3 — `POST /v1/context` (push CategoryContext), Example 1.4 — `POST /v1/context` (push MerchantContext), Example 1.5 — `POST /v1/context` (idempotency check — same version re-pushed), Example 1.6 — `POST /v1/context` (version bump replaces) (+23 more)

### Community 12 - "generate_dataset.py"
Cohesion: 0.21
Nodes (16): argparse, expand_customers(), expand_merchants(), expand_triggers(), load_seeds(), main(), Path, Generate 8 additional merchants per category (10 total per category, 50… (+8 more)

### Community 13 - "global_exception_handler"
Cohesion: 0.18
Nodes (13): global_exception_handler(), http_exception_handler(), Handle body and type validation errors strictly. Returns 400 with testing-…, Handle standard HTTP exceptions with clean JSON responses., Global exception fallback preventing crashes and returning clean JSON error., structured_logging_middleware(), validation_exception_handler(), Exception (+5 more)

### Community 14 - "app/replay_harness.py"
Cohesion: 0.05
Nodes (55): ContextEvent, EventType, NormalizedStep, parse_event(), Any, Enum, str, Deterministic replay harness for the complete Magicpin Vera bot lifecycle.… (+47 more)

### Community 15 - "test_adversarial_matrix.py"
Cohesion: 0.06
Nodes (28): fastapi_testclient, pytest, statistics, clean_state(), fixture, Full Adversarial Testing Matrix for Magicpin Vera Bot. Executes and measures:…, Verify that updated context versions immediately influence tick execution…, Tests missing links in trigger->merchant->category->customer chain and context… (+20 more)

### Community 16 - "test_context.py"
Cohesion: 0.08
Nodes (24): clean_store(), fixture, Tests for /v1/context endpoint and version-gating logic., Missing scope returns 400 with a clear error message., Invalid scope returns 400 with an error listing valid scopes., Ensure context_store is cleared before and after each test., Missing or empty context_id returns 400., Negative or zero version returns 400. (+16 more)

### Community 17 - "suppression.py"
Cohesion: 0.10
Nodes (19): get_suppression_record(), infer_frequency_window(), mark_suppressed(), _parse_suppression_dt(), Any, Time-aware suppression engine and frequency capping., Backwards compatibility set view., Marks key as suppressed with sent time and expiry/frequency window. (+11 more)

### Community 19 - "compose"
Cohesion: 0.40
Nodes (5): CategoryContext, compose(), CustomerContext, MerchantContext, TriggerContext

### Community 20 - "10 Case Studies — What "Good" Looks Like"
Cohesion: 0.14
Nodes (13): 10 Case Studies — What "Good" Looks Like, Case Study 10 — Pharmacies / Chronic Refill Reminder (customer-facing), Case Study 1 — Dentists / Research Digest (merchant-facing), Case Study 2 — Dentists / Recall Reminder (customer-facing), Case Study 3 — Salons / Active Planning (merchant-facing), Case Study 4 — Salons / Curious Ask (merchant-facing), Case Study 5 — Restaurants / IPL Match Day (merchant-facing), Case Study 6 — Restaurants / Active Planning Intent (merchant-facing) (+5 more)

### Community 21 - "Judge Harness"
Cohesion: 0.50
Nodes (4): POST /v1/context, Judge Harness, POST /v1/reply, POST /v1/tick

### Community 22 - "test_resolved_context.py"
Cohesion: 0.11
Nodes (23): Normalizes trigger, merchant, category, customer, versions, and relationship…, resolve_context(), Ambiguous trigger, merchant, or customer ownership must fail closed immediately., test_ambiguous_relationship_ownership_fails_closed(), Tests for Normalized ResolvedContext resolution, edge cases, and cross-merchant…, Context version tracking accurately reflects currently stored versions after…, Customer belongs to merchant A, but trigger for merchant B references that…, Merchant category_slug differs from category specified by trigger -> flagged in… (+15 more)

### Community 23 - "test_phase6_checklist.py"
Cohesion: 0.09
Nodes (19): clean_state(), fixture, Phase 6 Exit Checklist Verification Script. Executes all 8 checklist items: 1.…, Check 4: /v1/context -> Same version is idempotent., Check 5: /v1/tick -> Correct action and no-action., Check 6: /v1/reply -> Correct send/wait/end transitions., Check 7: LLM failure -> Deterministic fallback works., Check 8: Invalid LLM output -> Validator rejects/falls back. (+11 more)

### Community 26 - "test_idempotency.py"
Cohesion: 0.13
Nodes (18): Idempotency tests across /v1/context, /v1/tick, and /v1/reply for Magicpin Vera…, Replaying the exact same /v1/tick request produces 0 duplicate actions or sent…, Replaying identical /v1/reply returns wait without adding duplicate turns or…, Same turn number with changed message is a legitimate correction/update, not an…, Same message with changed turn number is a legitimate subsequent turn, not an…, Version bumps are accepted and replace context, while identical versions are…, Replaying a reply request where turn_number is omitted is properly deduplicated., Concurrent identical /v1/tick requests execute race-free and append exactly one… (+10 more)

### Community 27 - "test_phase1_endpoints.py"
Cohesion: 0.12
Nodes (15): clean_stores(), fixture, Verification that all Phase 1 endpoints continue passing without regression., GET /v1/healthz returns 200, uptime, and loaded context counts., GET /v1/metadata returns 200 with required team metadata., POST /v1/tick returns 200 and actions list stub., POST /v1/reply returns 200 and wait stub., POST /v1/teardown wipes state and returns 200. (+7 more)

### Community 28 - "decision/__init__.py"
Cohesion: 0.24
Nodes (12): Decision package combining taxonomy, suppression, resolution, scoring,…, Any, Context resolution helpers for merchant, category, customer, and trigger., Retrieve category payload from ContextStore., Retrieve customer payload from ContextStore with merchant scoping and ownership…, Retrieve trigger payload from ContextStore., Retrieve merchant payload from ContextStore., resolve_category() (+4 more)

### Community 31 - "decision_engine.py"
Cohesion: 0.10
Nodes (41): app_decision, Deterministic decision engine for Magicpin Vera bot. Compatibility façade re-…, get_all_canonical_kinds(), get_trigger_policy(), is_known_trigger_kind(), Canonical trigger taxonomy, policy registry, and alias resolution. Eliminates…, Deterministic specification for a trigger kind: - family: Target template…, Deterministically resolves any trigger kind, alias, or legacy name to its… (+33 more)

### Community 32 - "Any"
Cohesion: 0.15
Nodes (7): Any, Retrieve metadata (ack_id, updated_at, version) for stored context., Retrieve stored context item by (scope, context_id)., Retrieve conversation data copy by conversation_id., Create or update a conversation record., Record a turn in the conversation. Idempotent: - If an exact duplicate turn…, Record a sent message in the conversation, avoiding duplicate appends of the…

### Community 33 - "context_store"
Cohesion: 0.21
Nodes (13): Immediate replay after process_tick generates 0 duplicate actions or sent…, Triggers without explicit suppression_key are still idempotent against…, test_replay_after_process_tick(), test_replay_after_process_tick_without_explicit_suppression_key(), Identical customer IDs (e.g. 'c_vip') for multiple merchants remain strictly…, Trigger referencing a customer belonging to a different merchant fails closed., Suppressing a trigger for Merchant A does not suppress a trigger for Merchant B., test_cross_merchant_customer_mismatch_fails_closed() (+5 more)

### Community 34 - "ContextStore"
Cohesion: 0.18
Nodes (7): ContextStore, Return counts of loaded contexts by scope (for /v1/healthz)., Clear all stored contexts (for /v1/teardown)., Return total number of stored context items., Thread-safe in-memory store for context entities keyed by (scope, context_id).…, Unit tests for ContextStore and ConversationStore in app/store.py., test_context_store_version_gating()

### Community 35 - "ConversationStore"
Cohesion: 0.17
Nodes (7): ConversationStore, Thread-safe in-memory store for active conversations keyed by conversation_id.…, Update last action taken in conversation., Update conversation state., Clear all conversation records (for /v1/teardown)., Return count of active conversations., test_conversation_store()

### Community 36 - "store.py"
Cohesion: 0.22
Nodes (9): format_gate_response(), Enum, str, In-memory state management for Magicpin Vera bot., Maps VersionGateResult to exact contract response per testing-brief.md §2.1: -…, Store context according to version gating rules: - no existing record -> store…, VersionGateResult, threading (+1 more)

### Community 37 - "test_isolation_audit.py"
Cohesion: 0.16
Nodes (14): concurrent_futures, Dedicated cross-merchant isolation audit test suite for Magicpin Vera bot.…, Merchant A's compositions never use Merchant B's offers, prices, or performance…, Merchant B cannot query Merchant A's conversation or trigger., Grounded question answers only pull from the same conversation's previous…, Five distinct merchants running simultaneous multi-turn conversations maintain…, Helper to seed Merchant A (Dentist) and Merchant B (Salon) with full context., Merchant A cannot use Merchant B's category; category mismatch fails closed. (+6 more)

### Community 38 - "test_determinism.py"
Cohesion: 0.18
Nodes (9): clean_state(), fixture, Replay determinism tests for Magicpin Vera bot. Asserts: 1. Sending identical…, Identical reply messages must yield identical state transitions and action…, Specifically test that LLM wording variations in composer.py NEVER change which…, Identical contexts and multiple triggers must produce identical trigger…, test_llm_nondeterminism_does_not_affect_trigger_selection_or_action_decision(), test_reply_state_machine_replay_determinism() (+1 more)

### Community 39 - "ContextRequest"
Cohesion: 0.22
Nodes (9): ContextRequest, ContextRequest model raises ValidationError when version is a boolean., ContextRequest model raises ValidationError when version is a float., ContextRequest model rejects extra kwargs with extra_forbidden ValidationError., validate_context_request in validators.py handles both ContextRequest models…, test_boolean_used_as_version_rejected_by_model(), test_float_used_as_version_rejected_by_model(), test_unknown_fields_rejected_by_context_model() (+1 more)

### Community 40 - "tick"
Cohesion: 0.25
Nodes (8): Receives scoped context entity (category, merchant, customer, or trigger).…, Periodic wake-up called by the judge: 1. Gathers available triggers from…, Processes simulated merchant or customer reply using rule-based state machine…, receive_context(), reply(), tick(), TickResponse, post

### Community 41 - "main.py"
Cohesion: 0.15
Nodes (27): healthz(), metadata(), Returns bot liveness status, uptime in seconds, and loaded context counts., Returns team information, bot model, approach summary, and version., Wipes in-memory context, active suppressions, and resets conversation state., teardown(), ContextErrorResponse, ContextResponse (+19 more)

### Community 42 - "EvidenceLedger"
Cohesion: 0.40
Nodes (4): EvidenceLedger, Structured ledger of all verified facts permitted for a message., Directly verifies validate_message rejects each of the 5 security violation…, test_output_validator_rejects_all_five_security_violation_classes()

### Community 43 - "clean_state"
Cohesion: 0.67
Nodes (3): clean_state(), fixture, Reset all state stores before and after each test.

### Community 45 - "typing"
Cohesion: 0.40
Nodes (4): Settings, BaseSettings, pydantic_settings, typing

### Community 46 - "build_compact_context"
Cohesion: 0.06
Nodes (49): build_compact_context(), build_system_prompt(), compose_message(), _default_llm_call(), Any, Grounded LLM message composer for Magicpin Vera bot. Builds compact context…, Builds the master system prompt by injecting dynamic Python variables: -…, Sanitize user-controlled text fields against prompt injection markers. (+41 more)

### Community 48 - "clean_state"
Cohesion: 0.67
Nodes (3): clean_state(), fixture, Wipe all stores before and after each audit test.

### Community 49 - "clean_stores"
Cohesion: 0.67
Nodes (3): clean_stores(), fixture, Wipe in-memory context and conversation stores before/after each test.

### Community 50 - "reset_stores"
Cohesion: 0.67
Nodes (3): fixture, Reset context store, conversation store, and auto-reply tracking before every…, reset_stores()

### Community 51 - "test_matrix_e2e_reply_execution"
Cohesion: 0.67
Nodes (3): parametrize, End-to-end execution of every state x intent combination via POST /v1/reply.…, test_matrix_e2e_reply_execution()

## Knowledge Gaps
- **52 isolated node(s):** `Colors`, `Example 1.1 — `GET /v1/healthz``, `Example 1.2 — `GET /v1/metadata``, `Example 1.3 — `POST /v1/context` (push CategoryContext)`, `Example 1.4 — `POST /v1/context` (push MerchantContext)` (+47 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 536 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **11 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `set_custom_llm_caller()` connect `set_custom_llm_caller` to `validate_message`, `test_output_validator_adversarial.py`, `test_determinism.py`, `test_adversarial.py`, `build_compact_context`, `test_adversarial_matrix.py`, `app/replay_harness.py`, `test_phase6_checklist.py`?**
  _High betweenness centrality (0.061) - this node is a cross-community bridge._
- **Why does `ConversationState` connect `ConversationState` to `test_reply.py`, `test_isolation_audit.py`, `ConversationStateMachine`, `test_concurrent_duplicate_reply_requests`, `test_adversarial_matrix.py`, `test_matrix_e2e_reply_execution`, `test_reply_hostile_intent`, `test_multiturn_out_of_order_turn`, `test_idempotency.py`?**
  _High betweenness centrality (0.036) - this node is a cross-community bridge._
- **Why does `ConversationStore` connect `ConversationStore` to `Any`, `context_store`, `ContextStore`, `store.py`, `test_resolved_context.py`?**
  _High betweenness centrality (0.034) - this node is a cross-community bridge._
- **Are the 29 inferred relationships involving `ConversationState` (e.g. with `test_matrix_7_reply_state_machine_and_auto_reply_loop()` and `test_matrix_8_parallel_conversations_and_out_of_order_turns()`) actually correct?**
  _`ConversationState` has 29 INFERRED edges - model-reasoned connections that need verification._
- **Are the 5 inferred relationships involving `build_compact_context()` (e.g. with `metadata()` and `.category()`) actually correct?**
  _`build_compact_context()` has 5 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Colors`, `Example 1.1 — `GET /v1/healthz``, `Example 1.2 — `GET /v1/metadata`` to the rest of the system?**
  _52 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `test_tick.py` be split into smaller, more focused modules?**
  _Cohesion score 0.05079825834542816 - nodes in this community are weakly interconnected._