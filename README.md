# Magicpin "Vera" Merchant-Growth Bot Service

[![Tests](https://img.shields.io/badge/Tests-118%20Passed-brightgreen)](magicpin-bot/tests/)
[![Coverage](https://img.shields.io/badge/Coverage-95%25-brightgreen)](magicpin-bot/tests/)
[![Architecture](https://img.shields.io/badge/Architecture-Deterministic%20%2B%20LLM%20Composer%20%2B%20Validation%20Gate-blue)](#architecture)

Production-ready, highly resilient FastAPI service for the **Magicpin AI Challenge** ("Vera" merchant-growth bot).

This service implements the 6 HTTP endpoints specified in the official challenge testing brief, featuring a **deterministic multi-signal decision engine**, a **grounded LLM message composer** with category voice policies, a **strict evidence ledger validation gate** that intercepts hallucinations, and an **adaptive conversation state machine** supporting Indic Hindi/Hinglish/Emoji intent recognition.

---

## ⚠️ Critical Architectural Constraint: Single-Instance Enforcement

> [!IMPORTANT]
> **ContextStore** and **ConversationStore** in `magicpin-bot/app/store.py` are in-memory, thread-safe, and **process-local**.
> 
> **MANDATORY DEPLOYMENT RULE**:
> 1. The service **MUST** run as a **single instance** (`numInstances: 1` on Render). Autoscaling and multiple replicas **MUST BE DISABLED**.
> 2. The Uvicorn process **MUST** run with **`--workers 1`**.
>
> **Why this matters**:
> If the platform runs 2+ instances or 2+ Uvicorn workers, incoming requests are load-balanced across isolated memory spaces. A context pushed via `/v1/context` to Instance A would not exist in Instance B when processing `/v1/tick`, causing silent state fragmentation and evaluation failures. The included `Dockerfile` and `render.yaml` enforce `--workers 1` and `numInstances: 1` by default.

---

## 🏛️ Architecture Overview

The system processes incoming ticks and replies through a four-layer defense-in-depth pipeline:

```mermaid
flowchart TD
    subgraph Client ["Judge Simulator / Platform Client"]
        TickReq["POST /v1/tick"]
        ReplyReq["POST /v1/reply"]
        ContextReq["POST /v1/context"]
    end

    subgraph Storage ["Thread-Safe In-Memory Stores (Single Instance)"]
        CS["ContextStore\n- Scope & Version Gating\n- Merchant / Category / Customer"]
        ConvS["ConversationStore\n- Turns, State, Sent Hashes\n- Strict Cross-Merchant Isolation"]
        SE["SuppressionEngine\n- Weekly Campaign Keys\n- Duplicate Prevention"]
    end

    subgraph Pipeline ["Processing Pipeline"]
        V["Request Validation & Schema Normalization\n(FastAPI / Pydantic v2)"]
        
        DE["Layer 1: Deterministic Decision Engine\n- Multi-Signal Scoring (Urgency + Perf + Recency)\n- Deterministic Tie-Breaking\n- Safe Entity Sanitization"]
        
        LLM["Layer 2: Grounded LLM Composer\n- Compact Context Boundaries\n- Allowed Facts & Forbidden Claims\n- Category Tone Policies\n- 8s Strict Timeout"]
        
        OV["Layer 3: Evidence Ledger Validation Gate\n- Numeric, Price, & Percentage Exactness\n- Active Offer Verification\n- Proper Noun & Anti-Hallucination Gate\n- Exactly 1 CTA Rule\n- SHA-256 Deduplication"]
        
        FB["Deterministic Template Fallback Engine\n(Activated on LLM Timeout / Hallucination / Failure)"]
        
        SM["Layer 4: Conversation State Machine\n- Intent Classifier (English + Hindi + Hinglish + Emojis)\n- Qualification -> Commit -> Action Transition\n- Repeated Auto-Reply Loop Breaking"]
    end

    ContextReq --> V --> CS
    TickReq --> V --> DE
    CS -. Context Resolution .-> DE
    SE -. Suppression Check .-> DE
    
    DE -->|Strongest Signal| LLM
    LLM -->|Candidate Response| OV
    OV -->|Pass Grounding| SendAction["Action: SEND (Grounded)"]
    OV -->|Fail Grounding / Timeout| FB
    FB -->|Deterministic Body| SendActionFallback["Action: SEND (Template Fallback)"]
    
    ReplyReq --> V --> SM
    ConvS <--> SM
    SM --> ReplyAction["Action: SEND / WAIT / END / REDIRECT"]
```

---

## 🚀 Quickstart & Local Setup

```bash
# Navigate to bot directory
cd magicpin-bot

# Install dependencies
pip install -r requirements.txt

# Run single-worker service
uvicorn app.main:app --host 0.0.0.0 --port 8080 --workers 1
```

---

## 🧪 Verification & Test Suite

```bash
cd magicpin-bot
python -m pytest tests/ --cov=app --cov-report=term-missing -v
```

All **118 tests pass** at **95% coverage** in < 2 seconds.

---

## ☁️ Deployment Guide (Render)

### Deploy via Render Blueprint

1. Push this repository to GitHub (`https://github.com/Yashdeep1546/magic-pin-project.git`).
2. In [Render Dashboard](https://dashboard.render.com/), select **New** -> **Blueprint**.
3. Select this repository. Render automatically reads `render.yaml`:
   - Enforces **`numInstances: 1`** (Autoscaling disabled).
   - Configures health check path `/v1/healthz`.
   - Runs single worker `uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1`.
4. In Environment Variables, supply `OPENAI_API_KEY`.
5. Click **Apply**.

---

## 📋 Public URLs for Judge Submission

| Resource | Public URL | Description |
| :--- | :--- | :--- |
| **Service Root** | `https://magicpin-vera-bot.onrender.com` | Base URL of deployed bot |
| **Health Check** | `https://magicpin-vera-bot.onrender.com/v1/healthz` | Uptime & context statistics (Target for judge liveness) |
| **Metadata** | `https://magicpin-vera-bot.onrender.com/v1/metadata` | Team identity, model (`gpt-4o`), and approach description |
| **Context Ingestion** | `https://magicpin-vera-bot.onrender.com/v1/context` | Version-gated context updates |
| **Tick Evaluation** | `https://magicpin-vera-bot.onrender.com/v1/tick` | Proactive trigger evaluation & message composition |
| **Merchant Reply** | `https://magicpin-vera-bot.onrender.com/v1/reply` | Reactive conversation state machine |
