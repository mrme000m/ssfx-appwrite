# TG Signal Ingestion, Persistence & LLM Context Architecture

> How Telegram trading signals arrive at the downstream runtime (`appwrite-auth-consolidated`), how raw and parsed messages are persisted, and how **signal intelligence** plus **quantitative market context** are fed into the LLM agents that decide whether, how, and when to trade.
>
> **Correction from prior docs:** MongoDB is **not** the signal store anymore. Raw and parsed signals must be persisted in **Appwrite TablesDB** (cloud source of truth) or a **local SQLite** sidecar (offline / fallback), depending on runtime mode.

---

## 1. Ingestion Paths

Signals originate in the upstream `/Volumes/ExMac/code/ssfx/alwaydata` service. See [`cpr00.md`](../cpr00.md) for the upstream contract. There are three ways a signal can reach this workspace:

| Path | Latency | Endpoint / Handler | Best For |
|------|---------|-------------------|----------|
| **Telegram Bot API webhook** | ~100–300 ms | `POST /webhook` → `ssfx_server/web_app.py::telegram_webhook` | Production default |
| **Direct HTTP push** | ~50–150 ms | `POST /api/signals/webhook` → `ssfx_server/admin_api.py` | Lowest latency; verifies HMAC and parses raw signal text |
| **Long polling fall-back** | + poll interval | `bot.get_updates` in `ssfx_server/web_app.py::_poll_updates` | Local dev / no public URL |

For all three paths, the downstream ingestion pipeline is the same:

```
Telegram update or HTTP payload
    │
    ▼
parse_channel_post() ──► drop diagnostics / non-channel-posts
    │
    ▼
_process_channel_post() ──► save raw message ──► classify intent ──► parse signal ──► score / enrich ──► emit to followers
```

Key files:

- `remote-services/ssfx_server/telegram_webhook.py` — parses `channel_post` updates and drops `Price-augmented signal` diagnostics.
- `remote-services/ssfx_server/web_app.py` — webhook handler, background task dispatch, intent-agent call, parsing, and follower fan-out.
- `remote-services/ssfx_server/admin_api.py` — admin `/api/signals/inject` endpoint and HMAC-verified `/api/signals/webhook` endpoint for `SIGNAL_WEBHOOK_URL` pushes.
- `remote-services/ssfx_parser/parser.py` — `ChainedParser` that turns raw text into a `TradeSignal`.

---

## 2. Persistence: Appwrite TablesDB → SQLite → NoOp (MongoDB retired)

Prior documentation referenced MongoDB for raw-message replay and parsed-signal storage. That storage has been retired. The workspace now persists signal data with **Appwrite TablesDB as the source of truth**, **SQLite as a local fallback**, and **NoOp only for tests or when explicitly opted in**.

| Layer | Appwrite TablesDB (primary) | SQLite (local fallback) |
|-------|-----------------------------|-------------------------|
| **Raw messages** | `raw_messages` in `market_data` | `raw_messages` table in the SQLite sidecar |
| **Parsed signals** | `parsed_signals` in `market_data` | `parsed_signals` table in the SQLite sidecar |
| **Trade records** | `signal_trades` in `market_data` | `signal_trades` table in the SQLite sidecar |
| **Quality log** | `signal_quality_log` | Same table in SQLite |
| **Experience dimensions** | `signal_experience_authors`, `signal_experience_sessions`, `signal_experience_patterns`, `signal_experience_overall` | Same tables in SQLite |
| **Follower account config** | `ssfx_accounts` | Not recommended; use Appwrite |
| **Execution history** | `ssfx_executions` | Not recommended; use Appwrite |

Tables are managed through `appwrite.config.json` (source of truth for CI/CD) and the Appwrite CLI v22.4.0+ (`appwrite push tables`). The signal-store tables live in the `market_data` database.

Configuration:

```bash
# Primary backend: appwrite | sqlite | noop
SIGNAL_STORE_BACKEND=appwrite
APPWRITE_SIGNAL_DATABASE_ID=market_data
APPWRITE_RAW_MESSAGES_TABLE=raw_messages
APPWRITE_PARSED_SIGNALS_TABLE=parsed_signals
APPWRITE_SIGNAL_TRADES_TABLE=signal_trades

# Fallback when Appwrite is unreachable (default true)
SIGNAL_STORE_FALLBACK_SQLITE=true
SIGNAL_STORE_SQLITE_PATH=/app/data/signals.db
```

