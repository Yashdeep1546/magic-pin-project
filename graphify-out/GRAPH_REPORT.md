# Graph Report - magicpin-ai-challenge  (2026-09-27)

## Corpus Check
- 767 files · ~123,319 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 5 file(s) not represented in the graph (top: (none) 3, .example 2)

## Summary
- 1093 nodes · 2169 edges · 55 communities (44 shown, 11 thin omitted)
- Extraction: 94% EXTRACTED · 6% INFERRED · 0% AMBIGUOUS · INFERRED: 138 edges (avg confidence: 0.9)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `d7d28a34`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- test_transition_matrix.py
- models.py
- test_edge_cases.py
- test_reply.py
- ConversationStore
- test_tick.py
- test_phase1_endpoints.py
- set_custom_llm_caller
- LLMProvider
- test_context.py
- validate_message
- Phase 2 — Test window (T0 → T0 + 60 min)
- generate_dataset.py
- global_exception_handler
- app/replay_harness.py
- store.py
- Magicpin "Vera" Merchant-Growth Bot Service
- suppression.py
- BotClient
- compose
- 10 Case Studies — What "Good" Looks Like
- Judge Harness
- decision_engine.py
- resolve_customer
- Vera (Merchant AI Assistant)
- Aryan Client
- app/__init__.py
- tests/__init__.py
- orchestrator.py
- Architecture Documentation
- Render Blueprint
- test_taxonomy.py
- compose_message
- test_models_strictness.py
- ContextRequest
- JudgeSimulator
- ConversationState
- test_adversarial_matrix.py
- test_idempotency.py
- ConversationStateMachine
- score_trigger
- main.py
- process_tick
- build_evidence_ledger
- conversation.py
- config.py
- judge_simulator.py
- LLMScorer
- output_validator.py
- healthz
- DatasetLoader
- OllamaProvider
- TickRequest
- test_matrix_10_load_benchmark_latency_distribution
- test_concurrent_load_benchmark_10_req_per_sec

## God Nodes (most connected - your core abstractions)
1. `set_custom_llm_caller()` - 51 edges
2. `validate_message()` - 50 edges
3. `build_evidence_ledger()` - 48 edges
4. `ConversationState` - 43 edges
5. `build_compact_context()` - 37 edges
6. `render_template()` - 32 edges
7. `process_tick()` - 23 edges
8. `resolve_context()` - 22 edges
9. `compose_message()` - 20 edges
10. `Intent` - 20 edges

## Surprising Connections (you probably didn't know these)
- `test_select_strongest_signal_unit()` --calls--> `select_strongest_signal()`  [INFERRED]
  magicpin-bot/tests/test_tick.py → magicpin-bot/app/decision/scoring.py
- `4-Context Composition Framework` --implements--> `Vera (Merchant AI Assistant)`  [INFERRED]
  engagement-design.md → challenge-brief.md
- `test_concurrent_duplicate_reply_requests()` --uses--> `ConversationState`  [INFERRED]
  magicpin-bot/tests/test_idempotency.py → magicpin-bot/app/conversation.py
- `test_reply_repeated_identical_requests()` --uses--> `ConversationState`  [INFERRED]
  magicpin-bot/tests/test_idempotency.py → magicpin-bot/app/conversation.py
- `test_reply_same_message_with_changed_turn_number()` --uses--> `ConversationState`  [INFERRED]
  magicpin-bot/tests/test_idempotency.py → magicpin-bot/app/conversation.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Candidate Bot API Contract** — challenge_testing_brief_context_endpoint, challenge_testing_brief_tick_endpoint, challenge_testing_brief_reply_endpoint [EXTRACTED 1.00]
- **Vera 4-Context Architecture** — challenge_brief_category_context, challenge_brief_merchant_context, challenge_brief_trigger_context, challenge_brief_customer_context [EXTRACTED 1.00]

## Communities (55 total, 11 thin omitted)

### Community 0 - "test_transition_matrix.py"
Cohesion: 0.09
Nodes (21): fixture, parametrize, Formal transition matrix and conversation state machine test suite. Tests: 1.…, Verify that all active states allow all 6 standard intents., End-to-end execution of every state x intent combination via POST /v1/reply.…, Verify transition rationales and continuation messages reflect current state…, Duplicate turns must be rejected as no-ops (action: wait) without mutating…, Canned auto-replies or repeated turns break after 3 consecutive occurrences and… (+13 more)

### Community 1 - "models.py"
Cohesion: 0.24
Nodes (17): BaseModel, ContextErrorResponse, ContextResponse, ContextScope, ContextsLoaded, ErrorResponse, HealthzResponse, MetadataResponse (+9 more)

