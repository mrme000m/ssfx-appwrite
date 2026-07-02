"""FastAPI API for the agent harness."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .agents import EntryDecisionAgent, LifecyclePlannerAgent, PplxResearchAgent, SignalIntentAgent
from .config import get_settings
from .models import (
    EntryDecisionRequest,
    EntryDecisionResponse,
    HealthResponse,
    LifecyclePlanRequest,
    LifecyclePlanResponse,
    PplxResearchRequest,
    PplxResearchResponse,
    SignalIntentRequest,
    SignalIntentResponse,
)
from .providers import LlmProvider
from .tools import AccountHubClient, DataServiceClient
from .tools.pplx_agent import PplxAgentClient

logger = logging.getLogger(__name__)

# Runtime state (populated in lifespan)
state: dict[str, Any] = {
    "intent_agent": None,
    "entry_agent": None,
    "lifecycle_agent": None,
    "pplx_research_agent": None,
    "provider": None,
    "data_client": None,
    "hub_client": None,
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    provider = LlmProvider(settings)
    data_client = DataServiceClient(settings)
    hub_client = AccountHubClient(settings)

    state["provider"] = provider
    state["data_client"] = data_client
    state["hub_client"] = hub_client

    state["intent_agent"] = SignalIntentAgent(
        model=settings.agent_model_mistral,
        settings=settings,
        provider=provider,
        timeout_seconds=settings.agent_mistral_timeout_ms / 1000.0,
        max_tokens=512,
        temperature=0.1,
    )
    state["entry_agent"] = EntryDecisionAgent(
        model=settings.agent_model_hermes,
        settings=settings,
        provider=provider,
        timeout_seconds=settings.agent_hermes_timeout_ms / 1000.0,
        max_tokens=1024,
        temperature=0.2,
    )
    state["lifecycle_agent"] = LifecyclePlannerAgent(
        model=settings.agent_model_kimi,
        settings=settings,
        provider=provider,
        timeout_seconds=settings.agent_kimi_timeout_ms / 1000.0,
        max_tokens=1024,
        temperature=0.2,
    )
    state["pplx_research_agent"] = PplxResearchAgent(settings=settings)

    logger.info("Agent harness started")
    yield
    await provider.close()
    await data_client.close()
    await hub_client.close()
    pplx_research: PplxResearchAgent | None = state.get("pplx_research_agent")
    if pplx_research is not None:
        await pplx_research.close()
    logger.info("Agent harness stopped")


app = FastAPI(title="Agent Harness", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        status="ok",
        entry_enabled=settings.agent_entry_enabled,
        lifecycle_enabled=settings.agent_lifecycle_enabled,
        autonomy_enabled=settings.agent_autonomy_enabled,
        pplx_agent_enabled=settings.pplx_agent_enabled,
        pplx_agent_url=settings.pplx_agent_url,
        models={
            "mistral": settings.agent_model_mistral,
            "hermes": settings.agent_model_hermes,
            "kimi": settings.agent_model_kimi,
        },
    )


@app.post("/agent/v1/signal/intent", response_model=SignalIntentResponse)
async def signal_intent(req: SignalIntentRequest) -> SignalIntentResponse:
    agent: SignalIntentAgent | None = state.get("intent_agent")
    if agent is None:
        raise HTTPException(status_code=503, detail="Intent agent not initialized")

    payload = {
        "raw_text": req.raw_text,
        "message_id": req.message_id,
        "chat_id": req.chat_id,
        "reply_to_message_id": req.reply_to_message_id,
        "recent_messages": req.recent_messages,
        "open_positions": req.open_positions,
    }
    result = await agent.classify(payload)
    return SignalIntentResponse(**result)


@app.post("/agent/v1/entry/decision", response_model=EntryDecisionResponse)
async def entry_decision(req: EntryDecisionRequest) -> EntryDecisionResponse:
    settings = get_settings()
    if not settings.agent_entry_enabled:
        raise HTTPException(status_code=403, detail="Agent entry decisions are disabled")

    agent: EntryDecisionAgent | None = state.get("entry_agent")
    if agent is None:
        raise HTTPException(status_code=503, detail="Entry agent not initialized")

    quant = req.quant_snapshot
    if quant is None:
        client: DataServiceClient | None = state.get("data_client")
        if client is not None:
            quant = await client.get_gold_quant_snapshot()

    payload = {
        "signal": req.signal,
        "quant_snapshot": quant,
        "experience": req.experience,
        "open_positions": req.open_positions,
    }
    result = await agent.decide(payload)
    decision = result.get("decision", {})

    # Enforce confidence thresholds server-side
    action = decision.get("action", "WAIT")
    confidence = float(decision.get("confidence", 0.0))
    order_type = decision.get("order_type")
    if action == "ENTER" and confidence < settings.agent_min_entry_confidence:
        decision["action"] = "WAIT"
        decision["reasons"] = decision.get("reasons", []) + [
            f"confidence {confidence:.2f} below min_entry {settings.agent_min_entry_confidence:.2f}"
        ]
    elif action == "MODIFY" and order_type == "LIMIT" and confidence < settings.agent_min_limit_confidence:
        decision["action"] = "WAIT"
        decision["reasons"] = decision.get("reasons", []) + [
            f"limit confidence {confidence:.2f} below min_limit {settings.agent_min_limit_confidence:.2f}"
        ]

    return EntryDecisionResponse(**result)


@app.post("/agent/v1/lifecycle/plan", response_model=LifecyclePlanResponse)
async def lifecycle_plan(req: LifecyclePlanRequest) -> LifecyclePlanResponse:
    settings = get_settings()
    if not settings.agent_lifecycle_enabled:
        raise HTTPException(status_code=403, detail="Agent lifecycle planning is disabled")

    agent: LifecyclePlannerAgent | None = state.get("lifecycle_agent")
    if agent is None:
        raise HTTPException(status_code=503, detail="Lifecycle agent not initialized")

    quant = req.quant_snapshot
    if quant is None:
        client: DataServiceClient | None = state.get("data_client")
        if client is not None:
            quant = await client.get_gold_quant_snapshot()

    payload = {
        "position": req.position,
        "signal_update": req.signal_update,
        "quant_snapshot": quant,
        "recent_messages": req.recent_messages,
    }
    result = await agent.plan(payload)
    return LifecyclePlanResponse(**result)


@app.post("/agent/v1/research/pplx", response_model=PplxResearchResponse)
async def pplx_research(req: PplxResearchRequest) -> PplxResearchResponse:
    settings = get_settings()
    if not settings.pplx_agent_enabled:
        raise HTTPException(status_code=403, detail="PPLX Agent research is disabled")

    agent: PplxResearchAgent | None = state.get("pplx_research_agent")
    if agent is None:
        raise HTTPException(status_code=503, detail="PPLX research agent not initialized")

    result = await agent.run(question=req.question if req.include_custom else "")
    return PplxResearchResponse(**result)


@app.get("/agent/v1/research/pplx/health")
async def pplx_research_health() -> dict[str, Any]:
    """Check connectivity to the PPLX Agent service."""
    settings = get_settings()
    client = PplxAgentClient(settings)
    try:
        health = await client.health()
        if health is None:
            raise HTTPException(status_code=503, detail="PPLX Agent unreachable")
        return {"pplx_agent_enabled": settings.pplx_agent_enabled, "health": health}
    finally:
        await client.close()
