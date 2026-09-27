# Graph Report - magicpin-ai-challenge  (2026-09-27)

## Corpus Check
- 749 files · ~96,097 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 6 file(s) not represented in the graph (top: (none) 3, .example 2, .log 1)

## Summary
- 681 nodes · 1212 edges · 43 communities (30 shown, 13 thin omitted)
- Extraction: 97% EXTRACTED · 3% INFERRED · 0% AMBIGUOUS · INFERRED: 32 edges (avg confidence: 0.92)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `0e82f42b`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- decision_engine.py
- main.py
- validate_message
- test_reply.py
- store.py
- test_tick.py
- test_phase1_endpoints.py
- set_custom_llm_caller
- judge_simulator.py
- test_context.py
- test_phase6_checklist.py
- Phase 2 — Test window (T0 → T0 + 60 min)
- generate_dataset.py
- composer.py
- build_compact_context
- test_adversarial_matrix.py
- Magicpin "Vera" Merchant-Growth Bot Service
- SuppressionEngine
- BotClient
- compose
- 10 Case Studies — What "Good" Looks Like
- Judge Harness
- global_exception_handler
- test_edge_cases.py
- Vera (Merchant AI Assistant)
- Aryan Client
- app/__init__.py
- tests/__init__.py
- conversation.py
- Architecture Documentation
- Render Blueprint
- test_security.py
- typing
- validate_context_request
- tick
- healthz
- test_check_7_llm_failure_deterministic_fallback
- ContextScope
- test_matrix_9_reliability_simulations
- test_matrix_10_load_benchmark_latency_distribution
- test_concurrent_load_benchmark_10_req_per_sec
- test_llm_nondeterminism_does_not_affect_trigger_selection_or_action_decision
- clean_state

## God Nodes (most connected - your core abstractions)
1. `set_custom_llm_caller()` - 43 edges
2. `validate_message()` - 28 edges
3. `build_evidence_ledger()` - 26 edges
4. `build_compact_context()` - 20 edges
5. `compose_message()` - 18 edges
6. `process_tick()` - 17 edges
7. `ConversationState` - 15 edges
8. `render_template()` - 15 edges
9. `setup_standard_context()` - 15 edges
10. `seed_merchant_and_category()` - 15 edges

## Surprising Connections (you probably didn't know these)
- `4-Context Composition Framework` --implements--> `Vera (Merchant AI Assistant)`  [INFERRED]
  engagement-design.md → challenge-brief.md
- `healthz()` --uses--> `ContextsLoaded`  [INFERRED]
  magicpin-bot/app/main.py → magicpin-bot/app/models.py
- `healthz()` --uses--> `HealthzResponse`  [INFERRED]
  magicpin-bot/app/main.py → magicpin-bot/app/models.py
- `metadata()` --uses--> `MetadataResponse`  [INFERRED]
  magicpin-bot/app/main.py → magicpin-bot/app/models.py
- `tick()` --uses--> `TickRequest`  [INFERRED]
  magicpin-bot/app/main.py → magicpin-bot/app/models.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Candidate Bot API Contract** — challenge_testing_brief_context_endpoint, challenge_testing_brief_tick_endpoint, challenge_testing_brief_reply_endpoint [EXTRACTED 1.00]
- **Vera 4-Context Architecture** — challenge_brief_category_context, challenge_brief_merchant_context, challenge_brief_trigger_context, challenge_brief_customer_context [EXTRACTED 1.00]

## Communities (43 total, 13 thin omitted)

### Community 0 - "decision_engine.py"
Cohesion: 0.08
Nodes (50): check_suppressed(), _clean_entity_text(), is_trigger_expired(), process_tick(), Any, Deterministic decision engine for Magicpin Vera bot. Implements trigger…, Check if a suppression key is currently active., Retrieve merchant payload from ContextStore. (+42 more)

### Community 1 - "main.py"
Cohesion: 0.23
Nodes (17): BaseModel, datetime, fastapi_exceptions, fastapi_responses, ContextErrorResponse, ContextResponse, ContextsLoaded, ErrorResponse (+9 more)