Current runtime notes:

- The `SignalStore` protocol is defined in `remote-services/ssfx_trader/stores/base.py`.
- `AppwriteSignalStore` (`remote-services/ssfx_trader/stores/appwrite_signal_store.py`) is the new primary store and is created by default.
- `SQLiteSignalStore` (`remote-services/ssfx_trader/stores/sqlite_signal_store.py`) is used automatically when `SIGNAL_STORE_BACKEND=sqlite`, or when `SIGNAL_STORE_BACKEND=appwrite` and Appwrite is unreachable while `SIGNAL_STORE_FALLBACK_SQLITE=true`.
- `NoOpSignalStore` is only used when `SIGNAL_STORE_BACKEND=noop`, when fallback is disabled and Appwrite fails, or when an unknown backend is configured. It keeps the pipeline running but disables replay, today-message context, and post-trade analytics.
- For full functionality (especially LLM context and signal-experience learning), use Appwrite or set `SIGNAL_STORE_BACKEND=sqlite` and mount a persistent volume at `SIGNAL_STORE_SQLITE_PATH`.
- Market-data time series (ticks, bars, indicators) is handled separately by the data service, primarily in **InfluxDB Cloud Serverless** with SQLite as an explicit fallback. See [`account-hub-and-dataservice.md`](account-hub-and-dataservice.md).

---

## 3. Where the LLM Is Used

Three LLM agents operate in the `agent_harness` service (`remote-services/agent_harness/`) on `ai.mrme.tech`. Each is invoked by `ssfx_server` at a different stage of the signal lifecycle.

### 3.1 `SignalIntentAgent` (Mistral Small 3.2)

- **Endpoint:** `POST /agent/v1/signal/intent`
- **Source:** `remote-services/agent_harness/agents/signal_intent.py`
- **Role:** Classify each incoming message as:
  - `new_signal`
  - `update_to_existing`
  - `orphan_close`
  - `noise`
- **Current context:** raw text, `message_id`, `reply_to_message_id`, the last ~20 `recent_messages` from the same chat, and a lightweight `experience` block (author stats, streak, opposite-direction alert).
- **System prompt:** instructs the classifier to use author streak/recent SLs and to treat an opposite-direction post from the same author as a brand-new `new_signal` rather than a follow-up.

### 3.2 `EntryDecisionAgent` (Hermes 3)

- **Endpoint:** `POST /agent/v1/entry/decision`
- **Source:** `remote-services/agent_harness/agents/entry_decision.py`
- **Role:** Approve, reject, or modify a new XAUUSD entry signal.
- **Current context:** parsed signal, gold-quant snapshot, rich signal-experience intelligence (author, streak, recent outcomes, opposite-direction flag, pattern/session/overall stats), recent channel messages, and open positions.
- **Output:** `ENTER | REJECT | WAIT | MODIFY`, plus order type, limit price, size multiplier, SL/TP overrides, and reasoning.

### 3.3 `LifecyclePlannerAgent` (Kimi K2.7)

- **Endpoint:** `POST /agent/v1/lifecycle/plan`
- **Source:** `remote-services/agent_harness/agents/lifecycle_planner.py`
- **Role:** Decide what to do with an open position when a follow-up message arrives (TP hit, move SL, close, running update).
- **Current context:** open position, gold-quant snapshot, signal update, and recent channel messages (now populated from the signal store).

All agents are kill-switchable via environment variables (`AGENT_INTENT_ENABLED`, `AGENT_ENTRY_ENABLED`, `AGENT_LIFECYCLE_ENABLED`) and degrade to deterministic rules on timeout or failure.

---

## 4. Signal Intelligence Baked into LLM Context

The prompt for every signal-related LLM call should include a compact **signal-intelligence block**. This block is built from the tables in §2, and it answers questions such as:

> *Has this trader just burned through 2–3 stop-losses? Did they flip direction in the last hour? Is this hour of day historically bad for their calls?*

### 4.1 Intelligence dimensions

