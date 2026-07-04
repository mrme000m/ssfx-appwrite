# Gold Quantitative Analysis Engine — Integration & Deployment Checklist

**Date:** 2026-07-02
**Status:** Integration Complete — Ready for Deployment

---

## What Was Integrated

The Gold Quantitative Analysis Engine is now fully wired into the existing slwp market data infrastructure:

- [x] **Tick ingestion** — Real-time cTrader ticks flow into `GoldQuantEngine` via `DataIngestionEngine`
- [x] **Bar ingestion** — OHLCV bars from the analytics cycle feed MTF computations
- [x] **Depth ingestion** — Order-book snapshots enrich order-flow imbalance metrics
- [x] **Periodic snapshot refresh** — Every 5 seconds, the engine recomputes MTF, levels, and decisions
- [x] **Database persistence** — Snapshots stored to InfluxDB (or SQLite sidecar)
- [x] **REST API** — Six endpoints exposed on the API server (port 9002)
- [x] **Control API** — Two endpoints on the Data Service control API (port 9000)
- [x] **Configuration** — All parameters exposed via `Settings` / `.env`

---

## Files Added/Modified

### New Files

```
remote-services/market_data_service/gold_quant_engine/
├── __init__.py
├── models.py
├── tick_volume.py
├── order_flow.py
├── key_levels.py
├── multi_timeframe.py
├── confidence.py
├── context_builder.py
├── engine.py
└── tests/
    ├── test_gold_quant.py
    └── test_integration.py

docs/gold-quantitative-analysis-design.md
```

### Modified Files

```
remote-services/market_data_service/
├── data_service.py        (gold quant init, tick/depth processors, snapshot loop)
├── api_server.py          (6 gold REST endpoints + _fetch_gold_snapshot)
├── config.py              (9 new GOLD_QUANT_* settings)
├── database.py            (store_gold_quant_snapshot + SQLite table)
└── influxdb_database.py   (store_gold_quant_snapshot InfluxDB implementation)
```

---

## Configuration

Add these to `remote-services/config/dataservice.env` (or `.env`):

```env
# ── Gold Quantitative Analysis ──────────────────────────────────────
GOLD_QUANT_ENABLED=true
GOLD_QUANT_SYMBOL=XAUUSD
GOLD_QUANT_TIMEFRAMES=M15,H1,H4
GOLD_QUANT_TICK_WINDOW=1000
GOLD_QUANT_DELTA_STD_THRESHOLD=2.0
GOLD_QUANT_IMBALANCE_THRESHOLD=0.30
GOLD_QUANT_MIN_CONFLUENCE_TFS=3
GOLD_QUANT_SHORT_REJECT_THRESHOLD=0.30
GOLD_QUANT_LIMIT_MIN_CONFIDENCE=0.60
```

Or set in `config/dataservice-config.yml` under a `gold_quant` key if you prefer YAML (the `data_service.py` now reads directly from `Settings` fields, not a nested dict).

---

## API Endpoints

### Public API (port 9002) — proxied from Data Service

| Endpoint | Description |
|----------|-------------|
| `GET /api/v1/gold/quant` | Full snapshot (MTF + order flow + levels + decisions) |
| `GET /api/v1/gold/mtf` | Multi-timeframe confluence only |
| `GET /api/v1/gold/orderflow` | Tick volume & order flow metrics |
| `GET /api/v1/gold/levels` | Structural levels (S/R, FVG, OB, Fib) |
| `GET /api/v1/gold/decision` | Agent decision matrix |
| `GET /api/v1/gold/prompt` | LLM-ready prompt text |

### Control API (port 9000) — direct from Data Service

| Endpoint | Description |
|----------|-------------|
| `GET /gold/quant` | Raw snapshot dict from the running engine |
| `GET /gold/health` | Engine status, symbol, timeframes |

---

## Data Flow

```
cTrader Feed → FeedManager → DataIngestionEngine
                                    │
                                    ├──→ Tick Queue → GoldQuantEngine.ingest_tick()
                                    ├──→ Bar Queue  → Analytics → GoldQuantEngine.set_bars()
                                    └──→ Depth Queue → GoldQuantEngine.ingest_depth()

                                    Every 5s:
                                    GoldQuantEngine.get_snapshot_sync()
                                         │
                                         ├──→ InfluxDB (gold_quant_snapshot measurement)
                                         └──→ DataService Control API (/gold/quant)

API Server (port 9002)
    └──→ GET /api/v1/gold/quant → proxies to DataService /gold/quant
```

---

## Activation Steps

