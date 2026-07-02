"""Confidence & Decision Scoring Layer — converts quant indicators into agent decisions."""

from __future__ import annotations

import logging
from typing import Any

from .models import (
    AgentDecision,
    EntryDecision,
    KeyLevels,
    MultiTimeframeConfluence,
    OrderFlowMetrics,
)

logger = logging.getLogger(__name__)


class ConfidenceScorer:
    """Score short-entry and limit-order decisions from quant context."""

    def __init__(
        self,
        delta_std_threshold: float = 2.0,
        imbalance_threshold: float = 0.30,
        short_reject_confidence_threshold: float = 0.30,
        limit_order_min_confidence: float = 0.60,
    ) -> None:
        self.delta_std_threshold = delta_std_threshold
        self.imbalance_threshold = imbalance_threshold
        self.short_reject_threshold = short_reject_confidence_threshold
        self.limit_min_confidence = limit_order_min_confidence

    def evaluate(
        self,
        mtf: MultiTimeframeConfluence,
        flow: OrderFlowMetrics,
        levels: KeyLevels,
        current_price: float,
    ) -> AgentDecision:
        """Full evaluation returning all agent decisions."""
        decision = AgentDecision()
        decision.short_entry = self._evaluate_short_entry(mtf, flow, levels, current_price)
        decision.long_entry = self._evaluate_long_entry(mtf, flow, levels, current_price)
        decision.limit_order = self._evaluate_limit_order(mtf, flow, levels, current_price)
        decision.phase_guidance = self._lifecycle_guidance(mtf, flow, decision)
        return decision

    # ── Short Entry Evaluation ────────────────────────────────────────────────

    def _evaluate_short_entry(
        self,
        mtf: MultiTimeframeConfluence,
        flow: OrderFlowMetrics,
        levels: KeyLevels,
        price: float,
    ) -> EntryDecision:
        verdict = "WAIT"
        confidence = 0.0
        reasons: list[str] = []
        suggested = ""
        rr: float | None = None

        # REJECT conditions (strong bullish signals)
        if flow.delta_regime == "bullish" and abs(flow.delta_z_score) > self.delta_std_threshold:
            confidence = 0.10
            reasons.append(f"Cumulative delta strongly bullish (z={flow.delta_z_score:.2f})")
            verdict = "REJECT"

        if flow.last_event and "sweep_reject_bullish" in flow.last_event:
            confidence = min(confidence, 0.15)
            reasons.append(f"Recent bullish sweep & reject at {flow.last_event_price}")
            verdict = "REJECT"

        if flow.book_imbalance > self.imbalance_threshold:
            confidence = min(confidence, 0.20)
            reasons.append(f"Order-book imbalance bullish ({flow.book_imbalance:.2f})")
            verdict = "REJECT"

        if mtf.is_bullish and not mtf.is_bearish:
            confidence = min(confidence, 0.20)
            reasons.append(f"MTF confluence is bullish ({mtf.overall_direction})")
            verdict = "REJECT"

        # Bullish defense at key support
        nearest_support = levels.nearest_support(price)
        if nearest_support and abs(price - nearest_support.price) < 2.0:
            if flow.delta_regime == "bullish":
                confidence = min(confidence, 0.15)
                reasons.append(f"Price at support {nearest_support.price} with bullish delta")
                verdict = "REJECT"

        # ALLOW conditions (need bearish confluence)
        if verdict != "REJECT":
            bearish_factors = 0
            if mtf.is_bearish:
                bearish_factors += 1
                confidence += 0.25
                reasons.append(f"MTF bearish ({mtf.overall_direction})")
            if flow.delta_regime == "bearish":
                bearish_factors += 1
                confidence += 0.20
                reasons.append(f"Order flow bearish (z={flow.delta_z_score:.2f})")
            if flow.book_imbalance < -0.15:
                bearish_factors += 1
                confidence += 0.15
                reasons.append(f"Book imbalance bearish ({flow.book_imbalance:.2f})")
            if price > (flow.poc or price):
                bearish_factors += 1
                confidence += 0.10
                reasons.append(f"Price above POC ({flow.poc})")

            if bearish_factors >= 3:
                verdict = "ENTER"
                confidence = min(0.90, confidence)
                # Estimate R:R to next support
                target = levels.nearest_support(price)
                if target:
                    sl = levels.nearest_resistance(price)
                    sl_price = sl.price if sl else price + 15.0
                    rr = abs(price - target.price) / max(0.1, abs(price - sl_price))
                    suggested = f"Short at market, SL above {sl_price}, TP {target.price}"
            elif bearish_factors >= 2:
                verdict = "ENTER"
                confidence = min(0.70, confidence)
                suggested = "Short with reduced size — partial confluence only"
            else:
                verdict = "WAIT"
                confidence = min(0.40, confidence)
                suggested = "Insufficient bearish confluence — wait for alignment"

        return EntryDecision(
            verdict=verdict,
            confidence=round(min(1.0, max(0.0, confidence)), 2),
            reasons=reasons,
            suggested_action=suggested,
            risk_reward_estimate=rr,
        )

    # ── Long Entry Evaluation ─────────────────────────────────────────────────

    def _evaluate_long_entry(
        self,
        mtf: MultiTimeframeConfluence,
        flow: OrderFlowMetrics,
        levels: KeyLevels,
        price: float,
    ) -> EntryDecision:
        verdict = "WAIT"
        confidence = 0.0
        reasons: list[str] = []
        suggested = ""
        rr: float | None = None

        # REJECT conditions (strong bearish signals)
        if flow.delta_regime == "bearish" and abs(flow.delta_z_score) > self.delta_std_threshold:
            confidence = 0.10
            reasons.append(f"Cumulative delta strongly bearish (z={flow.delta_z_score:.2f})")
            verdict = "REJECT"

        if flow.last_event and "sweep_reject_bearish" in flow.last_event:
            confidence = min(confidence, 0.15)
            reasons.append(f"Recent bearish sweep & reject at {flow.last_event_price}")
            verdict = "REJECT"

        if mtf.is_bearish and not mtf.is_bullish:
            confidence = min(confidence, 0.20)
            reasons.append(f"MTF confluence is bearish ({mtf.overall_direction})")
            verdict = "REJECT"

        # ALLOW conditions
        if verdict != "REJECT":
            bullish_factors = 0
            if mtf.is_bullish:
                bullish_factors += 1
                confidence += 0.25
                reasons.append(f"MTF bullish ({mtf.overall_direction})")
            if flow.delta_regime == "bullish":
                bullish_factors += 1
                confidence += 0.20
                reasons.append(f"Order flow bullish (z={flow.delta_z_score:.2f})")
            if flow.book_imbalance > 0.15:
                bullish_factors += 1
                confidence += 0.15
                reasons.append(f"Book imbalance bullish ({flow.book_imbalance:.2f})")
            if price < (flow.poc or price):
                bullish_factors += 1
                confidence += 0.10
                reasons.append(f"Price below POC ({flow.poc})")

            if bullish_factors >= 3:
                verdict = "ENTER"
                confidence = min(0.90, confidence)
                target = levels.nearest_resistance(price)
                if target:
                    sl = levels.nearest_support(price)
                    sl_price = sl.price if sl else price - 15.0
                    rr = abs(price - target.price) / max(0.1, abs(price - sl_price))
                    suggested = f"Long at market, SL below {sl_price}, TP {target.price}"
            elif bullish_factors >= 2:
                verdict = "ENTER"
                confidence = min(0.70, confidence)
                suggested = "Long with reduced size — partial confluence only"
            else:
                verdict = "WAIT"
                confidence = min(0.40, confidence)
                suggested = "Insufficient bullish confluence — wait for alignment"

        return EntryDecision(
            verdict=verdict,
            confidence=round(min(1.0, max(0.0, confidence)), 2),
            reasons=reasons,
            suggested_action=suggested,
            risk_reward_estimate=rr,
        )

    # ── Limit Order Evaluation ────────────────────────────────────────────────

    def _evaluate_limit_order(
        self,
        mtf: MultiTimeframeConfluence,
        flow: OrderFlowMetrics,
        levels: KeyLevels,
        price: float,
    ) -> EntryDecision:
        verdict = "WAIT"
        confidence = 0.0
        reasons: list[str] = []
        suggested = ""
        nearest_level: dict[str, Any] | None = None

        # Score proximity to key levels
        best_level = None
        best_score = 0.0

        # Check FVGs
        for fvg in levels.fvgs:
            mid = (fvg.top + fvg.bottom) / 2
            dist = abs(price - mid)
            if dist < 5.0:
                level_score = 0.25 * fvg.strength
                if fvg.fvg_type == "bullish" and mtf.overall_direction in ("BULLISH", "STRONGLY_BULLISH"):
                    level_score += 0.15
                if fvg.fvg_type == "bearish" and mtf.overall_direction in ("BEARISH", "STRONGLY_BEARISH"):
                    level_score += 0.15
                if level_score > best_score:
                    best_score = level_score
                    best_level = {"price": mid, "type": f"FVG_{fvg.fvg_type}", "distance_pips": round(dist * 100, 1)}

        # Check order blocks
        for ob in levels.order_blocks:
            mid = (ob.high + ob.low) / 2
            dist = abs(price - mid)
            if dist < 5.0:
                level_score = 0.20 * ob.strength
                if ob.ob_type == "bullish" and mtf.is_bullish:
                    level_score += 0.15
                if ob.ob_type == "bearish" and mtf.is_bearish:
                    level_score += 0.15
                if level_score > best_score:
                    best_score = level_score
                    best_level = {"price": mid, "type": f"OB_{ob.ob_type}", "distance_pips": round(dist * 100, 1)}

        # Check POC / VAH / VAL
        for level_type, level_price in [("POC", flow.poc), ("VAH", flow.vah), ("VAL", flow.val)]:
            if level_price is None:
                continue
            dist = abs(price - level_price)
            if dist < 5.0:
                level_score = 0.20
                if level_type == "POC":
                    level_score = 0.25
                if level_type in ("VAH",) and mtf.is_bearish:
                    level_score += 0.10
                if level_type in ("VAL",) and mtf.is_bullish:
                    level_score += 0.10
                if level_score > best_score:
                    best_score = level_score
                    best_level = {"price": level_price, "type": level_type, "distance_pips": round(dist * 100, 1)}

        # Check S/R
        for sr in levels.support + levels.resistance:
            dist = abs(price - sr.price)
            if dist < 3.0:
                level_score = 0.15 * sr.strength
                if sr.level_type == "support" and mtf.is_bullish:
                    level_score += 0.10
                if sr.level_type == "resistance" and mtf.is_bearish:
                    level_score += 0.10
                if level_score > best_score:
                    best_score = level_score
                    best_level = {"price": sr.price, "type": sr.level_type, "distance_pips": round(dist * 100, 1)}

        # Decision based on level score + trend alignment
        if best_level:
            confidence = best_score
            nearest_level = best_level
            if mtf.is_bullish and best_level["type"] in ("support", "VAL", "FVG_bullish", "OB_bullish"):
                confidence += 0.20
                reasons.append(f"Bullish level confluence at {best_level['price']} ({best_level['type']})")
                verdict = "ENTER"
            elif mtf.is_bearish and best_level["type"] in ("resistance", "VAH", "FVG_bearish", "OB_bearish"):
                confidence += 0.20
                reasons.append(f"Bearish level confluence at {best_level['price']} ({best_level['type']})")
                verdict = "ENTER"
            else:
                reasons.append(f"Level at {best_level['price']} but trend alignment weak")
                verdict = "WAIT"

            if confidence >= self.limit_min_confidence:
                suggested = f"Place limit at {best_level['price']} ({best_level['type']})"
            else:
                verdict = "WAIT"
                suggested = f"Level found at {best_level['price']} but confidence too low ({confidence:.2f})"
        else:
            reasons.append("No key level within proximity of current price")
            verdict = "WAIT"
            suggested = "Wait for price to reach a structural level"

        return EntryDecision(
            verdict=verdict,
            confidence=round(min(1.0, max(0.0, confidence)), 2),
            reasons=reasons,
            suggested_action=suggested,
            risk_reward_estimate=None,
        )

    # ── Lifecycle Phase Guidance ──────────────────────────────────────────────

    def _lifecycle_guidance(
        self,
        mtf: MultiTimeframeConfluence,
        flow: OrderFlowMetrics,
        decision: AgentDecision,
    ) -> dict[str, str]:
        """Provide phase-specific guidance for the trading lifecycle."""
        guidance: dict[str, str] = {}

        # Pre-entry
        if decision.short_entry.verdict == "REJECT":
            guidance["pre_entry"] = "AVOID shorts — structural bullish signals detected"
        elif decision.short_entry.verdict == "ENTER":
            guidance["pre_entry"] = f"Short setup valid — confidence {decision.short_entry.confidence}"
        else:
            guidance["pre_entry"] = "WAIT — insufficient confluence for entry"

        # Entry timing
        if flow.last_event and "sweep_reject" in flow.last_event:
            guidance["entry_timing"] = f"Flow event detected: {flow.last_event} — consider immediate entry"
        elif abs(flow.delta_z_score) > 1.5:
            guidance["entry_timing"] = f"Delta extreme (z={flow.delta_z_score:.2f}) — possible reversal zone"
        else:
            guidance["entry_timing"] = "No extreme flow signal — standard entry acceptable"

        # In-trade management
        if mtf.is_strongly_bearish:
            guidance["in_trade"] = "Strong bearish confluence — hold position, trail SL"
        elif mtf.is_strongly_bullish:
            guidance["in_trade"] = "Strong bullish confluence — if short, consider early exit"
        else:
            guidance["in_trade"] = "Mixed confluence — manage risk tightly"

        # Exit
        if flow.last_event and "exhaustion" in flow.last_event:
            guidance["exit"] = "Exhaustion detected — consider partial close or full exit"
        else:
            guidance["exit"] = "No exhaustion signal — follow standard TP/SL plan"

        return guidance