| Dimension | Source Table | Example LLM prompt line |
|-----------|--------------|-------------------------|
| **Author track record** | `signal_experience_authors` | `Author 'Liam': 130 signals, 95% win rate, expectancy +84.4 pips, current streak = -2 (last two signals hit SL).` |
| **Current streak** | `signal_experience_authors.current_streak` | `Current streak is -3 → reduce size or wait for confirmation; do not increase risk.` |
| **Recent outcome log** | `signal_quality_log` (rows from last N hours for same author) | `Last 3 signals from this author: SL, SL, BE. Avoid aggressive entries until a winner closes.` |
| **Opposite direction** | Recent classified messages + `signal_quality_log` | `Author posted BUY XAUUSD at 10:05; 35 minutes later posted SELL XAUUSD. Treat as a fresh signal, not a follow-up, and verify market context.` |
| **Session / hour of day** | `signal_experience_sessions` | `UTC hour 15 has 100% win rate (34 samples). Current hour 02 has 38% win rate → caution.` |
| **Pattern performance** | `signal_experience_patterns` | `Pattern XAUUSD_SELL_LIMIT: 90% win rate but negative expectancy (-8.6 pips) → avoid limit sells at market; prefer confirmation.` |
| **Overall market regime** | `signal_experience_overall` | `Rolling 30-day win rate: 61%. Signals today: 7. Good/bad ratio: 1.4.` |
| **Open positions** | cTrader account hub + `account_events` | `Already long 0.5 lots XAUUSD from msg #1234. New signal would add same-direction exposure.` |

### 4.2 Building the block

`market_data_service/signal_experience/reporter.py` already produces a markdown LLM context from the experience tables. The file is regenerated to:

- `remote-services/market_data_service/signal_experience/reports/llm_context.md`
- `remote-services/market_data_service/signal_experience/reports/signal_experience_insights.json`

The JSON is also stored in `signal_experience_overall.insights_json` so the runtime can fetch it without reading the filesystem.

For real-time signal calls, the pipeline now assembles a **per-signal intelligence block** via:

- `market_data_service/signal_experience/context_builder.py::build_entry_context(signal, signal_store, experience_store)`
- `market_data_service/signal_experience/context_builder.py::build_intent_context(signal_stub, signal_store, experience_store)`

These helpers perform the steps above (author resolution, author/pattern/session/overall stats, recent outcomes, opposite-direction scan) and return a JSON-serializable dict.

Wiring:

- `ssfx_server/web_app.py::_call_signal_intent_agent()` calls `build_intent_context()` and sends the result as `experience` to the intent agent.
- `ssfx_trader/executor.py::_execute_new_signal()` calls `build_entry_context()` in a thread and sends the result as `experience` to the entry agent.
- `ssfx_trader/executor.py::_maybe_apply_lifecycle_plan()` fetches today's messages from the signal store and sends them as `recent_messages` to the lifecycle planner.
- `ssfx_trader/agent_harness_client.py` passes `recent_messages` through to both entry and lifecycle endpoints.

This block is passed as `experience` and `recent_messages` to `EntryDecisionAgent`, as `experience` + `recent_messages` to `SignalIntentAgent`, and as `recent_messages` to `LifecyclePlannerAgent`.

### 4.3 Example prompt augmentation

```markdown
## Signal Intelligence

- Author: Liam (total 130, win_rate 0.95, expectancy +84.4 pips)
- Current streak: -2 (last two signals stopped out)
- Last 3 closed signals: SL (-12 pips), SL (-8 pips), BE
- Opposite direction in last 60 min: YES — author sent BUY @ 10:05, now sending SELL @ 10:42
- Pattern: XAUUSD_SELL_LIMIT — 90% win rate but negative expectancy; avoid unless quant strongly confirms
- Session: UTC 10 — 96% win rate (44 samples); high-confidence hour
- Open positions: LONG 0.5 lots XAUUSD from msg #1234 (unrealised +$45)
- 30-day rolling win rate: 61%; good/bad ratio 1.4
```

---

## 5. Market Context via the Data Service

Signal decisions are not made in isolation. The data service (`market.mrme.tech` / `market_data_service`) provides a live quantitative snapshot that the agents consume through `agent_harness/tools/data_service.py`.

### 5.1 Endpoints

| Endpoint | Client Method | Purpose |
|----------|---------------|---------|
| `GET /api/v1/gold/quant` | `DataServiceClient.get_gold_quant_snapshot()` | Full XAUUSD quant snapshot: price, multi-timeframe confluence, order flow, key levels, short-entry / limit-order verdicts |
| `GET /api/v1/context/{symbol}` | `DataServiceClient.get_market_context(symbol)` | Aggregated market context for any symbol (tick, bars, indicators, signal quality) |

