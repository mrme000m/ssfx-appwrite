"""Agent Context Builder — formats gold quant data into agent-consumable output."""

from __future__ import annotations

import logging
from typing import Any

from .models import (
    AgentDecision,
    GoldQuantSnapshot,
    KeyLevels,
    MultiTimeframeConfluence,
    OrderFlowMetrics,
)

logger = logging.getLogger(__name__)


class AgentContextBuilder:
    """Builds JSON snapshots and LLM-ready prompt snippets."""

    def build_snapshot(
        self,
        symbol: str,
        timestamp_ms: int,
        bid: float,
        ask: float,
        mtf: MultiTimeframeConfluence,
        flow: OrderFlowMetrics,
        levels: KeyLevels,
        decision: AgentDecision,
    ) -> GoldQuantSnapshot:
        """Assemble the complete snapshot."""
        prompt = self._build_prompt(symbol, bid, ask, mtf, flow, levels, decision)

        return GoldQuantSnapshot(
            symbol=symbol,
            timestamp_ms=timestamp_ms,
            bid=bid,
            ask=ask,
            spread=round(ask - bid, 2),
            mtf=mtf,
            order_flow=flow,
            key_levels=levels,
            decision=decision,
            agent_prompt=prompt,
        )

    def build_compact_dict(self, snapshot: GoldQuantSnapshot) -> dict[str, Any]:
        """Compact dict suitable for REST API response."""
        return {
            "symbol": snapshot.symbol,
            "timestamp_ms": snapshot.timestamp_ms,
            "price": {"bid": snapshot.bid, "ask": snapshot.ask, "spread": snapshot.spread},
            "multi_timeframe": self._mtf_to_dict(snapshot.mtf),
            "order_flow": self._flow_to_dict(snapshot.order_flow),
            "key_levels": self._levels_to_dict(snapshot.key_levels),
            "decision": self._decision_to_dict(snapshot.decision),
        }

    def build_prompt_for_llm(self, snapshot: GoldQuantSnapshot) -> str:
        """Return the pre-rendered agent prompt."""
        return snapshot.agent_prompt

    def build_llm_prompt(self, snapshot: GoldQuantSnapshot) -> str:
        """Alias for build_prompt_for_llm (used by MCP/REST naming)."""
        return self.build_prompt_for_llm(snapshot)

    def build_decision_dict(self, snapshot: GoldQuantSnapshot) -> dict[str, Any]:
        """Return only the decision matrix from the snapshot."""
        return {
            "symbol": snapshot.symbol,
            "timestamp_ms": snapshot.timestamp_ms,
            "price": {"bid": snapshot.bid, "ask": snapshot.ask, "spread": snapshot.spread},
            "decision": self._decision_to_dict(snapshot.decision),
        }

    # ── Prompt builder ────────────────────────────────────────────────────────

    def _build_prompt(
        self,
        symbol: str,
        bid: float,
        ask: float,
        mtf: MultiTimeframeConfluence,
        flow: OrderFlowMetrics,
        levels: KeyLevels,
        decision: AgentDecision,
    ) -> str:
        lines: list[str] = []
        lines.append(f"# Gold Quantitative Analysis — {symbol}")
        lines.append(f"**Price:** Bid {bid:.2f} | Ask {ask:.2f} | Spread {ask-bid:.2f}")
        lines.append("")

        # MTF
        lines.append("## Multi-Timeframe Confluence")
        lines.append(f"**Overall:** {mtf.overall_direction} (confidence {mtf.confidence:.0%})")
        for r in mtf.readings:
            ind_summary = ", ".join(
                f"{i['name']}={i.get('value', i.get('signal', ''))}" for i in r.indicators[:3]
            )
            lines.append(f"- **{r.timeframe}:** {r.direction} (score {r.score:+.2f}) {ind_summary}")
        lines.append("")

        # Order Flow
        lines.append("## Tick Volume & Order Flow")
        lines.append(f"- Delta regime: {flow.delta_regime} (z={flow.delta_z_score:.2f})")
        lines.append(f"- Cumulative delta: {flow.cumulative_delta:+.1f}")
        if flow.poc:
            lines.append(f"- POC: {flow.poc:.2f}")
        if flow.vah and flow.val:
            lines.append(f"- Value Area: {flow.val:.2f} – {flow.vah:.2f}")
        lines.append(f"- Book imbalance: {flow.book_imbalance:+.2f}")
        if flow.last_event != "none":
            lines.append(f"- Last flow event: {flow.last_event} @ {flow.last_event_price}")
        lines.append("")

        # Key Levels
        lines.append("## Structural Levels")
        sr = [f"{s.price:.2f}(S)" for s in levels.support[:3]]
        rs = [f"{r.price:.2f}(R)" for r in levels.resistance[:3]]
        lines.append(f"- Support: {', '.join(sr) if sr else 'none detected'}")
        lines.append(f"- Resistance: {', '.join(rs) if rs else 'none detected'}")
        if levels.fvgs:
            fvg_txt = ", ".join(f"{f.bottom:.2f}-{f.top:.2f}({f.fvg_type[:1]})" for f in levels.fvgs[:2])
            lines.append(f"- FVGs: {fvg_txt}")
        if levels.order_blocks:
            ob_txt = ", ".join(f"{ob.low:.2f}-{ob.high:.2f}({ob.ob_type[:1]})" for ob in levels.order_blocks[:2])
            lines.append(f"- Order Blocks: {ob_txt}")
        lines.append("")

        # Decisions
        lines.append("## Agent Decisions")
        se = decision.short_entry
        lines.append(f"- **Short Entry:** {se.verdict} (confidence {se.confidence:.0%})")
        if se.reasons:
            lines.append(f"  - Reasons: {'; '.join(se.reasons)}")
        if se.suggested_action:
            lines.append(f"  - Action: {se.suggested_action}")

        le = decision.long_entry
        lines.append(f"- **Long Entry:** {le.verdict} (confidence {le.confidence:.0%})")
        if le.suggested_action:
            lines.append(f"  - Action: {le.suggested_action}")

        lo = decision.limit_order
        lines.append(f"- **Limit Order:** {lo.verdict} (confidence {lo.confidence:.0%})")
        if lo.suggested_action:
            lines.append(f"  - Action: {lo.suggested_action}")
        lines.append("")

        # Phase guidance
        if decision.phase_guidance:
            lines.append("## Lifecycle Guidance")
            for phase, guidance in decision.phase_guidance.items():
                lines.append(f"- **{phase}:** {guidance}")

        return "\n".join(lines)

    # ── Dict helpers ──────────────────────────────────────────────────────────

    def _mtf_to_dict(self, mtf: MultiTimeframeConfluence) -> dict[str, Any]:
        return {
            "overall_direction": mtf.overall_direction,
            "confidence": mtf.confidence,
            "bull_count": mtf.bull_count,
            "bear_count": mtf.bear_count,
            "neutral_count": mtf.neutral_count,
            "readings": [
                {
                    "timeframe": r.timeframe,
                    "direction": r.direction,
                    "score": r.score,
                    "regime": r.regime,
                    "indicators": r.indicators,
                }
                for r in mtf.readings
            ],
            "factors": mtf.factors,
            "reasons": mtf.reasons,
        }

    def _flow_to_dict(self, flow: OrderFlowMetrics) -> dict[str, Any]:
        return {
            "tick_delta": flow.tick_delta,
            "cumulative_delta": flow.cumulative_delta,
            "delta_regime": flow.delta_regime,
            "delta_z_score": flow.delta_z_score,
            "poc": flow.poc,
            "vah": flow.vah,
            "val": flow.val,
            "book_imbalance": flow.book_imbalance,
            "bid_depth": flow.bid_depth,
            "ask_depth": flow.ask_depth,
            "last_event": flow.last_event,
            "last_event_price": flow.last_event_price,
            "last_event_ms": flow.last_event_ms,
        }

    def _levels_to_dict(self, levels: KeyLevels) -> dict[str, Any]:
        return {
            "support": [{"price": s.price, "strength": s.strength} for s in levels.support],
            "resistance": [{"price": r.price, "strength": r.strength} for r in levels.resistance],
            "fvgs": [{"top": f.top, "bottom": f.bottom, "type": f.fvg_type, "strength": f.strength} for f in levels.fvgs],
            "order_blocks": [{"high": ob.high, "low": ob.low, "type": ob.ob_type, "strength": ob.strength} for ob in levels.order_blocks],
            "swing_highs": [{"price": sh.price} for sh in levels.swing_highs[-3:]],
            "swing_lows": [{"price": sl.price} for sl in levels.swing_lows[-3:]],
            "fib_levels": [{"price": fl.price, "metadata": fl.metadata} for fl in levels.fib_levels],
        }

    def _decision_to_dict(self, decision: AgentDecision) -> dict[str, Any]:
        def _entry_to_dict(e: Any) -> dict[str, Any]:
            return {
                "verdict": e.verdict,
                "confidence": e.confidence,
                "reasons": e.reasons,
                "suggested_action": e.suggested_action,
                "risk_reward_estimate": e.risk_reward_estimate,
            }

        return {
            "short_entry": _entry_to_dict(decision.short_entry),
            "long_entry": _entry_to_dict(decision.long_entry),
            "limit_order": _entry_to_dict(decision.limit_order),
            "phase_guidance": decision.phase_guidance,
        }
