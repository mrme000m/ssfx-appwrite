"""Thin wrapper around the vendored Perplexity web client.

Handles robust answer extraction and gold-market-specific query helpers.
"""

from __future__ import annotations

import json
from typing import Any

from pplx import PerplexityClient

from .config import PplxAgentSettings, get_settings


class PplxClientError(RuntimeError):
    """Wrapper for Perplexity client failures."""


class PplxClient:
    """PPLX Agent client with answer extraction and research presets."""

    def __init__(self, settings: PplxAgentSettings | None = None) -> None:
        self._settings = settings or get_settings()
        try:
            self._client = PerplexityClient()
        except Exception as exc:
            raise PplxClientError(f"Failed to initialize Perplexity client: {exc}") from exc

    # ------------------------------------------------------------------
    # Low-level search
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        mode: str | None = None,
        model: str | None = None,
        thinking: bool | None = None,
        sources: list[str] | None = None,
        follow_up: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run a Perplexity search and return a normalized result dict."""
        mode = mode or self._settings.pplx_mode
        model = model or self._settings.pplx_model
        if thinking is None:
            thinking = self._settings.pplx_thinking

        try:
            raw = self._client.search(
                query=query,
                mode=mode.replace("_", " "),
                model=model,
                thinking=thinking,
                sources=sources or ["web"],
                follow_up=follow_up,
            )
        except Exception as exc:
            raise PplxClientError(f"Perplexity search failed: {exc}") from exc

        return _normalize_result(raw)

    def search_space(
        self,
        space_uuid: str,
        query: str,
        *,
        mode: str | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        """Search inside a Perplexity Space and return a normalized result dict."""
        mode = mode or self._settings.pplx_mode
        model = model or self._settings.pplx_model

        try:
            raw = self._client.search_in_space(
                uuid=space_uuid,
                query=query,
                mode=mode.replace("_", " "),
                model=model,
            )
        except Exception as exc:
            raise PplxClientError(f"Perplexity space search failed: {exc}") from exc

        return _normalize_result(raw)

    def create_thread_in_space(
        self,
        space_uuid: str,
        query: str,
        *,
        mode: str | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        """Start a new thread inside a Space (used to seed/update the long-term picture)."""
        return self.search_space(space_uuid, query, mode=mode, model=model)

    # ------------------------------------------------------------------
    # Research presets
    # ------------------------------------------------------------------

    def research_macro(self, symbol: str = "XAUUSD") -> dict[str, Any]:
        """Deep-research macro and geopolitical drivers for gold."""
        query = (
            f"Provide a comprehensive macro analysis for {symbol} (gold vs USD). "
            "Cover: Federal Reserve policy and expectations, real yields, US dollar strength, "
            "inflation expectations, central bank gold buying, geopolitical risk, "
            "Tuesday/Thursday expected market-moving events. "
            "Return structured bullet points with data, levels, and cited sources."
        )
        return self.search(query, mode="deep_research", thinking=True)

    def research_technical(
        self,
        symbol: str,
        timeframe_summary: dict[str, Any],
        key_levels: dict[str, Any] | None = None,
        quant_context: str = "",
    ) -> dict[str, Any]:
        """Pro-mode technical analysis given TradingView + quant context."""
        levels_text = ""
        if key_levels:
            levels_text = f"\nKey levels from quantitative engine: {json.dumps(key_levels, indent=2)}"
        query = (
            f"You are a senior gold technical analyst. Current multi-timeframe TradingView summary "
            f"for {symbol}: {json.dumps(timeframe_summary, indent=2)}.{levels_text}"
            f"\n{quant_context}\n"
            "Identify the prevailing trend, nearest support/resistance, high-probability setups, "
            "and any divergences. Keep the answer concise, actionable, and cite any recent news."
        )
        return self.search(query, mode="pro", thinking=True)

    def research_fundamentals(self, symbol: str = "XAUUSD") -> dict[str, Any]:
        """Pro-mode fundamental snapshot focused on DXY, yields, ETF flows, and flows."""
        query = (
            f"Fundamental snapshot for {symbol}: current DXY level and trend, 10Y US Treasury yield, "
            "10Y TIPS real yield, GLD/IAU ETF flows, CFTC non-commercial net positioning if available, "
            "and any notable options market activity. Provide levels and a bias."
        )
        return self.search(query, mode="pro", thinking=True)

    def synthesize_long_term_picture(
        self,
        symbol: str,
        macro: str,
        technical: str,
        fundamental: str,
        prior_summary: str = "",
    ) -> dict[str, Any]:
        """Ask a reasoning model to merge all inputs into a single long-term picture."""
        prior_section = ""
        if prior_summary:
            prior_section = (
                "\n\nHere is the previous long-term summary; update it rather than contradicting it "
                f"unless new evidence justifies a shift:\n{prior_summary}"
            )
        query = (
            f"You are the head of cross-asset research. Synthesize the following into one coherent "
            f"long-term {symbol} market picture (macro + structural technical + fundamental). "
            f"State your bias (bullish/bearish/neutral), expected range for the next 1-4 weeks, "
            f"key levels, and the main catalysts that would invalidate your view."
            f"\n\n### MACRO\n{macro}\n\n### TECHNICAL\n{technical}\n\n### FUNDAMENTAL\n{fundamental}"
            f"{prior_section}"
        )
        return self.search(query, mode="reasoning", thinking=True)

    # ------------------------------------------------------------------
    # Pass-through for space management
    # ------------------------------------------------------------------

    @property
    def raw(self) -> PerplexityClient:
        """Expose the underlying client for space/file operations."""
        return self._client

    def get_rate_limits(self) -> dict[str, Any]:
        return self._client.get_rate_limit_status()


# ---------------------------------------------------------------------------
# Answer extraction
# ---------------------------------------------------------------------------


def _normalize_result(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Extract a clean answer + backend_uuid + citations from a Perplexity event."""
    if not raw or not isinstance(raw, dict):
        return {"answer": None, "backend_uuid": None, "citations": [], "raw": raw}

    text_obj = raw.get("text", "")
    if isinstance(text_obj, str):
        try:
            text_obj = json.loads(text_obj)
        except json.JSONDecodeError:
            pass

    answer = None
    citations: list[dict[str, Any]] = []

    if isinstance(text_obj, list):
        for step in text_obj:
            if not isinstance(step, dict):
                continue
            step_type = step.get("step_type", "")
            content = step.get("content", {})
            if step_type == "FINAL" and isinstance(content, dict):
                answer_json = content.get("answer", "")
                if isinstance(answer_json, str):
                    try:
                        parsed = json.loads(answer_json)
                        if isinstance(parsed, dict):
                            answer = parsed.get("answer")
                            citations = parsed.get("citations", [])
                    except json.JSONDecodeError:
                        answer = answer_json
                elif isinstance(answer_json, dict):
                    answer = answer_json.get("answer")
                    citations = answer_json.get("citations", [])
            elif step_type == "SEARCH_RESULTS" and isinstance(content, dict) and not answer:
                results = content.get("web_results", [])
                snippets = [r.get("snippet", "") for r in results if r.get("snippet")]
                if snippets:
                    answer = "\n\n".join(snippets)
                citations = [
                    {"title": r.get("title"), "url": r.get("url")}
                    for r in results
                    if r.get("url")
                ]
    elif isinstance(text_obj, dict):
        answer = text_obj.get("answer")
        citations = text_obj.get("citations", [])
    else:
        answer = str(text_obj) if text_obj else None

    return {
        "answer": answer,
        "backend_uuid": raw.get("backend_uuid"),
        "citations": citations,
        "raw": raw,
    }
