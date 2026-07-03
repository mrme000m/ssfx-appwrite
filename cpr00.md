# CPR00 — Signal Ingestion Guide for SSFX v2

> How `appwrite-auth` (the downstream trading runtime) should receive trading signals from the upstream `alwaydata` Telegram forwarder.
>
> **Last updated:** 2026-07-03 — reflects the signal-parser refactor, promo filtering, structured signal output, and direct HTTP push capability deployed to alwaysdata.

---

## 1. Architecture Snapshot

### Upstream: `/Volumes/ExMac/code/ssfx/alwaydata`
- Runs a **Telethon user client** (`ForwarderCore` in `core.py`) that listens to source VIP channels via MTProto.
- **New:** `signal_parser.py` classifies every message into `ENTRY`, `ENTRY_PENDING`, `MANAGE`, `RESULT`, `PROMO`, or `NOISE` using pure regex (0 FP / 0 FN on 2 354 historical messages).
- **New:** Promo/spam messages are **hard-filtered** before forwarding — they never reach the destination channel.
- **New:** Price augmentation only fires on `ENTRY` / `ENTRY_PENDING` signals. Close/manage/noise messages skip the cTrader tick fetch, saving API calls and RAM.
- **New:** `ctrader_auth_fallback.py` provides a **web-login fallback** for cTrader token refresh. If the auth broker is down and the refresh token is stale, the service can log into `id.ctrader.com` directly with username/password to acquire fresh tokens.
- For pairs with `forward_via_bot=true`, text is forwarded via the Telegram Bot API (bot must be admin of the destination channel).
- Snapshots are stored in MariaDB (`signal_log`).

### Downstream: `/Volumes/ExMac/code/ssfx/appwrite-auth`
- The trading runtime lives in `remote-services/` and runs on an Azure VM as a Docker container.
- `ssfx_server/web_app.py` exposes a FastAPI webhook endpoint (`POST /webhook`) for the Telegram Bot API.
- Incoming `channel_post` updates are parsed by `telegram_webhook.py`, stored in MongoDB, converted to `TradeSignal`, and routed to every configured `AccountFollower` for execution.

```
VIP source channel
       │
       ▼ (Telethon user client)
alwaydata forwarder
       │
       ├──► signal_parser (classify + filter promos)
       │          │
       │          ├──► ENTRY/PENDING ──┬──► destination channel (Bot API or user forward)
       │          │                    │          │
       │          │                    │          ▼ (Telegram Bot API webhook)
       │          │                    │    ssfx_server /webhook
       │          │                    │          │
       │          │                    │          ▼
       │          │                    │    parse → store → followers → cTrader
       │          │                    │
       │          │                    └──► price-augment snapshot → admin chat
       │          │
       │          └──► PROMO/MANAGE/NOISE → dropped (never forwarded)
       │
       └──► SIGNAL_WEBHOOK_URL (optional direct HTTP push)
                    │
                    ▼
            ssfx_server /api/signals/inject
```

---

## 2. Signal Classification on the Upstream (what reaches you)

The upstream now uses `SignalParser` (`signal_parser.py`) to classify every message before forwarding. You can rely on the following behaviour:

| Classification | What it looks like | Forwarded? | Price augmented? |
|----------------|-------------------|------------|-----------------|
| `ENTRY` | `XAUUSD BUY 4033.87` + `SL:` / `TP:` lines | **Yes** | **Yes** (if enabled) |
| `ENTRY_PENDING` | `XAUUSD BUY LIMIT 4700` + `SL:` / `TP:` | **Yes** | **Yes** (if enabled) |
| `MANAGE` | `CLOSE HALF`, `MOVE SL TO ENTRY`, `TP HIT` | **Yes** | **No** |
| `PROMO` | URLs to copier sites, discount codes, referral links | **No** — **dropped** | **No** |
| `NOISE` | `Delete Pending Order`, `Invalid Parameters` | **Yes** (unless filtered by pair rules) | **No** |

**Implications for the downstream:**
1. You will **never** receive promos from the upstream — the parser drops them before they hit the destination channel.
2. You may still receive `MANAGE` and `NOISE` messages. The downstream parser (`ChainedParser` / `TradeSignal` extractor) should either ignore them or map them to no-op signal types.
3. Price-augmented HTML snapshots arrive at the **admin chat only**, not the destination channel, so they will not show up in your webhook unless the admin chat and destination are the same channel (which is not recommended).

### Structured signal format (optional)

If you want to bypass raw-text parsing, the upstream's `SignalParser.parse_entry()` produces typed fields:

