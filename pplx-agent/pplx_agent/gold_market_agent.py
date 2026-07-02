"""High-level orchestrator for the daily gold market intelligence pipeline."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from .config import PplxAgentSettings, get_settings
from .logging import setup_logging
from .pplx_client import PplxClient, PplxClientError
from .report_generator import ReportGenerator
from .space_manager import SpaceManager
from .tradingview import TradingViewError, get_timeframe_summary

logger = setup_logging()


class GoldMarketAgent:
    """Run the full daily Perplexity + TradingView + DataService pipeline."""

    def __init__(self, settings: PplxAgentSettings | None = None) -> None:
        self._settings = settings or get_settings()
        self._pplx = PplxClient(self._settings)
        self._space = SpaceManager(self._settings)
        self._reporter = ReportGenerator(self._settings)

    # ------------------------------------------------------------------
    # Public pipeline
    # ------------------------------------------------------------------

    async def run_daily_update(self) -> dict[str, Any]:
        """Execute the full daily update end-to-end."""
        symbol = self._settings.analysis_symbol
        logger.info("Starting daily gold market update for %s", symbol)

        # 1. Gather numeric context concurrently.
        price, quant, tv_summary = await self._gather_context(symbol)

        # 2. Perplexity research concurrently.
        macro, technical, fundamental, long_term = await self._run_research(
            symbol, tv_summary, quant
        )

        # 3. Generate report.
        report = self._reporter.generate(
            symbol=symbol,
            price=price,
            tradingview_summary=tv_summary,
            quant_snapshot=quant,
            macro=macro,
            technical=technical,
            fundamental=fundamental,
            long_term=long_term,
        )

        saved_path = self._write_report(report)

        # 4. Space sync.
        space_uuid = None
        if self._settings.enable_space_sync:
            try:
                space_uuid = self._space.ensure_space()
                self._space.upload_report(space_uuid, report)
                self._space.update_long_term_thread(space_uuid, long_term.get("answer", ""))
                self._space.delete_old_reports(space_uuid, self._settings.report_retention_days)
            except PplxClientError as exc:
                logger.warning("Space sync failed: %s", exc)

        logger.info("Daily update complete. Report: %s", saved_path)
        return {
            "symbol": symbol,
            "report_path": str(saved_path),
            "space_uuid": space_uuid,
            "macro": _pruned(macro),
            "technical": _pruned(technical),
            "fundamental": _pruned(fundamental),
            "long_term": _pruned(long_term),
        }

    async def query_space(self, question: str, mode: str = "pro") -> dict[str, Any]:
        """Ask a question against the stored gold market knowledge base."""
        space_uuid = self._space.ensure_space()
        return self._space.query(space_uuid, question, mode=mode)

    def get_rate_limits(self) -> dict[str, Any]:
        return self._pplx.get_rate_limits()

    # ------------------------------------------------------------------
    # Context gathering
    # ------------------------------------------------------------------

    async def _gather_context(self, symbol: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any]]:
        """Fetch price/quant from data service and TradingView summary concurrently."""
        tasks = [
            asyncio.create_task(self._fetch_price(symbol)),
            asyncio.create_task(self._fetch_quant_snapshot()),
            asyncio.create_task(self._fetch_tv_summary(symbol)),
        ]
        price, quant, tv_summary = await asyncio.gather(*tasks, return_exceptions=True)

        if isinstance(price, Exception):
            logger.warning("Price fetch failed: %s", price)
            price = None
        if isinstance(quant, Exception):
            logger.warning("Quant snapshot fetch failed: %s", quant)
            quant = None
        if isinstance(tv_summary, Exception):
            logger.warning("TradingView fetch failed: %s", tv_summary)
            tv_summary = {"symbol": symbol, "timeframes": {}}

        return price, quant, tv_summary  # type: ignore[return-value]

    async def _fetch_price(self, symbol: str) -> dict[str, Any] | None:
        url = f"{self._settings.data_service_base_url.rstrip('/')}/api/v1/data/{symbol}"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=self._auth_headers())
            resp.raise_for_status()
            return resp.json()

    async def _fetch_quant_snapshot(self) -> dict[str, Any] | None:
        url = f"{self._settings.data_service_base_url.rstrip('/')}/api/v1/gold/quant"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=self._auth_headers())
            resp.raise_for_status()
            return resp.json()

    async def _fetch_tv_summary(self, symbol: str) -> dict[str, Any]:
        def _sync() -> dict[str, Any]:
            return get_timeframe_summary(symbol)

        try:
            return await asyncio.to_thread(_sync)
        except TradingViewError:
            raise

    # ------------------------------------------------------------------
    # Research orchestration
    # ------------------------------------------------------------------

    async def _run_research(
        self,
        symbol: str,
        tv_summary: dict[str, Any],
        quant: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
        """Run macro/technical/fundamental queries and synthesize the long-term picture."""
        loop = asyncio.get_event_loop()

        tasks: list[asyncio.Future[dict[str, Any]]] = []

        if self._settings.enable_macro:
            tasks.append(loop.run_in_executor(None, self._pplx.research_macro, symbol))
        else:
            tasks.append(loop.create_future())
            tasks[-1].set_result({"answer": "_Macro analysis disabled._"})

        if self._settings.enable_technical:
            key_levels = quant.get("key_levels", {}) if quant else None
            quant_context = ""
            if quant and quant.get("agent_prompt"):
                quant_context = quant["agent_prompt"]
            tasks.append(
                loop.run_in_executor(
                    None,
                    self._pplx.research_technical,
                    symbol,
                    tv_summary,
                    key_levels,
                    quant_context,
                )
            )
        else:
            tasks.append(loop.create_future())
            tasks[-1].set_result({"answer": "_Technical analysis disabled._"})

        if self._settings.enable_fundamental:
            tasks.append(loop.run_in_executor(None, self._pplx.research_fundamentals, symbol))
        else:
            tasks.append(loop.create_future())
            tasks[-1].set_result({"answer": "_Fundamental analysis disabled._"})

        macro, technical, fundamental = await asyncio.gather(*tasks)

        long_term = await loop.run_in_executor(
            None,
            self._pplx.synthesize_long_term_picture,
            symbol,
            macro.get("answer", ""),
            technical.get("answer", ""),
            fundamental.get("answer", ""),
        )

        return macro, technical, fundamental, long_term

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def _auth_headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self._settings.data_service_api_key:
            headers["Authorization"] = f"Bearer {self._settings.data_service_api_key}"
        return headers

    def _write_report(self, report: str) -> Path:
        self._settings.report_output_dir.mkdir(parents=True, exist_ok=True)
        date = datetime.now(UTC).strftime("%Y-%m-%d")
        path = self._settings.report_output_dir / f"gold_market_report_{date}.md"
        path.write_text(report, encoding="utf-8")
        return path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pruned(result: dict[str, Any]) -> dict[str, Any]:
    """Return a lightweight view of a Perplexity result for API responses."""
    return {
        "answer": result.get("answer"),
        "backend_uuid": result.get("backend_uuid"),
        "citations_count": len(result.get("citations", [])),
    }
