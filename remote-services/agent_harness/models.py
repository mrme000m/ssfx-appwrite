"""Pydantic models for the agent harness API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# ── Shared agent output models ───────────────────────────────────────────────


class AgentMetadata(BaseModel):
    """Provenance for an agent decision."""

    model: str = ""
    latency_ms: float = 0.0
    tokens_used: int | None = None
    fallback: bool = False
    error: str | None = None


# ── Signal intent ────────────────────────────────────────────────────────────


class SignalIntentRequest(BaseModel):
    raw_text: str
    message_id: int
    chat_id: str
    reply_to_message_id: int | None = None
    recent_messages: list[dict[str, Any]] = Field(default_factory=list)
    open_positions: list[dict[str, Any]] = Field(default_factory=list)


class SignalIntentResult(BaseModel):
    intent: str = "unknown"  # new_signal, update_to_existing, orphan_close, noise
    linked_message_id: int | None = None
    confidence: float = 0.0
    reasoning: str = ""


class SignalIntentResponse(BaseModel):
    result: SignalIntentResult
    metadata: AgentMetadata


# ── Entry decision ───────────────────────────────────────────────────────────


class EntryDecisionRequest(BaseModel):
    signal: dict[str, Any]
    quant_snapshot: dict[str, Any] | None = None
    experience: dict[str, Any] | None = None
    open_positions: list[dict[str, Any]] = Field(default_factory=list)
    account: dict[str, Any] | None = None


class EntryDecision(BaseModel):
    action: str = "WAIT"  # ENTER, REJECT, WAIT, MODIFY
    confidence: float = 0.0
    order_type: str | None = None  # MARKET, LIMIT, STOP
    limit_price: float | None = None
    size_multiplier: float = 1.0
    sl: float | str | None = None
    tp1: float | None = None
    tp2: float | None = None
    tp3: float | None = None
    reasons: list[str] = Field(default_factory=list)
    suggested_action: str = ""


class EntryDecisionResponse(BaseModel):
    decision: EntryDecision
    metadata: AgentMetadata


# ── Lifecycle plan ───────────────────────────────────────────────────────────


class LifecyclePlanRequest(BaseModel):
    position: dict[str, Any]
    signal_update: dict[str, Any] | None = None
    quant_snapshot: dict[str, Any] | None = None
    recent_messages: list[dict[str, Any]] = Field(default_factory=list)


class LifecyclePlan(BaseModel):
    action: str = "HOLD"  # HOLD, PARTIAL_CLOSE, MOVE_BREAKEVEN, FULL_CLOSE, CANCEL
    close_percentage: float | None = None
    new_sl: float | None = None
    new_tp: float | None = None
    reasoning: str = ""


class LifecyclePlanResponse(BaseModel):
    plan: LifecyclePlan
    metadata: AgentMetadata


# ── Health ───────────────────────────────────────────────────────────────────


class HealthResponse(BaseModel):
    status: str = "ok"
    entry_enabled: bool = False
    lifecycle_enabled: bool = False
    autonomy_enabled: bool = False
    models: dict[str, str] = Field(default_factory=dict)
