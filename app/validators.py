"""Request validation helpers for Magicpin Vera bot endpoints."""

from typing import Any, Dict, Set
from fastapi import HTTPException, status

VALID_SCOPES: Set[str] = {"category", "merchant", "customer", "trigger"}


def validate_context_request(data: Any) -> Dict[str, Any]:
    """
    Validates the request payload for POST /v1/context.
    Raises HTTPException(status_code=400) with {"accepted": false, "reason": ..., "details": ...} on any validation error.
    """
    if data is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "accepted": False,
                "reason": "empty_payload",
                "details": "Request body cannot be empty.",
            },
        )

    # Extract dictionary representation
    if hasattr(data, "model_dump"):
        raw_dict = data.model_dump()
    elif isinstance(data, dict):
        raw_dict = data
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "accepted": False,
                "reason": "malformed_request",
                "details": "Request body must be a JSON object.",
            },
        )

    # 1. Validate scope
    scope = raw_dict.get("scope")
    if scope is None or not isinstance(scope, str) or not scope.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "accepted": False,
                "reason": "missing_scope",
                "details": f"Missing or empty scope. Must be one of: {', '.join(sorted(VALID_SCOPES))}",
            },
        )
    scope = scope.strip().lower()
    if scope not in VALID_SCOPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "accepted": False,
                "reason": "invalid_scope",
                "details": f"Invalid scope '{raw_dict.get('scope')}'. Must be one of: {', '.join(sorted(VALID_SCOPES))}",
            },
        )

    # 2. Validate context_id
    context_id = raw_dict.get("context_id")
    if context_id is None or not isinstance(context_id, str) or not context_id.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "accepted": False,
                "reason": "missing_context_id",
                "details": "Missing or invalid context_id. Must be a non-empty string.",
            },
        )
    context_id = context_id.strip()

    # 3. Validate version (must be integer >= 1, and not boolean)
    version = raw_dict.get("version")
    if version is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "accepted": False,
                "reason": "missing_version",
                "details": "Missing version. Version must be an integer >= 1.",
            },
        )
    if isinstance(version, bool) or not isinstance(version, int):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "accepted": False,
                "reason": "invalid_version",
                "details": "Invalid version. Version must be an integer >= 1.",
            },
        )
    if version < 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "accepted": False,
                "reason": "invalid_version",
                "details": f"Invalid version ({version}). Version must be >= 1.",
            },
        )

    # 4. Validate payload (must be non-empty dict)
    payload = raw_dict.get("payload")
    if payload is None or not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "accepted": False,
                "reason": "missing_payload",
                "details": "Missing or invalid payload. Must be a non-empty JSON object.",
            },
        )
    if len(payload) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "accepted": False,
                "reason": "empty_payload",
                "details": "Invalid payload: payload object cannot be empty.",
            },
        )

    delivered_at = raw_dict.get("delivered_at")

    return {
        "scope": scope,
        "context_id": context_id,
        "version": version,
        "payload": payload,
        "delivered_at": delivered_at,
    }
