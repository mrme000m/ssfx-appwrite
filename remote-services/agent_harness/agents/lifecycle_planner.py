"""Long-context lifecycle planner using Kimi K2.7."""

from __future__ import annotations

import json
from typing import Any

from .base import BaseAgent

_SYSTEM_PROMPT = """You are a trade-management strategist.
You are given an open position, the current quantitative market snapshot, an optional signal update (TP hit, SL to entry, close, etc.), and recent channel messages for context.

Return ONLY a JSON object:
{
  "action": "HOLD|PARTIAL_CLOSE|MOVE_BREAKEVEN|FULL_CLOSE|CANCEL",
  "close_percentage": <float or null>,
  "new_sl": <float or null>,
  "new_tp": <float or null>,
  "reasoning": "<one sentence>"
}

Guidelines:
- HOLD when the trend and order flow still support the position and the quant snapshot does not show exhaustion.
- PARTIAL_CLOSE when a TP is hit or the quant snapshot shows partial exhaustion; set close_percentage (e.g., 50 for TP1).
- MOVE_BREAKEVEN when the position is in profit and the quant snapshot supports locking in gains.
- FULL_CLOSE when the quant snapshot strongly contradicts the position direction, the signal explicitly closes, or the recent messages suggest the trade thesis is invalidated.
- CANCEL for pending orders that no longer make sense.
- Use recent_messages to detect contradictions (e.g., author reversed direction or reported a stop loss on a correlated signal).
"""


class LifecyclePlannerAgent(BaseAgent):
    def __init__(self, model: str, **kwargs: Any) -> None:
        super().__init__(model=model, system_prompt=_SYSTEM_PROMPT, **kwargs)

    async def plan(self, request: dict[str, Any]) -> dict[str, Any]:
        user_prompt = self._render_prompt(request)
        result = await self.run(user_prompt)
        output = result.get("output", {})
        return {
            "plan": {
                "action": output.get("action", "HOLD"),
                "close_percentage": output.get("close_percentage"),
                "new_sl": output.get("new_sl"),
                "new_tp": output.get("new_tp"),
                "reasoning": output.get("reasoning", ""),
            },
            "metadata": result["metadata"],
        }

    @staticmethod
    def _render_prompt(request: dict[str, Any]) -> str:
        position = request.get("position", {})
        quant = request.get("quant_snapshot", {})
        update = request.get("signal_update", {})
        recent = request.get("recent_messages", [])

        lines = [
            "## Open Position",
            f"{json.dumps(position)}",
            "",
            "## Quantitative Market Snapshot",
            f"{quant.get('agent_prompt', json.dumps(quant))}",
            "",
        ]
        if update:
            lines.append("## Signal Update")
            lines.append(json.dumps(update))
            lines.append("")
        if recent:
            lines.append("## Recent Channel Messages")
            for msg in recent[-10:]:
                lines.append(f"- {msg}")
            lines.append("")
        lines.append("What is the optimal lifecycle action?")
        return "\n".join(lines)

    def _fallback_output(self) -> dict[str, Any]:
        return {
            "action": "HOLD",
            "close_percentage": None,
            "new_sl": None,
            "new_tp": None,
            "reasoning": "LLM fallback",
        }