```python
@dataclass(slots=True, frozen=True)
class ParsedSignal:
    symbol: str           # e.g. "XAUUSD"
    direction: str        # "BUY" or "SELL"
    order_type: str|None  # None, "LIMIT", or "STOP"
    entry_price: float|None
    sl: float|None
    tp: float|None
    raw_text: str
```

This could be passed directly via the `SIGNAL_WEBHOOK_URL` outbound push (see §7) instead of relying on downstream regex/LLM parsing.

---

## 3. Recommended Reception Mode: Bot Webhook

A Telegram bot token lets you receive channel updates in two ways:

| Mode | Latency | Connection Cost | Best For |
|------|---------|-----------------|----------|
| **Webhook** | ~100–300 ms | None on your side (Telegram pushes) | Production — always use this |
| **Long polling** (`getUpdates`) | +poll interval (1–5 s typical) | One persistent HTTPS connection | Fallback / local dev |
| **Direct HTTP push** (SIGNAL_WEBHOOK_URL) | ~50–150 ms | One POST per signal | Lowest latency; see §7 |

**Conclusion:** the bot-token webhook is the standard path. The direct HTTP push is optimal if you can expose a stable URL.

---

## 4. Required Configuration (Webhook Path)

Edit `remote-services/config/v2.env`:

```bash
# The bot that is an admin of the destination channel
TELEGRAM_BOT_TOKEN=123456:ABC-DEF...

# Source/destination chat ID used for context and logging
SOURCE_CHAT_ID=-1001661400724

# Public URL Telegram will POST to
WEBHOOK_HOST=https://ssfx-api.mrme.tech
WEBHOOK_PATH=/webhook
SSFX_SERVER_PORT=8000
```

Register the webhook:

```bash
./dev.sh set-telegram-webhook
```

Verify:

```bash
curl "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/getWebhookInfo"
```

Expected:

```json
{
  "ok": true,
  "result": {
    "url": "https://ssfx-api.mrme.tech/webhook",
    "has_custom_certificate": false,
    "pending_update_count": 0,
    "allowed_updates": ["channel_post"]
  }
}
```

---

## 5. Webhook Handler Details

File: `remote-services/ssfx_server/web_app.py`

```python
@app.post(state.config.webhook_path if state else "/webhook")
async def telegram_webhook(request: Request, background_tasks: BackgroundTasks) -> JSONResponse:
    update = await request.json()
    post = parse_channel_post(update)
    if post is None:
        return JSONResponse({"ok": True, "ignored": "not a channel post"})

    background_tasks.add_task(
        _process_channel_post,
        state,
        post.chat_id,
        post.message_id,
        post.text,
        post.reply_to_message_id,
    )
    return JSONResponse({"ok": True})
```

Key behaviours:

1. Only `channel_post` updates are processed (`ssfx_server/telegram_webhook.py`).
2. The handler returns `200 OK` immediately so Telegram does not retry.
3. Parsing and execution run in a background task.
4. Raw messages are saved to MongoDB for replay/context.
5. The `ChainedParser` (LLM → regex) converts text to `TradeSignal`.
6. The signal is distributed to every follower.

### Source-chat filtering (required)

`telegram_webhook()` currently accepts posts from **any** channel where the bot is an admin. Add filtering:

```python
if post.chat_id != state.config.source_chat_id:
    return JSONResponse({"ok": True, "ignored": "wrong chat"})
```

---

## 6. Handling Price-Augmented Snapshots and Diagnostics

`alwaydata` sends two separate things when `price_augment=true` on an entry signal:

1. **The original signal text** forwarded to the **destination channel** → arrives via `/webhook` and is parsed/executed.
2. **An HTML price snapshot** sent to `SIGNAL_ADMIN_CHAT_ID` via the Bot API. This is metadata / diagnostics, not a trade signal.

The snapshot looks like:

```html
<b>Price-augmented signal</b> (#42)

BUY XAUUSD @ 2050

<b>XAUUSD</b> at signal time:
  Bid: <code>2050.123</code>
  Ask: <code>2050.456</code>
  Spread: <code>0.333</code>
  Latency: 2 ms
  cTrader tick ts: July 1, 2026 11:12:33 PM UTC (...)
```

**Do not parse this as a trade signal.** The downstream `telegram_webhook.py` already guards against it:

