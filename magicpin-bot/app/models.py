from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field, StrictBool, StrictInt, StrictStr


# ---------------------------------------------------------------------------
# Healthz & Metadata Models
# ---------------------------------------------------------------------------

class ContextsLoaded(BaseModel):
    category: StrictInt = 0
    merchant: StrictInt = 0
    customer: StrictInt = 0
    trigger: StrictInt = 0


class HealthzResponse(BaseModel):
    status: Literal["ok"] = "ok"
    uptime_seconds: StrictInt
    contexts_loaded: ContextsLoaded = Field(default_factory=ContextsLoaded)


class MetadataResponse(BaseModel):
    team_name: StrictStr
    team_members: List[StrictStr]
    model: StrictStr
    approach: StrictStr
    contact_email: StrictStr
    version: StrictStr = "1.0.0"


# ---------------------------------------------------------------------------
# Context Models
# ---------------------------------------------------------------------------

class ContextScope(str, Enum):
    CATEGORY = "category"
    MERCHANT = "merchant"
    CUSTOMER = "customer"
    TRIGGER = "trigger"


class ContextRequest(BaseModel):
    model_config = {"extra": "forbid"}

    scope: Literal["category", "merchant", "customer", "trigger"] = Field(
        ...,
        description="Target domain: category, merchant, customer, or trigger",
    )
    context_id: StrictStr = Field(
        ...,
        min_length=1,
        description="Unique identifier for the context entity",
    )
    version: StrictInt = Field(
        ...,
        ge=1,
        description="Monotonically increasing version number",
    )
    payload: Dict[StrictStr, Any] = Field(
        ...,
        description="Full context object payload",
    )
    delivered_at: Optional[StrictStr] = Field(
        default=None,
        description="ISO-8601 delivery timestamp",
    )


class ContextResponse(BaseModel):
    accepted: bool = True
    ack_id: StrictStr
    stored_at: StrictStr


class StaleVersionResponse(BaseModel):
    accepted: Literal[False] = False
    reason: Literal["stale_version"] = "stale_version"
    current_version: StrictInt


class ContextErrorResponse(BaseModel):
    accepted: Literal[False] = False
    reason: StrictStr
    details: StrictStr


# ---------------------------------------------------------------------------
# Tick Models
# ---------------------------------------------------------------------------

class TickRequest(BaseModel):
    model_config = {"extra": "forbid"}

    now: Optional[StrictStr] = Field(default=None, description="Current simulated ISO-8601 timestamp")
    available_triggers: List[StrictStr] = Field(default_factory=list, description="Currently active trigger identifiers")


class TickAction(BaseModel):
    conversation_id: StrictStr = Field(..., description="Unique conversation identifier")
    merchant_id: StrictStr = Field(..., description="Target merchant identifier")
    customer_id: Optional[StrictStr] = Field(default=None, description="Target customer identifier if applicable")
    send_as: Literal["vera", "system"] = Field(default="vera", description="Sender identity")
    trigger_id: Optional[StrictStr] = Field(default=None, description="Trigger context ID initiating this action")
    template_name: Optional[StrictStr] = Field(default=None, description="Message template name")
    template_params: List[StrictStr] = Field(default_factory=list, description="Parameters passed into template")
    body: StrictStr = Field(..., description="Rendered message text")
    cta: Optional[StrictStr] = Field(default=None, description="Call to action classification")
    suppression_key: Optional[StrictStr] = Field(default=None, description="Deduplication / frequency cap key")
    rationale: Optional[StrictStr] = Field(default=None, description="Explanation for sending this action")


class TickResponse(BaseModel):
    actions: List[TickAction] = Field(default_factory=list, description="List of proactive actions to initiate")


# ---------------------------------------------------------------------------
# Reply Models
# ---------------------------------------------------------------------------

class ReplyRequest(BaseModel):
    model_config = {"extra": "forbid"}

    conversation_id: StrictStr = Field(..., min_length=1, description="Conversation identifier being replied to")
    message: StrictStr = Field(..., min_length=1, description="Raw message received from counterpart")
    merchant_id: Optional[StrictStr] = Field(default=None, description="Associated merchant ID")
    customer_id: Optional[StrictStr] = Field(default=None, description="Associated customer ID")
    from_role: Optional[Literal["merchant", "customer", "user"]] = Field(default="merchant", description="Role of sender")
    received_at: Optional[StrictStr] = Field(default=None, description="ISO-8601 timestamp received")
    turn_number: Optional[StrictInt] = Field(default=None, ge=1, description="Turn number in the conversation")


class ReplyResponse(BaseModel):
    action: Literal["wait", "send", "end"] = Field(default="wait", description="Next turn action")
    wait_seconds: Optional[StrictInt] = Field(default=None, ge=0, description="Delay duration when action is 'wait'")
    rationale: Optional[StrictStr] = Field(default="Stub response: wait", description="Reasoning behind action")
    body: Optional[StrictStr] = Field(default=None, description="Message content when action is 'send'")
    cta: Optional[StrictStr] = Field(default=None, description="CTA format when action is 'send'")


# ---------------------------------------------------------------------------
# Teardown Models
# ---------------------------------------------------------------------------

class TeardownResponse(BaseModel):
    status: Literal["ok"] = "ok"
    message: StrictStr = "State reset complete"


# ---------------------------------------------------------------------------
# Error Model
# ---------------------------------------------------------------------------

class ErrorResponse(BaseModel):
    error: StrictStr
    detail: Any
    request_id: Optional[StrictStr] = None


# ---------------------------------------------------------------------------
# Normalized Domain Context Model
# ---------------------------------------------------------------------------

class ResolvedContext(BaseModel):
    """
    Normalized, validated context representation combining trigger, merchant,
    category, customer, version tracking, and relationship metadata.
    """
    model_config = {"extra": "allow"}

    trigger: Dict[str, Any] = Field(default_factory=dict, description="Normalized trigger payload & metadata")
    merchant: Optional[Dict[str, Any]] = Field(default=None, description="Resolved merchant entity")
    category: Optional[Dict[str, Any]] = Field(default=None, description="Resolved category entity")
    customer: Optional[Dict[str, Any]] = Field(default=None, description="Resolved customer entity if present")
    versions: Dict[str, Optional[int]] = Field(default_factory=dict, description="Version numbers by scope")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Relationship & resolution metadata")


