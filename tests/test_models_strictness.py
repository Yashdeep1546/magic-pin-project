"""Regression tests for strict Pydantic models, type enforcement, and payload validation.

Covers:
- Wrong JSON types (scope, context_id, version, payload, tick/reply fields)
- Booleans used as versions (True/False rejected, not coerced to 1/0)
- Floats used as versions (1.0, 1.5, 2.9 rejected, not truncated)
- Nulls in required vs optional fields
- Unknown fields (rejected via model_config extra='forbid')
- Malformed payloads (syntax errors, non-object JSON bodies, empty dicts)
- Exact response contract preservation
"""

import pytest
from fastapi.exceptions import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.models import (
    ContextErrorResponse,
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
    TickAction,
    TickRequest,
    TickResponse,
)
from app.store import context_store, conversation_store
from app.validators import validate_context_request

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_stores():
    """Wipe in-memory context and conversation stores before/after each test."""
    context_store.clear()
    conversation_store.clear()
    yield
    context_store.clear()
    conversation_store.clear()


# ===========================================================================
# 1. Wrong JSON Types
# ===========================================================================

def test_scope_wrong_json_types():
    """Non-string and unsupported scope values are rejected with 400 invalid_scope."""
    bad_scopes = [123, True, False, ["category"], {"scope": "category"}, 3.14, "unsupported_scope"]
    for bad_scope in bad_scopes:
        resp = client.post(
            "/v1/context",
            json={
                "scope": bad_scope,
                "context_id": "c_type_test",
                "version": 1,
                "payload": {"name": "Test"},
            },
        )
        assert resp.status_code == 400, f"Expected 400 for scope={bad_scope}, got {resp.status_code}"
        data = resp.json()
        assert data["accepted"] is False
        assert data["reason"] in ("invalid_scope", "missing_scope")


def test_context_id_wrong_json_types():
    """Non-string or empty context_id values are rejected with 400 missing_context_id."""
    bad_ids = [123, True, False, ["c1"], {"id": "c1"}, 4.56, ""]
    for bad_id in bad_ids:
        resp = client.post(
            "/v1/context",
            json={
                "scope": "merchant",
                "context_id": bad_id,
                "version": 1,
                "payload": {"name": "Test"},
            },
        )
        assert resp.status_code == 400, f"Expected 400 for context_id={bad_id}, got {resp.status_code}"
        data = resp.json()
        assert data["accepted"] is False
        assert data["reason"] == "missing_context_id"


def test_version_wrong_json_types():
    """String, list, dict, and invalid types for version are rejected with 400 invalid_version."""
    bad_versions = ["1", "one", "v1", [1], {"version": 1}, 0, -5]
    for bad_ver in bad_versions:
        resp = client.post(
            "/v1/context",
            json={
                "scope": "category",
                "context_id": "dentists",
                "version": bad_ver,
                "payload": {"slug": "dentists"},
            },
        )
        assert resp.status_code == 400, f"Expected 400 for version={bad_ver}, got {resp.status_code}"
        data = resp.json()
        assert data["accepted"] is False
        assert data["reason"] == "invalid_version"


def test_payload_wrong_json_types():
    """Non-dict payload values (string, integer, list, bool) are rejected with 400 missing_payload."""
    bad_payloads = ["not_a_dict", 12345, [{"key": "val"}], True, False]
    for bad_p in bad_payloads:
        resp = client.post(
            "/v1/context",
            json={
                "scope": "merchant",
                "context_id": "m_test",
                "version": 1,
                "payload": bad_p,
            },
        )
        assert resp.status_code == 400, f"Expected 400 for payload={bad_p}, got {resp.status_code}"
        data = resp.json()
        assert data["accepted"] is False
        assert data["reason"] in ("missing_payload", "invalid_payload")


def test_tick_wrong_json_types():
    """Endpoint /v1/tick rejects non-list triggers or list with non-string elements."""
    # available_triggers as string
    r1 = client.post("/v1/tick", json={"available_triggers": "trg_001"})
    assert r1.status_code == 422
    assert r1.json()["error"] == "validation_error"

    # available_triggers as list of ints
    r2 = client.post("/v1/tick", json={"available_triggers": [123, 456]})
    assert r2.status_code == 422

    # now as integer
    r3 = client.post("/v1/tick", json={"now": 1234567890})
    assert r3.status_code == 422