```python
# remote-services/ssfx_server/telegram_webhook.py
_DIAGNOSTIC_HEADER_PLAIN = "Price-augmented signal"
_DIAGNOSTIC_HEADER_HTML = "<b>Price-augmented signal</b>"

def is_price_augmented_snapshot(text: str) -> bool:
    if not text:
        return False
    stripped = text.strip()
    lower = stripped.lower()
    return lower.startswith(_DIAGNOSTIC_HEADER_PLAIN.lower()) or _DIAGNOSTIC_HEADER_HTML.lower() in lower
```

If you route admin snapshots to the same channel as destination signals (not recommended), this guard drops them before they reach the parser.

---

## 7. Direct HTTP Push — Bypass Telegram Entirely (Recommended for Lowest Latency)

`alwaydata` now supports an **outbound webhook** that fires after every price-augmented signal is processed. This is configured by:

```bash
# In alwaydata .env
SIGNAL_WEBHOOK_URL=https://ssfx-api.mrme.tech/api/signals/inject
SIGNAL_WEBHOOK_SECRET=shared-secret-between-alwaydata-and-ssfx
```

When set, `signal_processor.py` POSTs a JSON payload with HMAC-SHA256 signature:

```json
{
  "event": "signal",
  "received_at_ms": 1782964101000,
  "snapshot": {
    "source_chat_id": -1001661400724,
    "source_message_id": 13743,
    "signal_text": "XAUUSD BUY 4033.87\n\nSL: 4023.87\nTP: 4053.87",
    "symbol": "XAUUSD",
    "price_available": true,
    "bid": 4033.850,
    "ask": 4033.870,
    "spread": 0.020,
    "tick_timestamp_ms": 1782964100987
  }
}
```

Pros:
- **Lowest latency** — no Telegram channel hop.
- **Structured** — bid/ask at signal time is included; no need to re-query market data.
- **No bot admin requirements** — upstream doesn't need a bot in the destination channel.
- **Signed** — `X-Signal-Signature: sha256=<hex>` lets you verify authenticity.

Cons:
- Requires `alwaydata` to know the downstream URL and shared secret.
- If `ssfx-api.mrme.tech` is down, the signal is lost unless you add retry logic on the upstream side (currently the upstream logs the failure but does not queue).

### Verifying the signature (downside)

```python
import hmac, hashlib

def verify_signature(secret: str, body: bytes, header: str) -> bool:
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", header)
```

---

## 8. Fallback: Long Polling with `getUpdates`

If the public webhook URL is unavailable, use the bot token to long-poll Telegram. See the polling snippet in §6 of the previous version of this doc (unchanged). This is only for local dev / disaster recovery.

---

## 9. Upstream Resilience — What Downstream Should Know

The upstream forwarder now has **three layers of token resilience** for its cTrader spot-price connection:

1. **Auth broker** (`internal.mrme.tech` / Appwrite) — primary path; refreshes tokens via grant ID.
2. **Refresh token** — if broker is down but `CTRADER_REFRESH_TOKEN` is valid, exchanges it directly with cTrader.
3. **Web-login fallback** — if both fail, logs into `id.ctrader.com` with `CTRADER_WEB_USERNAME` / `CTRADER_WEB_PASSWORD` to grab fresh tokens via OAuth code flow.

**What this means for downstream:**
- If you rely on the upstream's `bid`/`ask` snapshot (via `SIGNAL_WEBHOOK_URL`), the upstream can now self-heal token issues without manual intervention.
- If the upstream's cTrader connection is down, `price_available` in the webhook payload will be `false`. Your downstream should handle this gracefully (e.g. skip spread validation, fall back to last known price).

---

## 10. Security Checklist

1. **Webhook endpoint:**
   - Use a non-obvious path (`/webhook` is fine behind TLS + source filtering).
   - Filter by `post.chat_id == SOURCE_CHAT_ID`.
   - Return `200 OK` fast to avoid Telegram retries.

2. **Direct HTTP push (`/api/signals/inject`):**
   - Verify `X-Signal-Signature` HMAC against `SIGNAL_WEBHOOK_SECRET`.
   - Rate-limit the endpoint (e.g. max 10 req/s per IP).
   - Reject unknown fields to prevent injection.

3. **Admin API key:** Keep `ADMIN_API_KEY` long and random; it protects `/api/signals/inject` when used outside the webhook flow.

4. **Bot token:** Store `TELEGRAM_BOT_TOKEN` in `v2.env` only; never commit it.

5. **TLS:** Webhook must be HTTPS. Cloudflare Tunnel in front of the Azure VM already provides this.

---

## 11. Operational Runbook

### Start the runtime

```bash
cd /Volumes/ExMac/code/ssfx/appwrite-auth/remote-services
docker compose up -d
```

### Set or reset the webhook

