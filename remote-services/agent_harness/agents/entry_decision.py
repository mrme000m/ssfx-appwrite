"""Primary trading decision agent using Hermes 3."""

from __future__ import annotations

import json
from typing import Any

from .base import BaseAgent

_SYSTEM_PROMPT = """You are a disciplined XAUUSD (gold) trading decision agent.
You receive a parsed trading signal, a quantitative market snapshot (multi-timeframe, order flow, key levels), signal experience statistics, and any open positions.

Your job is to return a JSON decision object with these keys:
{
  "action": "ENTER|REJECT|WAIT|MODIFY",
  "confidence": <float 0.0-1.0>,
  "order_type": "MARKET|LIMIT|STOP|null",
  "limit_price": <float or null>,
  "size_multiplier": <float, default 1.0>,
  "sl": <float, "BREAKEVEN", or null>,
  "tp1": <float or null>,
  "tp2": <float or null>,
  "tp3": <float or null>,
  "reasons": ["<string>", ...],
  "suggested_action": "<one sentence>"
}

Decision rules:
1. If the quant snapshot's `short_entry.verdict` is REJECT and the signal is SELL, you should usually REJECT unless the signal has exceptional experience context.
2. If the quant snapshot shows strong confluence in the signal direction and the limit_order confidence is high, prefer LIMIT at the suggested key level.
3. If order flow is mixed or the signal contradicts the higher-timeframe trend, WAIT or MODIFY (e.g., reduce size, wait for a level).
4. Never increase size above the original; use size_multiplier <= 1.0 for caution, or 1.0 for normal execution.
5. For MODIFY, only override SL/TP/limit_price when the quant context gives a clear reason.
6. Output valid JSON only, no markdown.
"""


class EntryDecisionAgent(BaseAgent):
    def __init__(self, model: str, **kwargs: Any) -> None:
        super().__init__(model=model, system_prompt=_SYSTEM_PROMPT, **kwargs)

    async def decide(self, request: dict[str, Any]) -> dict[str, Any]:
        user_prompt = self._render_prompt(request)
        result = await self.run(user_prompt)
        output = result.get("output", {})
        return {
            "decision": {
                "action": output.get("action", "WAIT"),
                "confidence": float(output.get("confidence", 0.0)),
                "order_type": output.get("order_type"),
                "limit_price": output.get("limit_price"),
                "size_multiplier": float(output.get("size_multiplier", 1.0)),
                "sl": output.get("sl"),
                "tp1": output.get("tp1"),
                "tp2": output.get("tp2"),
                "tp3": output.get("tp3"),
                "reasons": output.get("reasons", []),
                "suggested_action": output.get("suggested_action", ""),
            },
            "metadata": result["metadata"],
        }

    @staticmethod
    def _render_prompt(request: dict[str, Any]) -> str:
        signal = request.get("signal", {})
        quant = request.get("quant_snapshot", {})
        experience = request.get("experience", {})
        open_positions = request.get("open_positions", [])

        lines = [
            "## Parsed Signal",
            f"{signal.get('signal_type')} {signal.get('direction')} {signal.get('symbol')}",
            f"Entry: {signal.get('entry_price')}  OrderType: {signal.get('order_type')}",
            f"SL: {signal.get('sl')}  TP1: {signal.get('tp1')}  TP2: {signal.get('tp2')}  TP3: {signal.get('tp3')}",
            f"Parser confidence: {signal.get('parse_confidence')}  Quality score: {signal.get('quality_score')}  Experience action: {signal.get('experience_action')}",
            f"Raw text: {signal.get('raw_text', '')[:300]}",
            "",
            "## Quantitative Market Snapshot",
            f"{quant.get('agent_prompt', json.dumps(quant))}",
            "",
            "## Signal Experience",
            f"{experience}",
            "",
            "## Open Positions",
        ]
        for pos in open_positions:
            lines.append(f"- {pos}")
        if not open_positions:
            lines.append("None")
        return "\n".join(lines)

    def _fallback_output(self) -> dict[str, Any]:
        return {
            "action": "WAIT",
            "confidence": 0.0,
            "order_type": None,
            "limit_price": None,
            "size_multiplier": 1.0,
            "sl": None,
            "tp1": None,
            "tp2": None,
            "tp3": None,
            "reasons": ["LLM fallback"],
            "suggested_action": "Use deterministic rules",
        }