1. **Ensure XAUUSD is subscribed** in the symbol registry with `collect_ticks=true` and `collect_bars=true` for timeframes M15, H1, H4.

2. **Add config values** to `remote-services/config/dataservice.env` (see Configuration above).

3. **Restart the Data Service**:
   ```bash
   cd /Volumes/ExMac/code/ssfx/appwrite-auth-consolidated/remote-services
   docker compose restart dataservice-daemon dataservice-api
   # OR locally:
   python -m market_data_service data-service
   ```

4. **Verify the engine loaded**:
   ```bash
   curl http://localhost:9000/gold/health
   # Expected: {"enabled": true, "symbol": "XAUUSD", "timeframes": ["M15","H1","H4"]}
   ```

5. **Test the snapshot endpoint**:
   ```bash
   curl http://localhost:9002/api/v1/gold/quant
   # Should return JSON with multi_timeframe, order_flow, key_levels, decision
   ```

6. **Check the agent prompt**:
   ```bash
   curl http://localhost:9002/api/v1/gold/prompt
   # Should return a markdown-formatted trading analysis
   ```

---

## How Trading Agents Consume This

### Pre-Entry Validation (Signal Experience Scorer)

Before accepting a Telegram short signal on XAUUSD:

```python
# In ssfx_trader/executor.py or signal_experience/scorer.py
async def validate_signal(signal):
    if signal.symbol == "XAUUSD" and signal.direction.value == "SELL":
        resp = httpx.get("http://localhost:9002/api/v1/gold/decision")
        data = resp.json()
        short = data["short_entry"]
        if short["verdict"] == "REJECT":
            logger.warning(f"Gold quant rejects short: {short['reasons']}")
            return False
    return True
```

### Limit Order Placement

```python
# When placing a limit order
resp = httpx.get("http://localhost:9002/api/v1/gold/levels")
data = resp.json()
levels = data["key_levels"]

# Find nearest actionable level
for fvg in levels["fvgs"]:
    if abs(fvg["top"] - current_price) < 5.0:
        place_limit_order(price=fvg["top"], sl=...)
```

### In-Trade Lifecycle Management

```python
# Periodically query for phase guidance
resp = httpx.get("http://localhost:9002/api/v1/gold/quant")
guidance = resp.json()["decision"]["phase_guidance"]
# e.g., {"in_trade": "Strong bearish confluence — hold position, trail SL"}
```

---

## Monitoring

### Logs to watch

```bash
docker compose logs -f dataservice-daemon | grep -i "gold quant"
```

Expected healthy log lines:
```
GoldQuantEngine initialized for XAUUSD (symbol_id=1)
Gold quant snapshot refreshed: mtf=STRONGLY_BEARISH confidence=0.75
```

### Metrics

In InfluxDB (if using that backend):
```sql
SELECT last("mtf_confidence"), last("short_confidence")
FROM "gold_quant_snapshot"
WHERE time > now() - 1h
GROUP BY time(1m)
```

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `GoldQuantEngine unavailable` | Import error or config disabled | Check logs for import traceback; ensure `GOLD_QUANT_ENABLED=true` |
| Empty snapshot (no MTF scores) | No bars in database for timeframes | Verify cTrader feed is connected and XAUUSD bars are being stored. Check `get_bars` returns ≥50 bars. |
| All decisions = WAIT | Not enough confluence | Normal if market is ranging. Lower `GOLD_QUANT_MIN_CONFLUENCE_TFS` to 2 for more signals (riskier). |
| High latency (>50ms) | Tick queue backpressure | Increase `tick_buffer_size` or reduce `tick_window_size`. |
| No order flow metrics | Depth not subscribed | Enable `collect_depth=true` for XAUUSD in symbol config. |

---

## Tuning Parameters

Edit `.env` or `dataservice-config.yml` and restart:

| Parameter | Default | Effect |
|-----------|---------|--------|
| `GOLD_QUANT_DELTA_STD_THRESHOLD` | 2.0 | Higher = more lenient on delta extremes (fewer rejects) |
| `GOLD_QUANT_IMBALANCE_THRESHOLD` | 0.30 | Book imbalance required to flag strong directional pressure |
| `GOLD_QUANT_MIN_CONFLUENCE_TFS` | 3 | How many TFs must agree for a directional bias |
| `GOLD_QUANT_SHORT_REJECT_THRESHOLD` | 0.30 | Confidence below which shorts are auto-rejected |
| `GOLD_QUANT_LIMIT_MIN_CONFIDENCE` | 0.60 | Minimum confidence to suggest a limit order |

---

*End of checklist*