Default base URL is configured via `AGENT_HARNESS_DATA_SERVICE_URL` or `DATA_SERVICE_BASE_URL`.

### 5.2 Snapshot contents

The gold-quant snapshot is defined in [`gold-quantitative-analysis-design.md`](gold-quantitative-analysis-design.md). Fields relevant to the LLM agents include:

```json
{
  "symbol": "XAUUSD",
  "timestamp_ms": 1719900000000,
  "price": { "bid": 2345.67, "ask": 2345.92, "mid": 2345.795 },
  "multi_timeframe": {
    "m15": { "direction": "bullish", "score": 0.45, "indicators": [...] },
    "h1":  { "direction": "bullish", "score": 0.35, "indicators": [...] },
    "h4":  { "direction": "bearish", "score": -0.40, "indicators": [...] },
    "d1":  { "direction": "neutral", "score": 0.05, "indicators": [...] },
    "confluence": { "direction": "NEUTRAL", "confidence": 0.35 }
  },
  "order_flow": {
    "tick_delta": -45.2,
    "cumulative_delta": -892.1,
    "delta_regime": "bearish",
    "poc": 2348.50,
    "vah": 2352.10,
    "val": 2341.20,
    "book_imbalance": -0.15
  },
  "key_levels": {
    "support": [2341.20, 2338.50, 2332.00],
    "resistance": [2348.50, 2352.10, 2358.00],
    "fvgs": [{"top": 2348.20, "bottom": 2347.50, "type": "bullish"}],
    "order_blocks": [{"high": 2350.00, "low": 2348.00, "type": "bearish"}]
  },
  "decision": {
    "short_entry": { "verdict": "REJECT", "confidence": 0.15, "reasons": [...] },
    "limit_order": { "verdict": "WAIT", "confidence": 0.40, "nearest_level": {...} }
  },
  "agent_prompt": "<markdown summary for LLM>"
}
```

The `agent_prompt` field is a pre-rendered markdown summary that can be dropped directly into the LLM user prompt.

### 5.3 How market context reaches the agents

1. `ssfx_server` calls `POST /agent/v1/entry/decision` with the parsed signal and signal-experience block.
2. `agent_harness/api.py` sees `quant_snapshot` is `None`.
3. It calls `DataServiceClient.get_gold_quant_snapshot()`.
4. The snapshot is injected into `EntryDecisionAgent._render_prompt()` under `## Quantitative Market Snapshot`.
5. `LifecyclePlannerAgent` follows the same pattern for follow-up messages.

For `SignalIntentAgent`, the snapshot is optional. In the target wiring, the intent agent should also receive a lightweight market-direction hint (e.g. `"quant_bearish_confidence": 0.35`) to help distinguish legitimate direction flips from noise.

---

## 6. Putting It Together: Example Request Payloads

### 6.1 Intent classification

```json
POST /agent/v1/signal/intent
{
  "raw_text": "XAUUSD SELL LIMIT 2350\nSL: 2360\nTP: 2340",
  "message_id": 9876,
  "chat_id": "-1001661400724",
  "reply_to_message_id": null,
  "recent_messages": [
    { "message_id": 9874, "reply_to_message_id": null, "text": "XAUUSD BUY 2345 SL 2335 TP 2355" },
    { "message_id": 9875, "reply_to_message_id": 9872, "text": "TP HIT +10 pips" }
  ],
  "open_positions": [
    "LONG 0.5 lots XAUUSD @ 2345 (msg #9874)"
  ],
  "experience": {
    "author": "Liam",
    "one_line_summary": "Author Liam: streak=-2, win_rate=0.95, expectancy=84.37",
    "opposite_direction_recent": {
      "detected": true,
      "minutes_ago": 35,
      "opposite_message_id": 9874,
      "opposite_raw_text": "XAUUSD BUY 2345 SL 2335 TP 2355"
    }
  }
}
```

### 6.2 Entry decision