### Community 2 - "test_edge_cases.py"
Cohesion: 0.08
Nodes (35): Tests for LLM hallucination edge cases and grounding validator fallback.…, LLM reports an ungrounded CTR (e.g. 5.8% when reality is 2.1%). Validator must…, LLM substitutes an invented discount price (₹99 instead of active ₹299).…, LLM invents an offer service ('Teeth Whitening' / 'Root Canal') not present in…, LLM introduces an ungrounded third-party / customer name ('Rahul') when none…, LLM includes more than one CTA in the body (e.g. 2 questions). Validator…, LLM returns an empty string or whitespace body. Validator catches it and…, LLM attempts to send a message identical to one previously sent in this… (+27 more)

### Community 3 - "test_reply.py"
Cohesion: 0.06
Nodes (35): is_auto_reply_text(), Detects standard canned auto-reply messages., clean_stores(), fixture, Tests for conversation state machine and POST /v1/reply endpoint., Negative intent gracefully ends the conversation., Delay intent returns wait action with delay seconds., Hostile intent immediately ends conversation. (+27 more)

### Community 4 - "ConversationStore"
Cohesion: 0.06
Nodes (25): ContextStore, ConversationStore, Any, Enum, str, Retrieve metadata (ack_id, updated_at, version) for stored context., Retrieve stored context item by (scope, context_id)., Return counts of loaded contexts by scope (for /v1/healthz). (+17 more)

### Community 5 - "test_tick.py"
Cohesion: 0.05
Nodes (52): clean_state(), fixture, Tests for deterministic decision engine and /v1/tick endpoint., Identical trigger kind/content with a new time bucket sends again., One valid trigger returns 1 action rendered with deterministic fallback and…, Multiple available triggers are resolved, scored, and ranked., Zero triggers in request returns empty actions list., Expired triggers are skipped and not acted upon. (+44 more)

### Community 6 - "test_phase1_endpoints.py"
Cohesion: 0.12
Nodes (15): clean_stores(), fixture, Verification that all Phase 1 endpoints continue passing without regression., GET /v1/healthz returns 200, uptime, and loaded context counts., GET /v1/metadata returns 200 with required team metadata., POST /v1/tick returns 200 and actions list stub., POST /v1/reply returns 200 and wait stub., POST /v1/teardown wipes state and returns 200. (+7 more)

### Community 7 - "set_custom_llm_caller"
Cohesion: 0.05
Nodes (40): Set or clear a custom LLM caller (useful for unit tests and mocks)., set_custom_llm_caller(), clean_state(), fixture, Adversarial payload inside trigger details cannot override validator rules., test_prompt_injection_in_trigger_details(), clean_stores(), fixture (+32 more)

### Community 8 - "LLMProvider"
Cohesion: 0.08
Nodes (10): ABC, AnthropicProvider, create_provider(), DeepSeekProvider, GeminiProvider, GroqProvider, LLMProvider, OpenAIProvider (+2 more)

### Community 9 - "test_context.py"
Cohesion: 0.08
Nodes (24): clean_store(), fixture, Tests for /v1/context endpoint and version-gating logic., Missing scope returns 400 with a clear error message., Invalid scope returns 400 with an error listing valid scopes., Ensure context_store is cleared before and after each test., Missing or empty context_id returns 400., Negative or zero version returns 400. (+16 more)

### Community 10 - "validate_message"
Cohesion: 0.10
Nodes (29): _normalize_num(), Any, Generate string representations of a numeric value, supporting comma-formatting…, Validates LLM-generated message body against the evidence ledger. Returns:…, validate_message(), Arbitrary number 4 must be rejected when not in context and not structural., Hallucinated percentage 7.2% must be rejected when not in evidence., Hallucinated price ₹99 must be rejected when active offer is ₹299. (+21 more)

### Community 11 - "Phase 2 — Test window (T0 → T0 + 60 min)"
Cohesion: 0.06
Nodes (31): API Call Examples — Judge ↔ Candidate Bot, Curl examples (for local testing), Example 1.1 — `GET /v1/healthz`, Example 1.2 — `GET /v1/metadata`, Example 1.3 — `POST /v1/context` (push CategoryContext), Example 1.4 — `POST /v1/context` (push MerchantContext), Example 1.5 — `POST /v1/context` (idempotency check — same version re-pushed), Example 1.6 — `POST /v1/context` (version bump replaces) (+23 more)