```bash
python -m ssfx_server.cli set-webhook --url https://ssfx-api.mrme.tech/webhook
```

### Check health

```bash
curl https://ssfx-api.mrme.tech/health
```

### Manually inject a signal for testing

```bash
curl -X POST https://ssfx-api.mrme.tech/api/signals/inject \
  -H "Content-Type: application/json" \
  -H "x-admin-key: $ADMIN_API_KEY" \
  -d '{
    "raw_text": "BUY XAUUSD @ 2050 SL 2045 TP1 2055",
    "symbol": "XAUUSD",
    "direction": "BUY",
    "signal_type": "NEW",
    "entry_price": 2050,
    "sl": 2045,
    "tp1": 2055,
    "chat_id": "manual",
    "message_id": 9999
  }'
```

### If Telegram stops delivering

1. Check `getWebhookInfo` for `pending_update_count` > 0 or a recent error.
2. Ensure the public URL is reachable and returns HTTP 200 for `POST /webhook`.
3. Re-register the webhook.
4. As a temporary fallback, switch to polling mode.
5. If using `SIGNAL_WEBHOOK_URL` direct push, check upstream logs (`~/logs/tg-forwarder.log`) for delivery failures.

---

## 12. Decision Matrix

| Scenario | Recommended Reception | File/Path | Notes |
|----------|----------------------|-----------|-------|
| Production (public URL) | **Bot webhook** | `ssfx_server/web_app.py::telegram_webhook` | Standard, battle-tested |
| Production (lowest latency) | **Direct HTTP push** | `signal_processor.py::_post_webhook` → `admin_api.py::inject_signal` | Requires `SIGNAL_WEBHOOK_URL` + secret |
| Local dev / no public URL | Long-polling `getUpdates` | add `_poll_updates` task | Higher latency, simple setup |
| Price-augmented admin snapshots | Ignore in parser | `telegram_webhook.py` diagnostic guard | Already implemented |
| Promo/spam from source | **Nothing to do** — upstream drops them | `signal_parser.py::is_promo()` | Zero false positives observed |

---

## 13. Agent Integration Notes

If you are an **AI agent** updating or extending this system, here are the integration contracts you must respect:

### Upstream → Downstream Contract (Telegram channel)

- **Message format:** free-form text, usually multi-line, always human-readable.
- **Entry signal pattern (high confidence):**
  - Line 1: `SYMBOL DIRECTION [LIMIT|STOP] [PRICE]`
  - Followed by at least one `SL: <price>` or `TP: <price>` line.
- **Promos:** will **never** arrive — filtered upstream.
- **Diagnostics (price snapshots):** arrive at admin chat only, not destination channel.

### Upstream → Downstream Contract (Direct HTTP push)

- **Endpoint:** `POST <SIGNAL_WEBHOOK_URL>`
- **Headers:**
  - `Content-Type: application/json`
  - `X-Signal-Signature: sha256=<hex>`
- **Body schema:**
  ```json
  {
    "event": "signal",
    "received_at_ms": 0,
    "snapshot": {
      "source_chat_id": 0,
      "source_message_id": 0,
      "signal_text": "",
      "signal_date": 0,
      "reply_to_message_id": null,
      "received_at_ms": 0,
      "price_fetched_at_ms": 0,
      "symbol": "XAUUSD",
      "price_available": true,
      "symbol_id": 41,
      "bid": 0.0,
      "ask": 0.0,
      "spread": 0.0,
      "tick_timestamp_ms": 0
    }
  }
  ```
- **Verify signature** before accepting. Reject unknown fields.

### Downstream Parser Contract

- If the webhook update is not a `channel_post`, ignore it (`parse_channel_post` returns `None`).
- If the text contains `Price-augmented signal`, ignore it (diagnostic).
- If `chat_id` does not match `SOURCE_CHAT_ID`, ignore it (source filtering).
- Save raw text to MongoDB before parsing so signals can be replayed.

---

## 14. Summary

- The upstream now has a **production-grade signal parser** that drops promos and classifies messages before forwarding.
- **Price augmentation is entry-only** — you will not receive augmented snapshots for manage/close messages.
- The **recommended production path** remains the Telegram Bot API webhook, but the **direct HTTP push (`SIGNAL_WEBHOOK_URL`)** is the lowest-latency option and includes pre-fetched bid/ask data.
- The upstream can **self-heal cTrader token issues** via a three-tier fallback (broker → refresh → web login), improving overall availability.
- For agents modifying this system: respect the contracts in §13, verify HMAC signatures on direct pushes, and always filter by `SOURCE_CHAT_ID`.
