import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings
from app.conversation import conversation_state_machine
from app.decision_engine import process_tick, suppression_engine
from app.models import (
    ContextRequest,
    ContextResponse,
    ContextsLoaded,
    ErrorResponse,
    HealthzResponse,
    MetadataResponse,
    ReplyRequest,
    ReplyResponse,
    StaleVersionResponse,
    TeardownResponse,
    TickRequest,
    TickResponse,
)
from app.store import VersionGateResult, context_store, conversation_store
from app.validators import validate_context_request

# ---------------------------------------------------------------------------
# Logging Setup
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(message)s",
)
logger = logging.getLogger("magicpin-bot")

# Track startup time for uptime calculation
START_TIME = time.time()

# ---------------------------------------------------------------------------
# FastAPI Application Initialization
# ---------------------------------------------------------------------------
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="FastAPI service for the Magicpin Vera merchant-growth bot challenge.",
    docs_url="/docs",
    redoc_url="/redoc",
)


# ---------------------------------------------------------------------------
# Structured Logging Middleware
# ---------------------------------------------------------------------------
@app.middleware("http")
async def structured_logging_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = request_id
    start_time = time.perf_counter()

    try:
        response = await call_next(request)
        latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
        log_payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "latency_ms": latency_ms,
        }
        logger.info(json.dumps(log_payload))
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time"] = f"{latency_ms}ms"
        return response
    except Exception as exc:
        latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
        log_payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status_code": 500,
            "latency_ms": latency_ms,
            "error": str(exc),
        }
        logger.error(json.dumps(log_payload))
        raise exc


# ---------------------------------------------------------------------------
# Exception Handlers
# ---------------------------------------------------------------------------
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    Handle body and type validation errors strictly.
    Returns 400 for malformed JSON and 422 for unhandled schema/type mismatches.
    """
    req_id = getattr(request.state, "request_id", None)
    errors = exc.errors()

    is_malformed_json = any(
        err.get("type") in ("json_invalid", "json_type") for err in errors
    )
    status_code = (
        status.HTTP_400_BAD_REQUEST
        if is_malformed_json
        else 422
    )
    error_type = "malformed_json" if is_malformed_json else "validation_error"

    serialized_errors = []
    for err in errors:
        serialized_errors.append(
            {
                "loc": list(err.get("loc", [])),
                "msg": str(err.get("msg", "")),
                "type": str(err.get("type", "")),
            }
        )

    return JSONResponse(
        status_code=status_code,
        content={
            "error": error_type,
            "detail": serialized_errors,
            "request_id": req_id,
        },
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Handle standard HTTP exceptions with clean JSON responses."""
    req_id = getattr(request.state, "request_id", None)
    if isinstance(exc.detail, dict):
        content = {"request_id": req_id, **exc.detail}
    else:
        error_name = "bad_request" if exc.status_code == 400 else "http_error"
        content = {
            "error": error_name,
            "detail": exc.detail,
            "request_id": req_id,
        }
    return JSONResponse(
        status_code=exc.status_code,
        content=content,
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """
    Global exception fallback preventing crashes and returning clean JSON error.
    """
    req_id = getattr(request.state, "request_id", None) or str(uuid.uuid4())
    logger.exception(f"Unhandled error for request {req_id}: {exc}")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "internal_server_error",
            "detail": "An internal server error occurred.",
            "request_id": req_id,
        },
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get(
    "/v1/healthz",
    response_model=HealthzResponse,
    tags=["Liveness"],
    summary="Liveness probe",
)
async def healthz():
    """Returns bot liveness status, uptime in seconds, and loaded context counts."""
    uptime = int(time.time() - START_TIME)
    counts = context_store.get_counts()
    return HealthzResponse(
        status="ok",
        uptime_seconds=uptime,
        contexts_loaded=ContextsLoaded(
            category=counts.get("category", 0),
            merchant=counts.get("merchant", 0),
            customer=counts.get("customer", 0),
            trigger=counts.get("trigger", 0),
        ),
    )