def test_reply_wrong_json_types():
    """Endpoint /v1/reply rejects non-string conversation_id/message or string turn_number."""
    # conversation_id as int
    r1 = client.post("/v1/reply", json={"conversation_id": 999, "message": "hello"})
    assert r1.status_code == 422

    # message as int
    r2 = client.post("/v1/reply", json={"conversation_id": "conv_1", "message": 12345})
    assert r2.status_code == 422

    # turn_number as string
    r3 = client.post(
        "/v1/reply",
        json={"conversation_id": "conv_1", "message": "hello", "turn_number": "two"},
    )
    assert r3.status_code == 422


# ===========================================================================
# 2. Booleans Used as Versions
# ===========================================================================

def test_boolean_used_as_version_rejected_by_endpoint():
    """Endpoint /v1/context strictly rejects boolean True or False for version without coercing to 1 or 0."""
    for bool_val in (True, False):
        resp = client.post(
            "/v1/context",
            json={
                "scope": "merchant",
                "context_id": "m_bool_ver",
                "version": bool_val,
                "payload": {"name": "Bool Merchant"},
            },
        )
        assert resp.status_code == 400
        data = resp.json()
        assert data["accepted"] is False
        assert data["reason"] == "invalid_version"
        assert "version" in data["details"].lower()


def test_boolean_used_as_version_rejected_by_model():
    """ContextRequest model raises ValidationError when version is a boolean."""
    with pytest.raises(ValidationError) as exc_info:
        ContextRequest(
            scope="category",
            context_id="dentists",
            version=True,
            payload={"name": "Dentists"},
        )
    errors = exc_info.value.errors()
    assert any(err["loc"] == ("version",) for err in errors)


def test_boolean_used_as_turn_number_rejected():
    """ReplyRequest model and endpoint strictly reject turn_number=True/False."""
    # Via HTTP
    resp = client.post(
        "/v1/reply",
        json={"conversation_id": "conv_bool", "message": "test", "turn_number": True},
    )
    assert resp.status_code == 422

    # Via direct model instantiation
    with pytest.raises(ValidationError):
        ReplyRequest(conversation_id="conv_1", message="hello", turn_number=True)


# ===========================================================================
# 3. Floats Used as Versions
# ===========================================================================

def test_float_used_as_version_rejected_by_endpoint():
    """Endpoint /v1/context strictly rejects float version (1.0, 1.5, 2.9) without truncating to int."""
    for float_ver in (1.0, 1.5, 2.9):
        resp = client.post(
            "/v1/context",
            json={
                "scope": "merchant",
                "context_id": "m_float_ver",
                "version": float_ver,
                "payload": {"name": "Float Merchant"},
            },
        )
        assert resp.status_code == 400
        data = resp.json()
        assert data["accepted"] is False
        assert data["reason"] == "invalid_version"
        assert "version" in data["details"].lower()


def test_float_used_as_version_rejected_by_model():
    """ContextRequest model raises ValidationError when version is a float."""
    for float_val in (1.0, 1.5, 3.14):
        with pytest.raises(ValidationError) as exc_info:
            ContextRequest(
                scope="category",
                context_id="dentists",
                version=float_val,
                payload={"name": "Dentists"},
            )
        errors = exc_info.value.errors()
        assert any(err["loc"] == ("version",) for err in errors)


def test_float_used_as_turn_number_rejected():
    """ReplyRequest rejects float turn_number values."""
    resp = client.post(
        "/v1/reply",
        json={"conversation_id": "conv_flt", "message": "test", "turn_number": 2.5},
    )
    assert resp.status_code == 422

    with pytest.raises(ValidationError):
        ReplyRequest(conversation_id="conv_1", message="hello", turn_number=2.5)


# ===========================================================================
# 4. Nulls in Required vs Optional Fields
# ===========================================================================

def test_nulls_in_required_fields_rejected_by_context():
    """Null values in required fields (scope, context_id, version, payload) return 400."""
    # scope: null
    r1 = client.post(
        "/v1/context",
        json={"scope": None, "context_id": "c1", "version": 1, "payload": {"a": 1}},
    )
    assert r1.status_code == 400
    assert r1.json()["reason"] in ("invalid_scope", "missing_scope")

    # context_id: null
    r2 = client.post(
        "/v1/context",
        json={"scope": "merchant", "context_id": None, "version": 1, "payload": {"a": 1}},
    )
    assert r2.status_code == 400
    assert r2.json()["reason"] == "missing_context_id"

    # version: null
    r3 = client.post(
        "/v1/context",
        json={"scope": "merchant", "context_id": "c1", "version": None, "payload": {"a": 1}},
    )
    assert r3.status_code == 400
    assert r3.json()["reason"] in ("invalid_version", "missing_version")

    # payload: null
    r4 = client.post(
        "/v1/context",
        json={"scope": "merchant", "context_id": "c1", "version": 1, "payload": None},
    )
    assert r4.status_code == 400
    assert r4.json()["reason"] in ("missing_payload", "empty_payload")