### Community 12 - "generate_dataset.py"
Cohesion: 0.19
Nodes (17): argparse, expand_customers(), expand_merchants(), expand_triggers(), load_seeds(), main(), Path, Generate 8 additional merchants per category (10 total per category, 50… (+9 more)

### Community 13 - "global_exception_handler"
Cohesion: 0.18
Nodes (13): Exception, exception_handler, global_exception_handler(), http_exception_handler(), Handle body and type validation errors strictly. Returns 400 with testing-…, Handle standard HTTP exceptions with clean JSON responses., Global exception fallback preventing crashes and returning clean JSON error., structured_logging_middleware() (+5 more)

### Community 14 - "app/replay_harness.py"
Cohesion: 0.05
Nodes (55): copy, dataclasses, difflib, logging, ContextEvent, EventType, NormalizedStep, parse_event() (+47 more)

### Community 15 - "store.py"
Cohesion: 0.07
Nodes (37): fastapi_testclient, json, Grounded LLM message composer for Magicpin Vera bot. Builds compact context…, In-memory state management for Magicpin Vera bot., Adversarial security, prompt injection, cross-merchant isolation, and Unicode…, Verify Merchant A's actions and compact context never leak into Merchant B., Verify /v1/reply correctly processes Hindi Devanagari messages., Ingesting a large payload with 100+ offers and nested objects succeeds without… (+29 more)

### Community 16 - "Magicpin "Vera" Merchant-Growth Bot Service"
Cohesion: 0.12
Nodes (15): 1. Local Python Environment (Python 3.10+), 2. Run the Service Locally, 🏛️ Architecture Overview, ⚠️ Critical Architectural Constraint: Single-Instance Enforcement, ☁️ Deployment Guide (Render), 🐳 Docker Deployment, Magicpin "Vera" Merchant-Growth Bot Service, Option A: Deploy via Render Blueprint (Recommended) (+7 more)

### Community 17 - "suppression.py"
Cohesion: 0.10
Nodes (18): datetime, get_suppression_record(), mark_suppressed(), _parse_suppression_dt(), Any, Time-aware suppression engine and frequency capping., Backwards compatibility set view., Marks key as suppressed with sent time and expiry/frequency window. (+10 more)

### Community 19 - "compose"
Cohesion: 0.40
Nodes (5): CategoryContext, compose(), CustomerContext, MerchantContext, TriggerContext

### Community 20 - "10 Case Studies — What "Good" Looks Like"
Cohesion: 0.14
Nodes (13): 10 Case Studies — What "Good" Looks Like, Case Study 10 — Pharmacies / Chronic Refill Reminder (customer-facing), Case Study 1 — Dentists / Research Digest (merchant-facing), Case Study 2 — Dentists / Recall Reminder (customer-facing), Case Study 3 — Salons / Active Planning (merchant-facing), Case Study 4 — Salons / Curious Ask (merchant-facing), Case Study 5 — Restaurants / IPL Match Day (merchant-facing), Case Study 6 — Restaurants / Active Planning Intent (merchant-facing) (+5 more)

### Community 21 - "Judge Harness"
Cohesion: 0.50
Nodes (4): POST /v1/context, Judge Harness, POST /v1/reply, POST /v1/tick

### Community 22 - "decision_engine.py"
Cohesion: 0.27
Nodes (18): Deterministic decision engine for Magicpin Vera bot. Compatibility façade re-…, Decision package combining taxonomy, suppression, resolution, scoring,…, infer_frequency_window(), Infers frequency window in seconds from a suppression key format: - ISO Week…, _clean_entity_text(), Any, Deterministic template rendering for message generation., Renders deterministic message body, template name, template parameters, and… (+10 more)

### Community 23 - "resolve_customer"
Cohesion: 0.20
Nodes (12): Any, Retrieve category payload from ContextStore., Retrieve customer payload from ContextStore with merchant scoping and ownership…, Retrieve trigger payload from ContextStore., Retrieve merchant payload from ContextStore., resolve_category(), resolve_customer(), resolve_merchant() (+4 more)

### Community 28 - "orchestrator.py"
Cohesion: 0.10
Nodes (26): Tick decision orchestrator and proactive pipeline., Context resolution helpers for merchant, category, customer, and trigger., Normalizes trigger, merchant, category, customer, versions, and relationship…, resolve_context(), Trigger expiration, scoring, and signal selection logic., Canonical trigger taxonomy, policy registry, and alias resolution. Eliminates…, Normalized, validated context representation combining trigger, merchant,…, ResolvedContext (+18 more)

### Community 31 - "test_taxonomy.py"
Cohesion: 0.11
Nodes (26): glob, get_all_canonical_kinds(), get_trigger_policy(), is_known_trigger_kind(), Deterministic specification for a trigger kind: - family: Target template…, Deterministically resolves any trigger kind, alias, or legacy name to its…, Returns the canonical TriggerPolicy for any trigger kind or alias. Guarantees…, Returns True if the trigger kind is canonical or a known alias. (+18 more)

### Community 32 - "compose_message"
Cohesion: 0.10
Nodes (17): compose_message(), _default_llm_call(), Default HTTP client calling Gemini or OpenAI chat completions if API key…, Attempts LLM message composition using compact_context. Enforces 8s timeout and…, When LLM composes research digest message citing real trial numbers, evidence…, On LLM timeout, immediately falls back to deterministic template., When LLM returns non-JSON or malformed output, falls back cleanly., When LLM raises any unexpected exception, falls back immediately. (+9 more)

### Community 33 - "test_models_strictness.py"
Cohesion: 0.05
Nodes (41): clean_stores(), fixture, Regression tests for strict Pydantic models, type enforcement, and payload…, Non-dict payload values (string, integer, list, bool) are rejected with 400…, Endpoint /v1/tick rejects non-list triggers or list with non-string elements., Endpoint /v1/reply rejects non-string conversation_id/message or string…, Endpoint /v1/context strictly rejects boolean True or False for version without…, ReplyRequest model and endpoint strictly reject turn_number=True/False. (+33 more)

### Community 34 - "ContextRequest"
Cohesion: 0.18
Nodes (11): ContextRequest, Directly test uncovered error branches in app/validators.py., test_validators_py_edge_branches(), ContextRequest model raises ValidationError when version is a boolean., ContextRequest model raises ValidationError when version is a float., ContextRequest model rejects extra kwargs with extra_forbidden ValidationError., validate_context_request in validators.py handles both ContextRequest models…, test_boolean_used_as_version_rejected_by_model() (+3 more)

### Community 35 - "JudgeSimulator"
Cohesion: 0.37
Nodes (8): JudgeSimulator, main(), print_fail(), print_header(), print_info(), print_section(), print_success(), print_warn()

### Community 36 - "ConversationState"
Cohesion: 0.08
Nodes (20): ConversationState, Returns all formal conversation states., Returns terminal conversation states., Returns active conversation states., Tests hostile escalation, off-topic redirect, and repeated auto-reply loop…, Verify parallel conversations for same merchant and out-of-order turn numbers., test_matrix_7_reply_state_machine_and_auto_reply_loop(), test_matrix_8_parallel_conversations_and_out_of_order_turns() (+12 more)

### Community 37 - "test_adversarial_matrix.py"
Cohesion: 0.08
Nodes (22): concurrent_futures, clean_state(), fixture, parametrize, Full Adversarial Testing Matrix for Magicpin Vera Bot. Executes and measures:…, Verify that updated context versions immediately influence tick execution…, Tests missing links in trigger->merchant->category->customer chain and context…, Verifies that ungrounded offers and fake prices are rejected across every… (+14 more)

### Community 38 - "test_idempotency.py"
Cohesion: 0.08
Nodes (27): clean_state(), fixture, Idempotency tests across /v1/context, /v1/tick, and /v1/reply for Magicpin Vera…, Replaying the exact same /v1/tick request produces 0 duplicate actions or sent…, Replaying identical /v1/reply returns wait without adding duplicate turns or…, Same turn number with changed message is a legitimate correction/update, not an…, Same message with changed turn number is a legitimate subsequent turn, not an…, Reset all state stores before and after each test. (+19 more)

### Community 39 - "ConversationStateMachine"
Cohesion: 0.16
Nodes (16): ConversationStateMachine, generate_grounded_answer(), generate_positive_continuation(), normalize_state(), Any, Resolves full conversation context: - conversation's merchant - customer -…, Deterministically normalizes raw state strings and aliases to canonical…, Constructs a context-aware continuation for the active campaign/trigger when… (+8 more)

### Community 40 - "score_trigger"
Cohesion: 0.15
Nodes (17): is_trigger_expired(), Any, Returns True if the trigger has passed its expires_at timestamp. Per testing-…, Computes deterministic score for a trigger: score = urgency_score +…, Selects the single highest-priority, qualified trigger to act on, or None.…, score_trigger(), select_strongest_signal(), check_suppressed() (+9 more)

### Community 41 - "main.py"
Cohesion: 0.13
Nodes (18): fastapi, fastapi_exceptions, fastapi_responses, Receives scoped context entity (category, merchant, customer, or trigger).…, Periodic wake-up called by the judge: 1. Gathers available triggers from…, Wipes in-memory context, active suppressions, and resets conversation state., receive_context(), teardown() (+10 more)

### Community 42 - "process_tick"
Cohesion: 0.09
Nodes (32): process_tick(), Any, Full pipeline for POST /v1/tick: 1. Gather and deduplicate available triggers.…, Processes simulated merchant or customer reply using rule-based state machine…, reply(), Immediate replay after process_tick generates 0 duplicate actions or sent…, test_replay_after_process_tick(), clean_state() (+24 more)

### Community 43 - "build_evidence_ledger"
Cohesion: 0.08
Nodes (38): build_compact_context(), Any, Sanitize user-controlled text fields against prompt injection markers., Recursively sanitize untrusted context data structures., Builds a small, grounded JSON object containing ONLY the facts the LLM is…, Dynamically resolve category voice policy from category.voice payload, falling…, resolve_voice_policy(), sanitize_text() (+30 more)

### Community 44 - "conversation.py"
Cohesion: 0.16
Nodes (17): classify_intent(), Intent, Enum, str, Conversation state machine and context-aware transition engine for Magicpin…, Classifies merchant/customer free-text message into an Intent: 1. Hostile ->…, Formal specification of a deterministic state transition rule., _register_transition_rule() (+9 more)

### Community 45 - "config.py"
Cohesion: 0.50
Nodes (3): BaseSettings, Settings, pydantic_settings

### Community 46 - "judge_simulator.py"
Cohesion: 0.20
Nodes (10): Colors, print_hint(), print_reason(), print_score_bar(), magicpin AI Challenge — LLM-Powered Judge Simulator…, Score an action and display results., re, socket (+2 more)

### Community 47 - "LLMScorer"
Cohesion: 0.24
Nodes (7): LLMScorer, print_llm(), Scores messages using LLM and provides detailed reasoning., Score a message and return detailed results., Parse LLM JSON response., Basic fallback scoring., ScoreResult

### Community 48 - "output_validator.py"
Cohesion: 0.22
Nodes (8): hashlib, count_ctas(), extract_numbers_from_text(), is_security_violation(), Output grounding validator for Magicpin Vera bot. Enforces strict grounding…, Check if a string contains any prompt injection or security violation pattern., Extracts all numeric tokens from text, stripping currency and percent symbols.…, Counts distinct CTAs in message. Considers questions ('?') and imperative…

### Community 49 - "healthz"
Cohesion: 0.40
Nodes (5): get, healthz(), metadata(), Returns bot liveness status, uptime in seconds, and loaded context counts., Returns team information, bot model, approach summary, and version.

### Community 52 - "TickRequest"
Cohesion: 0.67
Nodes (3): TickRequest, Endpoint /v1/tick and TickRequest model forbid extra inputs., test_unknown_fields_rejected_by_tick()

## Knowledge Gaps
- **63 isolated node(s):** `Colors`, `Example 1.1 — `GET /v1/healthz``, `Example 1.2 — `GET /v1/metadata``, `Example 1.3 — `POST /v1/context` (push CategoryContext)`, `Example 1.4 — `POST /v1/context` (push MerchantContext)` (+58 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 542 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **11 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `set_custom_llm_caller()` connect `set_custom_llm_caller` to `compose_message`, `test_edge_cases.py`, `test_adversarial_matrix.py`, `build_evidence_ledger`, `app/replay_harness.py`, `store.py`?**
  _High betweenness centrality (0.064) - this node is a cross-community bridge._
- **Why does `ConversationState` connect `ConversationState` to `test_transition_matrix.py`, `test_reply.py`, `test_adversarial_matrix.py`, `test_idempotency.py`, `ConversationStateMachine`, `process_tick`, `conversation.py`?**
  _High betweenness centrality (0.044) - this node is a cross-community bridge._
- **Why does `ConversationStore` connect `ConversationStore` to `process_tick`, `orchestrator.py`, `store.py`?**
  _High betweenness centrality (0.021) - this node is a cross-community bridge._
- **Are the 29 inferred relationships involving `ConversationState` (e.g. with `test_matrix_7_reply_state_machine_and_auto_reply_loop()` and `test_matrix_8_parallel_conversations_and_out_of_order_turns()`) actually correct?**
  _`ConversationState` has 29 INFERRED edges - model-reasoned connections that need verification._
- **Are the 4 inferred relationships involving `build_compact_context()` (e.g. with `.category()` and `.customer()`) actually correct?**
  _`build_compact_context()` has 4 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Colors`, `Example 1.1 — `GET /v1/healthz``, `Example 1.2 — `GET /v1/metadata`` to the rest of the system?**
  _63 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `test_transition_matrix.py` be split into smaller, more focused modules?**
  _Cohesion score 0.09090909090909091 - nodes in this community are weakly interconnected._