@app.get(
    "/v1/metadata",
    response_model=MetadataResponse,
    tags=["Identity"],
    summary="Bot metadata and identification",
)
async def metadata():
    """Returns team information, bot model, approach summary, and version."""
    return MetadataResponse(
        team_name=settings.TEAM_NAME,
        team_members=settings.TEAM_MEMBERS,
        model=os.environ.get("LLM_MODEL") or ("gemini-1.5-flash" if os.environ.get("GEMINI_API_KEY", "").startswith("AIza") or os.environ.get("LLM_PROVIDER") == "gemini" else settings.MODEL),
        approach=settings.APPROACH,
        contact_email=settings.CONTACT_EMAIL,
        version=settings.APP_VERSION,
    )


@app.post(
    "/v1/context",
    response_model=ContextResponse,
    status_code=status.HTTP_200_OK,
    tags=["Context"],
    summary="Receive a context push",
    responses={
        400: {"model": ErrorResponse, "description": "Validation error (missing/invalid fields)"},
        409: {"model": StaleVersionResponse, "description": "Stale version conflict"},
    },
)
async def receive_context(body: ContextRequest):
    """
    Receives scoped context entity (category, merchant, customer, or trigger).
    Validates request payload and applies version-gating:
    - no existing record          -> store it, return 200
    - incoming version > current  -> replace, return 200
    - incoming version == current -> no-op, return 200 (idempotent)
    - incoming version < current  -> return 409 with {"error": "stale_version"}
    - invalid/missing scope, context_id, version, payload -> return 400
    """
    validated = validate_context_request(body)
    scope = validated["scope"]
    context_id = validated["context_id"]
    version = validated["version"]
    payload = validated["payload"]

    gate_result, current_version = context_store.set(
        scope=scope,
        context_id=context_id,
        version=version,
        payload=payload,
    )

    if gate_result == VersionGateResult.STALE:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "error": "stale_version",
                "accepted": False,
                "reason": "stale_version",
                "current_version": current_version,
                "incoming_version": version,
                "detail": f"Incoming version {version} is older than current version {current_version}.",
            },
        )

    ack_id = f"ack_{uuid.uuid4().hex[:8]}"
    return ContextResponse(
        accepted=True,
        ack_id=ack_id,
        stored_at=datetime.now(timezone.utc).isoformat(),
    )


@app.post(
    "/v1/tick",
    response_model=TickResponse,
    status_code=status.HTTP_200_OK,
    tags=["Decision"],
    summary="Periodic wake-up for proactive engagement",
)
async def tick(body: TickRequest):
    """
    Periodic wake-up called by the judge:
    1. Gathers available triggers from context store.
    2. Resolves merchant, category, and customer entities.
    3. Checks trigger expiry and suppression.
    4. Ranks triggers and selects the strongest signal per merchant.
    5. Renders deterministic templates (capped at 20 actions).
    """
    actions = process_tick(
        available_trigger_ids=body.available_triggers,
        now=body.now,
        context_store=context_store,
        conversation_store=conversation_store,
    )
    return TickResponse(actions=actions)


@app.post(
    "/v1/reply",
    response_model=ReplyResponse,
    status_code=status.HTTP_200_OK,
    tags=["Conversation"],
    summary="Process counterpart reply",
)
async def reply(body: ReplyRequest):
    """
    Processes simulated merchant or customer reply using rule-based
    state machine and intent classification.
    """
    return conversation_state_machine.process_reply(
        request=body,
        conversation_store=conversation_store,
    )


@app.post(
    "/v1/teardown",
    response_model=TeardownResponse,
    status_code=status.HTTP_200_OK,
    tags=["Lifecycle"],
    summary="Reset bot state at end of test",
)
async def teardown():
    """Wipes in-memory context, active suppressions, and resets conversation state."""
    context_store.clear()
    conversation_store.clear()
    suppression_engine.clear()
    return TeardownResponse(
        status="ok",
        message="State reset complete",
    )
