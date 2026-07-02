# Gold Quantitative Market Analysis — Design Document

**Version:** 1.0  
**Date:** 2026-07-02  
**Status:** Design Complete → Implementation Ready  
**Scope:** Quantitative XAUUSD (gold) market indicators for agent-driven trading decisions across the full trade lifecycle.

---

## 1. Executive Summary

This document designs a **Gold Quantitative Analysis Engine** that augments the existing slwp trading infrastructure with multi-timeframe (MTF) technical indicators, real-time tick-volume/order-flow analytics, key-level detection, and a confidence-scoring layer purpose-built for **limit-order entry validation** and **short-entry rejection**.

The engine plugs into the existing `market_data_service` (InfluxDB-backed) and feeds structured, agent-readable context into the trading lifecycle at every phase: **pre-entry validation → entry timing → in-trade management → exit confirmation**.

---

## 2. Problem Statement

### Current Gaps

| Gap | Impact |
|-----|--------|
| No dedicated gold quant module | XAUUSD trades using generic forex assumptions; gold-specific volatility and session behaviour are ignored. |
| No real-time tick-volume analysis | Cannot detect buyer/seller aggression, absorption, or exhaustion at the tick level. |
| No multi-timeframe confluence engine | Agents trade on single-TF signals; missing high-probability setups where 3+ TFs align. |
| No structural key-level detection | Limit orders are placed without quantified proximity to support/resistance, FVGs, or order blocks. |
| No order-flow confidence scoring | Agents cannot reject low-probability short entries when order flow is structurally bullish. |

### Requirements

1. **≥3 Timeframe Proof** — Compute and store indicators on at least three timeframes (e.g., M15, H1, H4) with confluence scoring.
2. **Tick Volume & Order Flow** — Real-time delta, volume profile, imbalance, sweep detection from tick and depth data.
3. **Short Entry Filter** — Quantitative criteria to pick (high confidence) or reject (low confidence / structural bullish) short market entries.
4. **Key Levels & Macro Trends** — Automated S/R, fair-value gaps, order blocks, and trend regime for limit-order confidence.
5. **Agent-Ready Output** — Structured JSON/prompt snippets consumable by decision agents at every lifecycle step.

---