```json
POST /agent/v1/entry/decision
{
  "signal": {
    "signal_type": "NEW",
    "direction": "SELL",
    "symbol": "XAUUSD",
    "entry_price": 2350,
    "order_type": "LIMIT",
    "sl": 2360,
    "tp1": 2340,
    "parse_confidence": 0.97,
    "quality_score": 0.62,
    "experience_action": "reduce",
    "raw_text": "..."
  },
  "quant_snapshot": { /* see §5.2 */ },
  "experience": {
    "author": "Liam",
    "author_win_rate": 0.95,
    "author_streak": -2,
    "last_3_outcomes": ["SL", "SL", "BE"],
    "opposite_direction_minutes_ago": 35,
    "pattern": "XAUUSD_SELL_LIMIT",
    "pattern_win_rate": 0.90,
    "pattern_expectancy": -8.61,
    "session_hour": 10,
    "session_win_rate": 0.96,
    "rolling_30d_win_rate": 0.61,
    "signals_today": 7
  },
  "open_positions": [
    { "symbol": "XAUUSD", "direction": "LONG", "volume": 0.5, "entry": 2345 }
  ],
  "recent_messages": [
    { "message_id": 9874, "text": "XAUUSD BUY 2345 SL 2335 TP 2355" },
    { "message_id": 9875, "text": "TP HIT +10 pips" }
  ]
}
```

### 6.3 Lifecycle planning

```json
POST /agent/v1/lifecycle/plan
{
  "position": { "symbol": "XAUUSD", "direction": "LONG", "volume": 0.5, "entry": 2345, "current_price": 2354 },
  "signal_update": { "type": "tp_hit", "tp_number": 1, "pips": 10 },
  "quant_snapshot": { /* see §5.2 */ },
  "recent_messages": [
    "TP1 HIT +10 pips on BUY XAUUSD @ 2345"
  ]
}
```

---

## 7. Operational Runbook Notes

### 7.1 Verify signal persistence is active

Set in `v2.env`:

```bash
SIGNAL_STORE_BACKEND=sqlite
SIGNAL_STORE_SQLITE_PATH=/app/data/signals.db
```

and mount a persistent volume at `/app/data` so the database survives container restarts.

If the logs show:

```text
NoOpSignalStore active — signal history not persisted. Set SIGNAL_STORE_BACKEND=sqlite and mount a persistent volume to enable it.
```

then `ssfx_server` is running with `NoOpSignalStore`. For LLM context and analytics, switch to SQLite (or the Appwrite-backed store once it exists).

### 7.2 Refresh signal experience

The `signal_experience` reporter can be run manually:

```bash
python -m market_data_service.signal_experience.reporter
```

It regenerates `signal_experience/reports/llm_context.md` and `signal_experience/reports/signal_experience_insights.json`, and writes the JSON summary back to `signal_experience_overall.insights_json`.

### 7.3 Reconcile experience with outcomes

When cTrader reports that a position closed (TP, SL, manual close, breakeven), the runtime must write the outcome to `signal_quality_log` and call `SignalExperienceUpdater.update_from_outcome()` so that author/session/pattern stats stay current. This is what makes the intelligence block accurate for the next signal.

---

## 8. Security & Reliability Reminders

- **Webhook secret token:** `telegram_webhook.py` verifies `X-Telegram-Bot-Api-Secret-Token` before accepting a `POST /webhook`.
- **Direct push HMAC:** `admin_api.py` verifies `X-Signal-Signature` on `/api/signals/inject`.
- **Source chat filtering:** Only posts from `SOURCE_CHAT_ID` are processed.
- **Do not log raw signals or HMAC secrets.** The upstream PIN/tokens and downstream `ADMIN_API_KEY` must never appear in logs.
- **Kill switches:** `AGENT_INTENT_ENABLED`, `AGENT_ENTRY_ENABLED`, `AGENT_LIFECYCLE_ENABLED` let operators disable LLM participation instantly.

---

## 9. Related Documentation

- [`cpr00.md`](../cpr00.md) — upstream `alwaydata` signal contract, direct HTTP push schema, and security checklist.
- [`account-hub-and-dataservice.md`](account-hub-and-dataservice.md) — Appwrite-native account hub, data service backends, and InfluxDB/SQLite configuration.
- [`gold-quantitative-analysis-design.md`](gold-quantitative-analysis-design.md) — gold quant engine design, indicators, confluence scoring, and REST endpoints.
- [`AGENTS.md`](../AGENTS.md) — high-level project architecture, TablesDB schema, and agent harness overview.
- `remote-services/market_data_service/signal_experience/reports/llm_context.md` — example generated LLM context from the signal experience tables.

---

*Last updated: 2026-07-04*