### Community 2 - "validate_message"
Cohesion: 0.09
Nodes (34): build_evidence_ledger(), EvidenceLedger, _normalize_num(), Any, Structured ledger of all verified facts permitted for a message., Generate string representations of a numeric value, supporting comma-formatting…, Validates LLM-generated message body against the evidence ledger. Returns:…, Extracts an evidence ledger from compact context: - Every numeric fact from… (+26 more)

### Community 3 - "test_reply.py"
Cohesion: 0.06
Nodes (45): classify_intent(), ConversationState, Intent, is_auto_reply_text(), Enum, str, Classifies merchant/customer free-text message into an Intent: 1. Hostile ->…, Detects standard canned auto-reply messages. (+37 more)

### Community 4 - "store.py"
Cohesion: 0.06
Nodes (29): ContextStore, ConversationStore, format_gate_response(), Any, Enum, str, In-memory state management for Magicpin Vera bot., Return counts of loaded contexts by scope (for /v1/healthz). (+21 more)

### Community 5 - "test_tick.py"
Cohesion: 0.06
Nodes (42): mark_suppressed(), Mark a suppression key as active., clean_state(), fixture, Tests for deterministic decision engine and /v1/tick endpoint., One valid trigger returns 1 action rendered with deterministic fallback and…, Multiple available triggers are resolved, scored, and ranked., Zero triggers in request returns empty actions list. (+34 more)

### Community 6 - "test_phase1_endpoints.py"
Cohesion: 0.12
Nodes (15): clean_stores(), fixture, Verification that all Phase 1 endpoints continue passing without regression., GET /v1/healthz returns 200, uptime, and loaded context counts., GET /v1/metadata returns 200 with required team metadata., POST /v1/tick returns 200 and actions list stub., POST /v1/reply returns 200 and wait stub., POST /v1/teardown wipes state and returns 200. (+7 more)

### Community 7 - "set_custom_llm_caller"
Cohesion: 0.10
Nodes (24): Set or clear a custom LLM caller (useful for unit tests and mocks)., set_custom_llm_caller(), clean_stores(), fixture, Tests for grounded LLM message composer, timeout handling, and deterministic…, When LLM composes research digest message citing real trial numbers, evidence…, On LLM timeout, immediately falls back to deterministic template., When LLM returns non-JSON or malformed output, falls back cleanly. (+16 more)

### Community 8 - "judge_simulator.py"
Cohesion: 0.05
Nodes (38): ABC, dataclasses, AnthropicProvider, Colors, create_provider(), DatasetLoader, DeepSeekProvider, GeminiProvider (+30 more)

### Community 9 - "test_context.py"
Cohesion: 0.08
Nodes (24): clean_store(), fixture, Tests for /v1/context endpoint and version-gating logic., Missing scope returns 400 with a clear error message., Invalid scope returns 400 with an error listing valid scopes., Ensure context_store is cleared before and after each test., Missing or empty context_id returns 400., Negative or zero version returns 400. (+16 more)

### Community 10 - "test_phase6_checklist.py"
Cohesion: 0.11
Nodes (17): clean_state(), fixture, Phase 6 Exit Checklist Verification Script. Executes all 8 checklist items: 1.…, Check 4: /v1/context -> Same version is idempotent., Check 5: /v1/tick -> Correct action and no-action., Check 6: /v1/reply -> Correct send/wait/end transitions., Check 8: Invalid LLM output -> Validator rejects/falls back., Check 1: /v1/healthz -> 200 consistently. (+9 more)

### Community 11 - "Phase 2 — Test window (T0 → T0 + 60 min)"
Cohesion: 0.06
Nodes (31): API Call Examples — Judge ↔ Candidate Bot, Curl examples (for local testing), Example 1.1 — `GET /v1/healthz`, Example 1.2 — `GET /v1/metadata`, Example 1.3 — `POST /v1/context` (push CategoryContext), Example 1.4 — `POST /v1/context` (push MerchantContext), Example 1.5 — `POST /v1/context` (idempotency check — same version re-pushed), Example 1.6 — `POST /v1/context` (version bump replaces) (+23 more)

