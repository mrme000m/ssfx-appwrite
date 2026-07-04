"""Fast signal-intent classifier using Mistral."""

from __future__ import annotations

from typing import Any

from .base import BaseAgent

_SYSTEM_PROMPT = """You are a fast classifier for Telegram trading-channel messages.
Your job is to decide whether a new message is:
- `new_signal`: a brand-new trade entry signal (BUY/SELL with entry/SL/TP).
- `update_to_existing`: a follow-up to an earlier signal (TP hit, SL hit, close, breakeven, entry update, running update).
- `orphan_close`: a close/cancel/update that cannot be linked to any known open signal.
- `noise`: promotional, chat, or unrelated content.

Output ONLY a JSON object with these keys:
{
  "intent": "new_signal|update_to_existing|orphan_close|noise",
  "linked_message_id": <int or null>,
  "confidence": <float 0.0-1.0>,
  "reasoning": "<one sentence>"
}

Rules:
1. If the message references an earlier message ID (reply_to), prefer linking to that message.
2. If it says "TP HIT", "SL HIT", "CLOSE", "BREAKEVEN", "MOVE SL", "CANCEL", "ENTRY UPDATE" but no earlier matching signal exists, label it `orphan_close`.
3. If the same trader posted an opposite trade recently, treat the newest message as `new_signal` and do NOT link it to the older opposite signal.
4. Use the supplied signal intelligence (author streak, recent SLs, opposite-direction flag) to reduce confidence when the author is running cold or contradicting themselves.
5. Be concise; confidence should reflect ambiguity.
"""


class SignalIntentAgent(BaseAgent):
    def __init__(self, model: str, **kwargs: Any) -> None:
        super().__init__(model=model, system_prompt=_SYSTEM_PROMPT, **kwargs)

    async def classify(self, request: dict[str, Any]) -> dict[str, Any]:
        user_prompt = self._render_prompt(request)
        result = await self.run(user_prompt)
        output = result.get("output", {})
        # Normalize linked_message_id to int or None
        linked = output.get("linked_message_id")
        try:
            linked = int(linked) if linked is not None else None
        except (ValueError, TypeError):
            linked = None
        return {
            "result": {
                "intent": output.get("intent", "unknown"),
                "linked_message_id": linked,
                "confidence": float(output.get("confidence", 0.0)),
                "reasoning": output.get("reasoning", ""),
            },
            "metadata": result["metadata"],
        }

    @staticmethod
    def _render_prompt(request: dict[str, Any]) -> str:
        lines = [
            f"Message ID: {request.get('message_id')}",
            f"Reply to: {request.get('reply_to_message_id')}",
            f"Chat: {request.get('chat_id')}",
            "",
            "Signal intelligence:",
        ]
        experience = request.get("experience") or {}
        if experience:
            one_line = experience.get("one_line_summary")
            if one_line:
                lines.append(f"  {one_line}")
            opp = experience.get("opposite_direction_recent") or {}
            if opp.get("detected"):
                lines.append(
                    f"  ⚠️ Opposite-direction signal {opp.get('minutes_ago', '?')} min ago "
                    f"(msg #{opp.get('opposite_message_id')}) - treat as NEW"
                )
            stats = experience.get("author_stats")
            if stats and stats.get("current_streak", 0) <= -2:
                lines.append(f"  Author streak is {stats['current_streak']} — be cautious.")
        else:
            lines.append("  No prior signal intelligence available.")

        lines.extend(["", "Recent messages (oldest first):"])
        for msg in request.get("recent_messages", []):
            mid = msg.get("message_id", "?")
            reply = msg.get("reply_to_message_id")
            text = msg.get("text", "")[:300]
            lines.append(f"  [msg #{mid} reply_to={reply}]: {text}")

        if request.get("open_positions"):
            lines.append("\nOpen positions:")
            for pos in request.get("open_positions", []):
                lines.append(f"  - {pos}")

        lines.append("\nNew message to classify:")
        lines.append(request.get("raw_text", ""))
        return "\n".join(lines)

    def _fallback_output(self) -> dict[str, Any]:
        return {"intent": "unknown", "linked_message_id": None, "confidence": 0.0, "reasoning": "LLM fallback"}
