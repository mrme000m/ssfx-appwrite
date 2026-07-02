"""Generate the daily gold market markdown report from all research inputs."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .config import PplxAgentSettings


class ReportGenerator:
    """Synthesize Perplexity research, TradingView TA, and quant snapshot."""

    def __init__(self, settings: PplxAgentSettings) -> None:
        self._settings = settings

    def generate(
        self,
        *,
        symbol: str,
        price: dict[str, Any] | None,
        tradingview_summary: dict[str, Any],
        quant_snapshot: dict[str, Any] | None,
        macro: dict[str, Any],
        technical: dict[str, Any],
        fundamental: dict[str, Any],
        long_term: dict[str, Any],
    ) -> str:
        """Return a markdown report string."""
        date = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
        lines = [
            f"# Gold Market Intelligence Report — {symbol}",
            f"**Generated:** {date}  ",
            f"**Model:** Perplexity {self._settings.pplx_model} ({self._settings.pplx_mode})",
            "",
            "## Executive Summary",
            long_term.get("answer") or "_Long-term synthesis unavailable._",
            "",
            "## Current Market Snapshot",
        ]

        if price:
            lines.append(
                f"- **Price:** Bid {price.get('bid')} / Ask {price.get('ask')} "
                f"(spread {price.get('spread', 'n/a')})"
            )
            lines.append(f"- **Change:** {price.get('bid_change_pct', 'n/a')}%")
        else:
            lines.append("- Price data unavailable from data service.")

        if quant_snapshot:
            decision = quant_snapshot.get("decision", {})
            lines.append(
                f"- **Quant Short Entry:** {decision.get('short_entry', {}).get('verdict')} "
                f"({decision.get('short_entry', {}).get('confidence')} confidence)"
            )
            lines.append(
                f"- **Quant Limit Order:** {decision.get('limit_order', {}).get('verdict')} "
                f"({decision.get('limit_order', {}).get('confidence')} confidence)"
            )

        lines.extend(["", "## Multi-Timeframe Technicals (TradingView)", ""])
        tf_summary = tradingview_summary.get("timeframes", {})
        for tf in self._settings.timeframes_list:
            data = tf_summary.get(tf, {})
            lines.append(
                f"- **{tf}:** {data.get('bias', 'n/a')} "
                f"(all={data.get('all', 0):.2f}, ma={data.get('ma', 0):.2f})"
            )

        lines.extend(["", "## Macro & Geopolitical", ""])
        lines.extend(_split_answer(macro.get("answer")))

        lines.extend(["", "## Technical Analysis", ""])
        lines.extend(_split_answer(technical.get("answer")))

        lines.extend(["", "## Fundamental Drivers", ""])
        lines.extend(_split_answer(fundamental.get("answer")))

        lines.extend(["", "## Long-Term Picture", ""])
        lines.extend(_split_answer(long_term.get("answer")))

        lines.extend(["", "## Sources"])
        for section, result in [
            ("Macro", macro),
            ("Technical", technical),
            ("Fundamental", fundamental),
            ("Long-term", long_term),
        ]:
            citations = result.get("citations", [])
            if citations:
                lines.append(f"### {section}")
                for i, c in enumerate(citations, 1):
                    title = c.get("title") or "Source"
                    url = c.get("url") or ""
                    lines.append(f"{i}. [{title}]({url})")

        return "\n".join(lines) + "\n"


def _split_answer(text: Any) -> list[str]:
    if not text:
        return ["_No content returned._"]
    out = []
    for line in str(text).splitlines():
        stripped = line.strip()
        if stripped:
            out.append(line)
    return out if out else ["_No content returned._"]