def test_null_in_optional_field_accepted():
    """delivered_at as None or omitted is valid and stores context successfully."""
    resp = client.post(
        "/v1/context",
        json={
            "scope": "trigger",
            "context_id": "trg_null_opt",
            "version": 1,
            "payload": {"headline": "Optional fields test"},
            "delivered_at": None,
        },
    )
    assert resp.status_code == 200
    assert resp.json()["accepted"] is True


def test_nulls_in_reply_required_vs_optional():
    """Reply endpoint rejects null message/conversation_id but accepts null customer_id/turn_number."""
    # Null conversation_id -> 422
    r_bad1 = client.post("/v1/reply", json={"conversation_id": None, "message": "hello"})
    assert r_bad1.status_code == 422

    # Null message -> 422
    r_bad2 = client.post("/v1/reply", json={"conversation_id": "c1", "message": None})
    assert r_bad2.status_code == 422

    # Null customer_id & turn_number -> valid optional fields, 200
    r_ok = client.post(
        "/v1/reply",
        json={
            "conversation_id": "c_null_opt",
            "message": "hello there",
            "customer_id": None,
            "merchant_id": None,
            "turn_number": None,
            "received_at": None,
        },
    )
    assert r_ok.status_code == 200


# ===========================================================================
# 5. Unknown / Extra Fields
# ===========================================================================

def test_unknown_fields_rejected_by_context_endpoint():
    """Extra fields in /v1/context payload are forbidden and rejected with 400 unknown_field."""
    resp = client.post(
        "/v1/context",
        json={
            "scope": "category",
            "context_id": "dentists",
            "version": 1,
            "payload": {"slug": "dentists"},
            "rogue_field": "disallowed",
        },
    )
    assert resp.status_code == 400
    data = resp.json()
    assert data["accepted"] is False
    assert data["reason"] == "unknown_field"
    assert "rogue_field" in data["details"]


def test_unknown_fields_rejected_by_context_model():
    """ContextRequest model rejects extra kwargs with extra_forbidden ValidationError."""
    with pytest.raises(ValidationError) as exc_info:
        ContextRequest(
            scope="category",
            context_id="dentists",
            version=1,
            payload={"name": "test"},
            unknown_arg="illegal",
        )
    errors = exc_info.value.errors()
    assert any(err["type"] == "extra_forbidden" for err in errors)


def test_unknown_fields_rejected_by_tick():
    """Endpoint /v1/tick and TickRequest model forbid extra inputs."""
    resp = client.post(
        "/v1/tick",
        json={"available_triggers": [], "unexpected_extra": 999},
    )
    assert resp.status_code == 422
    errs = resp.json()["detail"]
    assert any(err["type"] == "extra_forbidden" for err in errs)

    with pytest.raises(ValidationError):
        TickRequest(available_triggers=[], rogue_param="bad")


def test_unknown_fields_rejected_by_reply():
    """Endpoint /v1/reply and ReplyRequest model forbid extra inputs."""
    resp = client.post(
        "/v1/reply",
        json={
            "conversation_id": "conv_extra",
            "message": "hello",
            "extra_key": "not_allowed",
        },
    )
    assert resp.status_code == 422
    errs = resp.json()["detail"]
    assert any(err["type"] == "extra_forbidden" for err in errs)

    with pytest.raises(ValidationError):
        ReplyRequest(conversation_id="c1", message="m", extra_data=True)


# ===========================================================================
# 6. Malformed Payloads & Non-Object JSON
# ===========================================================================

