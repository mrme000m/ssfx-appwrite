"""Build agent-ready context snapshots from dataservice responses."""
from __future__ import annotations

from typing import Any


class ContextEngine:
    """Aggregates price, indicators, signals, and quality into a prompt snippet."""

    def build_prompt(self, symbol: str, price: dict[str, Any], context: dict[str, Any], quality: dict[str, Any]) -> str:
        lines = [
            f"Market context for {symbol.upper()}:",
            f"  Bid: {self._fmt(price.get('bid'))} Ask: {self._fmt(price.get('ask'))} Spread: {self._fmt(price.get('spread'))}",
        ]

        indicators = context.get("indicators", {})
        if indicators:
            lines.append("  Indicators:")
            for k, v in list(indicators.items())[:8]:
                val = v if not isinstance(v, dict) else f"{v}"
                lines.append(f"    {k}: {val}")

        trend = context.get("trend")
        if trend:
            lines.append(f"  Trend: {trend} (strength {self._fmt(context.get('trend_strength'))})")

        q_score = quality.get("score") or quality.get("quality_score")
        if q_score is not None:
            lines.append(f"  Quality: {float(q_score) * 100:.0f}% tradeable={quality.get('tradeable')}")

        reasons = quality.get("reasons") or quality.get("blockers") or []
        if reasons:
            lines.append("  Blockers:")
            for r in reasons:
                lines.append(f"    - {r}")

        return "\n".join(lines)

    def build_system_message(self, symbol: str, snapshot: dict[str, Any]) -> dict[str, str]:
        return {
            "role": "system",
            "content": self.build_prompt(
                symbol,
                snapshot.get("price", {}),
                snapshot.get("context", {}),
                snapshot.get("quality", {}),
            ),
        }

    @staticmethod
    def _fmt(value: Any) -> str:
        if value is None:
            return "—"
        try:
            return f"{float(value):.5f}"
        except (TypeError, ValueError):
            return str(value)
