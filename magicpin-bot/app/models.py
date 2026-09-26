from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Healthz & Metadata Models
# ---------------------------------------------------------------------------

class ContextsLoaded(BaseModel):
    category: int = 0
    merchant: int = 0
    customer: int = 0
    trigger: int = 0


class HealthzResponse(BaseModel):
    status: str = "ok"
    uptime_seconds: int
    contexts_loaded: ContextsLoaded = Field(default_factory=ContextsLoaded)


class MetadataResponse(BaseModel):
    team_name: str
    team_members: List[str]
    model: str
    approach: str
    contact_email: str
    version: str = "1.0.0"


# ---------------------------------------------------------------------------
# Context Models
# ---------------------------------------------------------------------------

class ContextScope(str, Enum):
    CATEGORY = "category"
    MERCHANT = "merchant"
    CUSTOMER = "customer"
    TRIGGER = "trigger"


class ContextRequest(BaseModel):
    model_config = {"extra": "allow"}

    scope: Optional[Any] = Field(default=None, description="Target domain: category, merchant, customer, or trigger")
    context_id: Optional[Any] = Field(default=None, description="Unique identifier for the context entity")
    version: Optional[Any] = Field(default=None, description="Monotonically increasing version number")
    payload: Optional[Any] = Field(default=None, description="Full context object payload")
    delivered_at: Optional[str] = Field(default=None, description="ISO-8601 delivery timestamp")


class ContextResponse(BaseModel):
    accepted: bool = True
    ack_id: str
    stored_at: str


class StaleVersionResponse(BaseModel):
    error: str = "stale_version"
    accepted: bool = False
    reason: str = "stale_version"
    current_version: int
    incoming_version: int
    detail: str


# ---------------------------------------------------------------------------
# Tick Models
# ---------------------------------------------------------------------------

class TickRequest(BaseModel):
    now: Optional[str] = Field(default=None, description="Current simulated ISO-8601 timestamp")
    available_triggers: List[str] = Field(default_factory=list, description="Currently active trigger identifiers")


class TickAction(BaseModel):
    conversation_id: str = Field(..., description="Unique conversation identifier")
    merchant_id: str = Field(..., description="Target merchant identifier")
    customer_id: Optional[str] = Field(default=None, description="Target customer identifier if applicable")
    send_as: Literal["vera", "system"] = Field(default="vera", description="Sender identity")
    trigger_id: Optional[str] = Field(default=None, description="Trigger context ID initiating this action")
    template_name: Optional[str] = Field(default=None, description="Message template name")
    template_params: List[str] = Field(default_factory=list, description="Parameters passed into template")
    body: str = Field(..., description="Rendered message text")
    cta: Optional[str] = Field(default=None, description="Call to action classification")
    suppression_key: Optional[str] = Field(default=None, description="Deduplication / frequency cap key")
    rationale: Optional[str] = Field(default=None, description="Explanation for sending this action")


class TickResponse(BaseModel):
    actions: List[TickAction] = Field(default_factory=list, description="List of proactive actions to initiate")


# ---------------------------------------------------------------------------
# Reply Models
# ---------------------------------------------------------------------------

class ReplyRequest(BaseModel):
    conversation_id: str = Field(..., min_length=1, description="Conversation identifier being replied to")
    message: str = Field(..., min_length=1, description="Raw message received from counterpart")
    merchant_id: Optional[str] = Field(default=None, description="Associated merchant ID")
    customer_id: Optional[str] = Field(default=None, description="Associated customer ID")
    from_role: Optional[Literal["merchant", "customer", "user"]] = Field(default="merchant", description="Role of sender")
    received_at: Optional[str] = Field(default=None, description="ISO-8601 timestamp received")
    turn_number: Optional[int] = Field(default=None, ge=1, description="Turn number in the conversation")


class ReplyResponse(BaseModel):
    action: Literal["wait", "send", "end"] = Field(default="wait", description="Next turn action")
    wait_seconds: Optional[int] = Field(default=None, description="Delay duration when action is 'wait'")
    rationale: Optional[str] = Field(default="Stub response: wait", description="Reasoning behind action")
    body: Optional[str] = Field(default=None, description="Message content when action is 'send'")
    cta: Optional[str] = Field(default=None, description="CTA format when action is 'send'")


# ---------------------------------------------------------------------------
# Teardown Models
# ---------------------------------------------------------------------------

class TeardownResponse(BaseModel):
    status: str = "ok"
    message: str = "State reset complete"


# ---------------------------------------------------------------------------
# Error Model
# ---------------------------------------------------------------------------

class ErrorResponse(BaseModel):
    error: str
    detail: Any
    request_id: Optional[str] = None