## 3. High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                    MARKET DATA SERVICE (port 9000-9002)              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────┐   │
│  │ Tick Ingest  │  │ Bar Ingest   │  │ Depth Ingest             │   │
│  │ (ctrader)    │  │ (aggregator) │  │ (order book)             │   │
│  └──────┬───────┘  └──────┬───────┘  └───────────┬──────────────┘   │
│         │                  │                      │                  │
│         └──────────────────┼──────────────────────┘                  │
│                            ▼                                         │
│              ┌─────────────────────────────┐                         │
│              │  InfluxDB Cloud Serverless  │                         │
│              │  (ticks, bars, depth)       │                         │
│              └─────────────┬───────────────┘                         │
│                            │                                         │
│                            ▼                                         │
│  ┌─────────────────────────────────────────────────────────────┐     │
│  │           GOLD QUANTITATIVE ANALYSIS ENGINE                 │     │
│  │  ┌─────────────┐ ┌─────────────┐ ┌─────────────────────┐    │     │
│  │  │ Tick Volume │ │ Order Flow  │ │ Key Levels Detector │    │     │
│  │  │  Analyzer   │ │   Engine    │ │   (S/R, FVG, OB)    │    │     │
│  │  └──────┬──────┘ └──────┬──────┘ └──────────┬──────────┘    │     │
│  │         └───────────────┼─────────────────────┘              │     │
│  │                         ▼                                    │     │
│  │           ┌─────────────────────────────┐                    │     │
│  │           │  Multi-Timeframe Confluence │                    │     │
│  │           │       Engine (≥3 TFs)       │                    │     │
│  │           └─────────────┬───────────────┘                    │     │
│  │                         ▼                                    │     │
│  │           ┌─────────────────────────────┐                    │     │
│  │           │   Confidence & Decision     │                    │     │
│  │           │       Scoring Layer         │                    │     │
│  │           └─────────────┬───────────────┘                    │     │
│  │                         ▼                                    │     │
│  │           ┌─────────────────────────────┐                    │     │
│  │           │   Agent Context Builder     │                    │     │
│  │           │   (prompt-ready output)     │                    │     │
│  │           └─────────────────────────────┘                    │     │
│  └─────────────────────────────────────────────────────────────┘     │
│                            │                                         │
│                            ▼                                         │
│              ┌─────────────────────────────┐                         │
│              │   InfluxDB — gold_quant_*   │                         │
│              │   measurements (indicators) │                         │
│              └─────────────────────────────┘                         │
│                            │                                         │
│         ┌──────────────────┼──────────────────┐                      │
│         ▼                  ▼                  ▼                      │
│  ┌────────────┐   ┌──────────────┐   ┌──────────────┐               │
│  │ REST API   │   │  SSE / WS    │   │  Agent Pipeline│              │
│  │ /gold/*    │   │  live stream │   │  (command center)│            │
│  └────────────┘   └──────────────┘   └──────────────┘               │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 4. Component Design

### 4.1 Data Ingestion Layer (Existing — Extended)

**What already exists:**
- `DataIngestionEngine` ticks → bars aggregation
- `OrderBookSnapshot` from cTrader depth events
- InfluxDB measurements: `ticks`, `bars`, `orderbook`

**Extension required:**
- Add `bid_volume` and `ask_volume` tracking per tick (currently minimal)
- Compute tick delta on-ingestion: `delta = ask_volume - bid_volume` per tick
- Store running volume profile in Redis-style ephemeral cache (or in-memory) per symbol

### 4.2 Tick Volume Analyzer

**Purpose:** Derive buyer/seller aggression and accumulation/distribution from tick-level volume.

**Indicators:**

| Indicator | Formula | Usage |
|-----------|---------|-------|
| **Tick Delta** | Σ(ask_volume) − Σ(bid_volume) over window | Net buying vs selling pressure. Positive = aggressive buyers. |
| **Cumulative Delta** | Running sum of tick delta | Trend of order-flow momentum. Divergence with price = reversal signal. |
| **Volume Profile (VP)** | Histogram of volume at each price level over lookback | POC (point of control), VAH (value area high), VAL (value area low). |
| **Delta Profile** | Bull volume − Bear volume per price row | Shows where buyers/sellers are most aggressive. |
| **Delta Divergence** | Price makes new low but delta makes higher low | Bullish exhaustion / reversal signal. |

**Gold-specific tuning:**
- Window: 200 ticks (scalping), 1000 ticks (intraday), 5000 ticks (swing)
- POC refresh: every 50 ticks for scalping, 200 for swing
- Session awareness: London open (08:00 UTC), NY open (13:30 UTC), Tokyo overlap

### 4.3 Order Flow Engine

**Purpose:** Detect structural order-flow events from tick stream + order book depth.

**Event Types:**

| Event | Detection Logic | Trading Implication |
|-------|----------------|---------------------|
| **Sweep & Reject** | Price pierces a recent swing low/high with low delta, then reverses with high delta in opposite direction | Stop-run finished; trade opposite direction. |
| **Absorption** | Large volume at a price level with minimal price progress (high volume, small range) | Institutional accumulation; expect breakout in direction of absorption. |
| **Exhaustion** | SPIKE in delta with minimal price follow-through | Peak aggression; reversal likely. |
| **Imbalance** | Bid depth ≫ Ask depth (or vice versa) sustained > 5 seconds | Directional pressure; short-term bias. |
| **Iceberg Detection** | Repeated fills at same price level, depth replenishes | Large hidden order; level is defended. |

**Short Entry Rejection Logic:**

```
REJECT_SHORT if ANY of:
  - Cumulative Delta > +2σ (strong buying pressure)
  - POC is rising over last N ticks
  - Order book imbalance > +0.30 (bid depth dominates)
  - Recent sweep & reject at swing low (bullish rejection)
  - Price is near VAL with delta turning positive
  - H1/H4 trend is bullish and M15 delta is positive

ALLOW_SHORT if ALL of:
  - Cumulative Delta < -1σ (selling pressure)
  - POC is falling
  - Price is below POC + VAH broken
  - Order book imbalance < -0.20
  - H1/H4 trend is bearish or neutral-bearish
  - No recent bullish sweep & reject within last 50 ticks
```

### 4.4 Key Levels Detector

**Purpose:** Quantify structural support/resistance levels from bar data.

**Level Types:**

| Level Type | Detection Method | Usage for Limit Orders |
|------------|-----------------|------------------------|
| **Swing High/Low** | Local maxima/minima (2-bar lookback) | SL placement, reversal entries. |
| **Fair Value Gap (FVG)** | Three-candle pattern: middle candle's wick doesn't overlap adjacent bodies | Retracement targets; price "should" return to fill inefficiency. |
| **Order Block (OB)** | Last opposite-colour candle before strong impulsive move | Institutional order zone; high-probability reversal. |
| **Volume POC** | Price level with maximum volume in recent profile | Magnet for price; strong S/R. |
| **Previous Day High/Low** | Daily extremes | Macro S/R for swing entries. |
| **Fibonacci Retracements** | 0.382, 0.5, 0.618 from recent swing | Confluence zones for limit orders. |

**Confidence Score for Limit Orders:**

```python
limit_order_confidence = weighted_sum([
    (distance_to_nearest_fvg < threshold, weight=0.25),
    (price_near_poc_or_val, weight=0.20),
    (order_block_in_direction, weight=0.20),
    (fibonacci_confluence, weight=0.15),
    (trend_aligned_with_level, weight=0.20),
])
```

### 4.5 Multi-Timeframe Confluence Engine

**Purpose:** Require ≥3 timeframes to agree before high-confidence entry.

**Timeframes for Gold:**

| Timeframe | Role | Primary Indicators |
|-----------|------|-------------------|
| **M15** | Entry timing | EMA alignment, RSI, delta, volume profile |
| **H1** | Trend confirmation | MACD, ATR, swing structure, trend regime |
| **H4** | Macro direction | Market structure (BOS/CHoCH), higher-timeframe POC |
| **D1** | Context | Daily bias, previous day high/low, macro trend |

*(Minimum 3 of 4 must be evaluated; D1 is optional for intraday.)*

**Confluence Scoring:**

```python
def compute_confluence(m15_score, h1_score, h4_score, d1_score=None):
    """Returns (direction, confidence, factors)"""
    scores = {"m15": m15_score, "h1": h1_score, "h4": h4_score}
    if d1_score is not None:
        scores["d1"] = d1_score

    bull_count = sum(1 for s in scores.values() if s > +0.3)
    bear_count = sum(1 for s in scores.values() if s < -0.3)
    neutral_count = len(scores) - bull_count - bear_count

    if bull_count >= 3:
        direction = "STRONGLY_BULLISH"
        confidence = min(1.0, 0.6 + 0.1 * bull_count)
    elif bull_count >= 2 and bear_count == 0:
        direction = "BULLISH"
        confidence = 0.55
    elif bear_count >= 3:
        direction = "STRONGLY_BEARISH"
        confidence = min(1.0, 0.6 + 0.1 * bear_count)
    elif bear_count >= 2 and bull_count == 0:
        direction = "BEARISH"
        confidence = 0.55
    else:
        direction = "NEUTRAL"
        confidence = 0.3

    return direction, confidence, scores
```

**Indicator Scoring per Timeframe:**

| Signal | Score Range | Condition |
|--------|------------|-----------|
| EMA alignment | +0.2 to +0.3 | EMA10 > EMA20 > EMA50 (bullish) |
| RSI extreme | +0.15 / −0.15 | RSI < 30 (bullish mean-reversion), RSI > 70 (bearish) |
| MACD histogram | +0.1 / −0.1 | Positive/negative crossover |
| Volume Delta | +0.15 / −0.15 | Cumulative delta positive/negative |
| Structure break | +0.25 / −0.25 | BOS/CHoCH in direction |
| Key level proximity | +0.1 to +0.2 | Price at confluence zone aligned with direction |

### 4.6 Confidence & Decision Scoring Layer

**Purpose:** Convert raw quant indicators into actionable agent decisions.

**Decision Matrix for Short Entries:**

| Condition | Decision | Confidence | Reason |
|-----------|----------|------------|--------|
| H4 bearish + H1 bearish + M15 bearish + delta negative + price below POC | **ENTER_SHORT** | 0.85 | Full confluence, order flow aligned |
| H4 bearish + H1 bearish + M15 neutral + delta negative | **ENTER_SHORT** | 0.70 | Majority bearish, flow supports |
| H4 bearish + H1 neutral + M15 bullish + delta positive | **REJECT** | 0.15 | M15 and flow against macro; high risk |
| H4 bullish + H1 bullish + M15 bearish + delta mixed | **REJECT** | 0.20 | Counter-trend short against macro |
| Any TF + recent bullish sweep & reject | **REJECT** | 0.25 | Structural bullish defense detected |
| Delta > +2σ at key support level | **REJECT** | 0.10 | Aggressive buying at support |

**Decision Matrix for Limit Orders:**

| Condition | Confidence | Action |
|-----------|------------|--------|
| Price at FVG + OB + POC confluence + trend aligned | 0.90 | Place limit with tight SL |
| Price at single level (e.g., just S/R) + trend mixed | 0.50 | Place limit with wide SL or wait |
| No key level nearby + trend unclear | 0.20 | Avoid limit; use market order with caution |
| Price near VAH/VAL with delta divergence | 0.75 | Place limit at level edge |

### 4.7 Agent Context Builder

**Purpose:** Format all quant data into agent-consumable structures.

**Output Schema (JSON):**

```json
{
  "symbol": "XAUUSD",
  "timestamp_ms": 1719900000000,
  "price": {
    "bid": 2345.67,
    "ask": 2345.92,
    "mid": 2345.795
  },
  "multi_timeframe": {
    "m15": {"direction": "bullish", "score": 0.45, "indicators": [...]},
    "h1":  {"direction": "bullish", "score": 0.35, "indicators": [...]},
    "h4":  {"direction": "bearish", "score": -0.40, "indicators": [...]},
    "d1":  {"direction": "neutral", "score": 0.05, "indicators": [...]},
    "confluence": {
      "direction": "NEUTRAL",
      "confidence": 0.35,
      "factors": {"m15": 0.45, "h1": 0.35, "h4": -0.40}
    }
  },
  "order_flow": {
    "tick_delta": -45.2,
    "cumulative_delta": -892.1,
    "delta_regime": "bearish",
    "poc": 2348.50,
    "vah": 2352.10,
    "val": 2341.20,
    "book_imbalance": -0.15,
    "last_event": "sweep_reject_bearish"
  },
  "key_levels": {
    "support": [2341.20, 2338.50, 2332.00],
    "resistance": [2348.50, 2352.10, 2358.00],
    "fvgs": [{"top": 2348.20, "bottom": 2347.50, "type": "bullish"}],
    "order_blocks": [{"high": 2350.00, "low": 2348.00, "type": "bearish"}]
  },
  "decision": {
    "short_entry": {
      "verdict": "REJECT",
      "confidence": 0.15,
      "reasons": [
        "M15 bullish with positive delta",
        "Price near VAL with bullish sweep rejection at 2338",
        "H4 bearish but no confluence from lower TFs"
      ]
    },
    "limit_order": {
      "verdict": "WAIT",
      "confidence": 0.40,
      "nearest_level": {"price": 2348.50, "type": "POC", "distance_pips": 27.0},
      "suggested_action": "Wait for price to reach POC with bearish delta confirmation"
    }
  },
  "agent_prompt": "...markdown summary for LLM..."
}
```

---

## 5. InfluxDB Schema Extensions

New measurements to store computed gold-quant indicators:

| Measurement | Fields | Tags | Retention |
|-------------|--------|------|-----------|
| `gold_quant_mtf` | `m15_score`, `h1_score`, `h4_score`, `d1_score`, `confluence_confidence` | `symbol`, `direction` | 30d |
| `gold_quant_orderflow` | `tick_delta`, `cum_delta`, `poc`, `vah`, `val`, `book_imbalance` | `symbol`, `regime` | 30d |
| `gold_quant_levels` | `level_price`, `level_type`, `strength` | `symbol`, `timeframe`, `level_type` | 90d |
| `gold_quant_decisions` | `short_verdict_confidence`, `limit_verdict_confidence` | `symbol`, `short_verdict`, `limit_verdict` | 90d |

---

## 6. API Surface

### REST Endpoints (extend `api_server.py`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/gold/quant` | GET | Full quantitative snapshot (MTF + order flow + levels + decision) |
| `/api/v1/gold/mtf` | GET | Multi-timeframe confluence only |
| `/api/v1/gold/orderflow` | GET | Tick volume & order flow metrics only |
| `/api/v1/gold/levels` | GET | Key structural levels only |
| `/api/v1/gold/decision` | GET | Agent decision matrix (short + limit) |

### WebSocket / SSE Stream

Push real-time `gold_quant_decisions` updates every 5 seconds (or on significant delta/level change) to agent pipeline consumers.

---

## 7. Integration with Existing Services

### 7.1 Signal Experience Scorer

Before a Telegram signal is accepted, call the Gold Quant Engine:

```python
# In signal_experience/scorer.py — extended
async def score_with_market_context(signal: TradeSignal) -> tuple[float, dict]:
    if signal.symbol == "XAUUSD":
        quant = await gold_engine.get_snapshot()
        if signal.direction.value == "SELL":
            if quant["decision"]["short_entry"]["verdict"] == "REJECT":
                return 0.1, {"reason": "gold_quant_rejects_short", "quant": quant}
    return base_score, base_factors
```

### 7.2 Context Engine

Extend `ctrader/market/context_engine.py` to include gold quant context in agent prompts:

```python
def build_prompt(self, symbol: str, ...):
    if symbol == "XAUUSD":
        quant = gold_engine.get_snapshot_sync()
        lines.append(f"  Gold Quant: {quant['decision']['short_entry']['verdict']} "
                     f"(confidence {quant['decision']['short_entry']['confidence']})")
```

### 7.3 Trade Lifecycle Manager

`xauusd_lifecycle.py` queries the quant engine before executing `new_position`:

```python
async def validate_entry(...):
    quant = await gold_engine.get_snapshot()
    if direction == "SELL" and quant["decision"]["short_entry"]["verdict"] == "REJECT":
        return TradeResult(accepted=False, message="Gold quant rejects short entry")
```

---

## 8. Implementation Plan

### Phase 1 — Core Engine (Week 1)
- [ ] Create `gold_quant_engine/` package
- [ ] Implement `TickVolumeAnalyzer` (delta, cumulative delta, profile)
- [ ] Implement `OrderFlowEngine` (sweep, absorption, imbalance)
- [ ] Implement `KeyLevelsDetector` (S/R, FVG, OB)
- [ ] Unit tests with synthetic tick data

### Phase 2 — MTF & Confidence (Week 2)
- [ ] Implement `MultiTimeframeConfluence`
- [ ] Implement `ConfidenceScorer` (short entry, limit order)
- [ ] Integrate with existing `AnalyticsEngine`
- [ ] Add InfluxDB measurement writers

### Phase 3 — Agent Integration (Week 3)
- [ ] Implement `AgentContextBuilder`
- [ ] Add REST API endpoints to `api_server.py`
- [ ] Add SSE stream for live quant updates
- [ ] Extend `ContextEngine` for gold-specific prompts

### Phase 4 — Validation & Tuning (Week 4)
- [ ] Backtest on 30 days of XAUUSD tick data
- [ ] Tune thresholds (delta σ, imbalance weights, confluence levels)
- [ ] A/B test agent decisions with vs without gold quant
- [ ] Document tuning parameters in Appwrite `service_config`

---

## 9. File Layout

```
remote-services/market_data_service/
├── gold_quant_engine/
│   ├── __init__.py
│   ├── models.py              # GoldQuantSnapshot, Level, Decision
│   ├── tick_volume.py         # TickVolumeAnalyzer
│   ├── order_flow.py          # OrderFlowEngine
│   ├── key_levels.py          # KeyLevelsDetector
│   ├── multi_timeframe.py     # MultiTimeframeConfluence
│   ├── confidence.py          # ConfidenceScorer
│   ├── context_builder.py     # AgentContextBuilder
│   ├── engine.py              # GoldQuantEngine orchestrator
│   └── tests/
│       ├── test_tick_volume.py
│       ├── test_order_flow.py
│       └── test_confluence.py
├── analytics.py               # (extend with gold_quant calls)
├── api_server.py              # (add /gold/* endpoints)
└── influxdb_database.py       # (add store_gold_quant_* methods)
```

---

## 10. Configuration

Add to `config.py` / `dataservice-config.yml`:

```yaml
gold_quant:
  enabled: true
  symbol: "XAUUSD"
  timeframes: ["M15", "H1", "H4", "D1"]
  tick_window: 1000
  delta_std_threshold: 2.0
  imbalance_threshold: 0.30
  min_confluence_tfs: 3
  short_reject_confidence_threshold: 0.30
  limit_order_min_confidence: 0.60
```

---

## 11. Success Metrics

| Metric | Target |
|--------|--------|
| Short entry rejection accuracy | ≥70% (rejected shorts that would have lost) |
| Limit order fill rate | ≥85% at key levels |
| MTF confluence prediction rate | ≥65% (correct directional bias) |
| Agent decision latency | <50ms from tick to decision output |
| False positive short rejection | <20% (good shorts wrongly rejected) |

---

## 12. Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| Overfitting to recent gold regime | Use rolling 30-day parameter calibration; store params in Appwrite config |
| Latency on tick processing | Process in async batch; pre-compute profiles asynchronously |
| InfluxDB write pressure | Batch gold_quant writes every 5s; use SQLite sidecar for hot cache |
| Agent confusion from too many signals | Provide clear `verdict` field; confidence < 0.40 = "WAIT" |
| cTrader depth data quality | Fall back to tick-derived delta if depth unavailable |

---

*End of Design Document*