### Community 12 - "generate_dataset.py"
Cohesion: 0.19
Nodes (17): argparse, expand_customers(), expand_merchants(), expand_triggers(), load_seeds(), main(), Path, Generate 8 additional merchants per category (10 total per category, 50… (+9 more)

### Community 13 - "composer.py"
Cohesion: 0.15
Nodes (15): hashlib, logging, compose_message(), _default_llm_call(), Grounded LLM message composer for Magicpin Vera bot. Builds compact context…, Default HTTP client calling Gemini or OpenAI chat completions if API key…, Attempts LLM message composition using compact_context. Enforces 8s timeout and…, count_ctas() (+7 more)

### Community 14 - "build_compact_context"
Cohesion: 0.14
Nodes (15): build_compact_context(), Any, Sanitize user-controlled text fields against prompt injection markers., Builds a small, grounded JSON object containing ONLY the facts the LLM is…, Dynamically resolve category voice policy from category.voice payload, falling…, resolve_voice_policy(), sanitize_text(), Verify that category.voice dynamically configures tone and taboos without… (+7 more)

### Community 15 - "test_adversarial_matrix.py"
Cohesion: 0.08
Nodes (25): concurrent_futures, fastapi_testclient, json, clean_state(), fixture, Full Adversarial Testing Matrix for Magicpin Vera Bot. Executes and measures:…, Verify that updated context versions immediately influence tick execution…, Tests missing links in trigger->merchant->category->customer chain and context… (+17 more)

### Community 16 - "Magicpin "Vera" Merchant-Growth Bot Service"
Cohesion: 0.12
Nodes (15): 1. Local Python Environment (Python 3.10+), 2. Run the Service Locally, 🏛️ Architecture Overview, ⚠️ Critical Architectural Constraint: Single-Instance Enforcement, ☁️ Deployment Guide (Render), 🐳 Docker Deployment, Magicpin "Vera" Merchant-Growth Bot Service, Option A: Deploy via Render Blueprint (Recommended) (+7 more)

### Community 17 - "SuppressionEngine"
Cohesion: 0.22
Nodes (5): Thread-safe storage for active suppression keys., Returns True if the key is already suppressed., Adds key to suppression set., Wipes all suppression keys., SuppressionEngine

### Community 19 - "compose"
Cohesion: 0.40
Nodes (5): CategoryContext, compose(), CustomerContext, MerchantContext, TriggerContext

### Community 20 - "10 Case Studies — What "Good" Looks Like"
Cohesion: 0.14
Nodes (13): 10 Case Studies — What "Good" Looks Like, Case Study 10 — Pharmacies / Chronic Refill Reminder (customer-facing), Case Study 1 — Dentists / Research Digest (merchant-facing), Case Study 2 — Dentists / Recall Reminder (customer-facing), Case Study 3 — Salons / Active Planning (merchant-facing), Case Study 4 — Salons / Curious Ask (merchant-facing), Case Study 5 — Restaurants / IPL Match Day (merchant-facing), Case Study 6 — Restaurants / Active Planning Intent (merchant-facing) (+5 more)

### Community 21 - "Judge Harness"
Cohesion: 0.50
Nodes (4): POST /v1/context, Judge Harness, POST /v1/reply, POST /v1/tick

### Community 22 - "global_exception_handler"
Cohesion: 0.18
Nodes (13): Exception, exception_handler, global_exception_handler(), http_exception_handler(), Handle body and type validation errors strictly. Returns 400 with testing-…, Handle standard HTTP exceptions with clean JSON responses., Global exception fallback preventing crashes and returning clean JSON error., structured_logging_middleware() (+5 more)

### Community 23 - "test_edge_cases.py"
Cohesion: 0.15
Nodes (12): clean_stores(), fixture, Tests for LLM hallucination edge cases and grounding validator fallback.…, Ensure clean isolated state before and after each test., LLM returns a statement with zero CTAs. Validator rejects and fallback fires., Unicode and Hindi/English mixed text in merchant name, customer name, and reply…, Very long text fields (near payload limit) -> confirm graceful handling, not an…, Null/missing optional fields across /v1/context, /v1/tick, /v1/reply -> confirm… (+4 more)

### Community 28 - "conversation.py"
Cohesion: 0.27
Nodes (7): ConversationStateMachine, Any, Conversation state machine and rule-based intent classifier for Magicpin Vera…, Manages conversation state transitions and produces reply actions., Reset auto-reply tracker., ReplyRequest, ReplyResponse

### Community 31 - "test_security.py"
Cohesion: 0.20
Nodes (9): clean_state(), fixture, Security and isolation tests for Magicpin Vera bot. Tests: 1. Prompt injection…, Prompt injection embedded in trigger or category payload text: - Confirms…, Attempt to make /v1/reply for conversation A leak sent_messages or context from…, Prompt injection embedded in merchant payload text (e.g. 'ignore previous…, test_cross_conversation_and_cross_merchant_isolation_in_reply(), test_prompt_injection_in_merchant_payload_no_leakage() (+1 more)

### Community 32 - "typing"
Cohesion: 0.25
Nodes (6): BaseSettings, fastapi, Settings, Request validation helpers for Magicpin Vera bot endpoints., pydantic_settings, typing

### Community 33 - "validate_context_request"
Cohesion: 0.29
Nodes (8): Receives scoped context entity (category, merchant, customer, or trigger).…, receive_context(), ContextRequest, Any, Validates the request payload for POST /v1/context. Raises…, validate_context_request(), Directly test uncovered error branches in app/validators.py., test_validators_py_edge_branches()

### Community 34 - "tick"
Cohesion: 0.29
Nodes (7): Periodic wake-up called by the judge: 1. Gathers available triggers from…, Processes simulated merchant or customer reply using rule-based state machine…, Wipes in-memory context, active suppressions, and resets conversation state., reply(), teardown(), tick(), post

### Community 35 - "healthz"
Cohesion: 0.40
Nodes (5): get, healthz(), metadata(), Returns bot liveness status, uptime in seconds, and loaded context counts., Returns team information, bot model, approach summary, and version.

### Community 37 - "ContextScope"
Cohesion: 0.67
Nodes (3): ContextScope, Enum, str

## Knowledge Gaps
- **63 isolated node(s):** `Colors`, `Example 1.1 — `GET /v1/healthz``, `Example 1.2 — `GET /v1/metadata``, `Example 1.3 — `POST /v1/context` (push CategoryContext)`, `Example 1.4 — `POST /v1/context` (push MerchantContext)` (+58 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 343 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **13 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `set_custom_llm_caller()` connect `set_custom_llm_caller` to `decision_engine.py`, `validate_message`, `test_check_7_llm_failure_deterministic_fallback`, `test_matrix_9_reliability_simulations`, `test_llm_nondeterminism_does_not_affect_trigger_selection_or_action_decision`, `clean_state`, `test_phase6_checklist.py`, `composer.py`, `test_adversarial_matrix.py`, `test_edge_cases.py`, `test_security.py`?**
  _High betweenness centrality (0.048) - this node is a cross-community bridge._
- **What connects `Colors`, `Example 1.1 — `GET /v1/healthz``, `Example 1.2 — `GET /v1/metadata`` to the rest of the system?**
  _63 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `decision_engine.py` be split into smaller, more focused modules?**
  _Cohesion score 0.07918552036199095 - nodes in this community are weakly interconnected._
- **Should `validate_message` be split into smaller, more focused modules?**
  _Cohesion score 0.09411764705882353 - nodes in this community are weakly interconnected._
- **Should `test_reply.py` be split into smaller, more focused modules?**
  _Cohesion score 0.05603864734299517 - nodes in this community are weakly interconnected._
- **Should `store.py` be split into smaller, more focused modules?**
  _Cohesion score 0.057971014492753624 - nodes in this community are weakly interconnected._
- **Should `test_tick.py` be split into smaller, more focused modules?**
  _Cohesion score 0.06312292358803986 - nodes in this community are weakly interconnected._