def test_malformed_json_syntax_returns_400():
    """Malformed raw JSON syntax returns 400 malformed_json."""
    resp = client.post(
        "/v1/context",
        content="{\"scope\": \"category\", unclosed_json...",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400
    assert resp.json()["accepted"] is False
    assert resp.json()["reason"] == "malformed_json"


def test_non_object_json_body_returns_400():
    """Passing a JSON array or JSON string as top-level body returns 400 malformed_request."""
    # JSON array
    r_arr = client.post(
        "/v1/context",
        content="[1, 2, 3]",
        headers={"Content-Type": "application/json"},
    )
    assert r_arr.status_code == 400
    assert r_arr.json()["accepted"] is False
    assert r_arr.json()["reason"] == "malformed_request"

    # JSON string
    r_str = client.post(
        "/v1/context",
        content="\"just a string\"",
        headers={"Content-Type": "application/json"},
    )
    assert r_str.status_code == 400
    assert r_str.json()["accepted"] is False
    assert r_str.json()["reason"] == "malformed_request"


def test_empty_payload_dictionary_domain_validation():
    """Empty payload {} passes Pydantic Dict type check but is rejected by domain validation with 400 empty_payload."""
    resp = client.post(
        "/v1/context",
        json={
            "scope": "category",
            "context_id": "dentists",
            "version": 1,
            "payload": {},
        },
    )
    assert resp.status_code == 400
    assert resp.json()["accepted"] is False
    assert resp.json()["reason"] == "empty_payload"


def test_validators_py_handles_direct_model_and_dict():
    """validate_context_request in validators.py handles both ContextRequest models and dicts cleanly."""
    # Clean ContextRequest model
    req = ContextRequest(
        scope="merchant",
        context_id="m_direct_valid",
        version=1,
        payload={"name": "Direct Valid"},
    )
    res = validate_context_request(req)
    assert res["scope"] == "merchant"
    assert res["context_id"] == "m_direct_valid"
    assert res["version"] == 1
    assert res["payload"] == {"name": "Direct Valid"}

    # Direct dict with empty payload raises HTTPException(400) empty_payload
    with pytest.raises(HTTPException) as exc_info:
        validate_context_request({
            "scope": "merchant",
            "context_id": "m_1",
            "version": 1,
            "payload": {},
        })
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["reason"] == "empty_payload"


# ===========================================================================
# 7. Exact Response Contracts Preservation
# ===========================================================================

def test_response_models_exact_contracts():
    """All response models preserve exact public schemas, fields, and defaults."""
    # ContextResponse
    cr = ContextResponse(ack_id="ack_123", stored_at="2026-04-26T10:00:00Z")
    assert cr.accepted is True
    assert cr.ack_id == "ack_123"
    assert cr.stored_at == "2026-04-26T10:00:00Z"
    assert cr.model_dump() == {
        "accepted": True,
        "ack_id": "ack_123",
        "stored_at": "2026-04-26T10:00:00Z",
    }

    # StaleVersionResponse
    svr = StaleVersionResponse(current_version=3)
    assert svr.accepted is False
    assert svr.reason == "stale_version"
    assert svr.current_version == 3

    # ContextErrorResponse
    cer = ContextErrorResponse(reason="invalid_scope", details="Invalid scope provided")
    assert cer.accepted is False
    assert cer.reason == "invalid_scope"
    assert cer.details == "Invalid scope provided"

    # TickAction & TickResponse
    act = TickAction(
        conversation_id="conv_1",
        merchant_id="m_1",
        body="Promo alert",
    )
    tr = TickResponse(actions=[act])
    assert len(tr.actions) == 1
    assert tr.actions[0].send_as == "vera"
    assert tr.actions[0].body == "Promo alert"

    # ReplyResponse
    rr_wait = ReplyResponse()
    assert rr_wait.action == "wait"
    assert rr_wait.body is None

    rr_send = ReplyResponse(action="send", body="Confirmed", cta="open_ended")
    assert rr_send.action == "send"
    assert rr_send.body == "Confirmed"

    # HealthzResponse
    hz = HealthzResponse(uptime_seconds=120)
    assert hz.status == "ok"
    assert hz.uptime_seconds == 120
    assert hz.contexts_loaded.merchant == 0

    # MetadataResponse
    mr = MetadataResponse(
        team_name="VeraAI",
        team_members=["Alice", "Bob"],
        model="gemini-flash",
        approach="Deterministic",
        contact_email="dev@example.com",
    )
    assert mr.team_name == "VeraAI"
    assert mr.version == "1.0.0"

    # TeardownResponse
    td = TeardownResponse()
    assert td.status == "ok"
    assert td.message == "State reset complete"

    # ErrorResponse
    er = ErrorResponse(error="bad_request", detail="Error detail", request_id="req_1")
    assert er.error == "bad_request"
    assert er.request_id == "req_1"
