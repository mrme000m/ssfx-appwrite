"""FastAPI server exposing the PPLX Agent pipeline and knowledge base."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .config import get_settings
from .gold_market_agent import GoldMarketAgent
from .logging import setup_logging

logger = setup_logging()


# ---------------------------------------------------------------------------
# Pydantic request/response models
# ---------------------------------------------------------------------------

class QueryRequest(BaseModel):
    query: str
    mode: str = "pro"


class UpdateResponse(BaseModel):
    success: bool
    symbol: str
    report_path: str | None = None
    space_uuid: str | None = None
    error: str | None = None


class QueryResponse(BaseModel):
    answer: str | None = None
    backend_uuid: str | None = None
    citations_count: int = 0


class HealthResponse(BaseModel):
    status: str
    symbol: str
    space_uuid: str | None = None


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logger.info("PPLX Agent API starting on %s:%s", settings.api_host, settings.api_port)
    yield
    logger.info("PPLX Agent API stopped")


app = FastAPI(title="PPLX Agent", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        status="ok",
        symbol=settings.analysis_symbol,
        space_uuid=settings.gold_market_space_uuid or None,
    )


@app.post("/api/v1/gold/update", response_model=UpdateResponse)
async def trigger_update() -> UpdateResponse:
    """Run the daily gold market update synchronously."""
    agent = GoldMarketAgent()
    try:
        result = await agent.run_daily_update()
        return UpdateResponse(
            success=True,
            symbol=result["symbol"],
            report_path=result.get("report_path"),
            space_uuid=result.get("space_uuid"),
        )
    except Exception as exc:
        logger.exception("Daily update failed")
        return UpdateResponse(
            success=False,
            symbol=get_settings().analysis_symbol,
            error=str(exc),
        )


@app.post("/api/v1/gold/query", response_model=QueryResponse)
async def query_knowledge_base(request: QueryRequest) -> QueryResponse:
    """Ask a question against the gold market knowledge base."""
    agent = GoldMarketAgent()
    try:
        result = await agent.query_space(request.query, mode=request.mode)
        return QueryResponse(
            answer=result.get("answer"),
            backend_uuid=result.get("backend_uuid"),
            citations_count=len(result.get("citations", [])),
        )
    except Exception as exc:
        logger.exception("Space query failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/v1/gold/rate-limits")
async def rate_limits() -> dict[str, Any]:
    agent = GoldMarketAgent()
    return agent.get_rate_limits()
