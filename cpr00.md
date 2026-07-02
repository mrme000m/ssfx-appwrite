# CPR00 — Signal Ingestion Guide for SSFX v2

> How `appwrite-auth` (the downstream trading runtime) should receive trading signals from the upstream `alwaydata` Telegram forwarder using a Telegram bot token, without maintaining a persistent MTProto/Telethon connection.

---

## 1. Architecture Snapshot

### Upstream: `/Volumes/ExMac/code/ssfx/alwaydata`
- Runs a **Telethon user client** (`ForwarderCore` in `core.py`) that listens to source VIP channels via MTProto.
- Forwards messages to one or more destination channels.
- For pairs with `price_augment=true`, it attaches a live cTrader tick snapshot and sends an HTML summary to an admin chat via the **Telegram Bot API** (`signal_processor.py`).
- It also stores snapshots in its own MariaDB (`signal_log`).

### Downstream: `/Volumes/ExMac/code/ssfx/appwrite-auth`
- The trading runtime lives in `remote-services/` and runs on an Azure VM as a Docker container.
- `ssfx_server/web_app.py` exposes a FastAPI webhook endpoint (`POST /webhook`) for the Telegram Bot API.
- The bot must be an **administrator** of the destination channel that `alwaydata` forwards into.
- Incoming `channel_post` updates are parsed, stored in MongoDB, parsed into `TradeSignal`, and routed to every configured `AccountFollower` for execution.

```
VIP source channel
       │
       ▼ (Telethon user client)
alwaydata forwarder
       │
       ├──► destination channel (text forward)
       │          │
       │          ▼ (Telegram Bot API webhook)
       │    ssfx_server /webhook
       │          │
       │          ▼
       │    parse → store → followers → cTrader
       │
       └──► admin chat (price-augmented HTML snapshot via Bot API)
```

---

## 2. Recommended Reception Mode: Bot Webhook (Lowest Latency)

A Telegram bot token lets you receive channel updates in two ways:

| Mode | Latency | Connection Cost | Best For |
|------|---------|-----------------|----------|
| **Webhook** | ~100–300 ms | None on your side (Telegram pushes) | Production — always use this |
| **Long polling** (`getUpdates`) | +poll interval (1–5 s typical) | One persistent HTTPS connection | Fallback / local dev |
| **Periodic `getUpdates` with offset** | +poll interval | Per-poll request | Recovery only |

**Conclusion:** the bot-token webhook is the most latency-free option because Telegram pushes the `Update` to your public URL as soon as the message is posted. It does **not** require maintaining a long-lived MTProto/Telethon connection like the upstream forwarder does.

---

## 3. Required Configuration

Edit `remote-services/config/v2.env` (mounted at `/app/config/v2.env` in the container):

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

Then register the webhook:

```bash
# From inside the container / repo root
python -m ssfx_server.cli set-webhook
```

This calls `Bot.set_webhook(url=..., allowed_updates=["channel_post"])` (`ssfx_server/cli.py`).

Verify with:

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

## 4. How the Webhook Handler Works

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

Key behaviors:

1. Only `channel_post` updates are processed (`ssfx_server/telegram_webhook.py`).
2. The handler returns `200 OK` immediately so Telegram does not retry.
3. Parsing and execution run in a background task.
4. Raw messages are saved to MongoDB for replay/context.
5. The `ChainedParser` (LLM → regex) converts text to `TradeSignal`.
6. The signal is distributed to every follower:

```python
for follower in app_state.followers.values():
    await follower.on_signal(signal)
```

### Current Gap: Source-Chat Filtering

`config_loader.py` loads `SOURCE_CHAT_ID`, but `telegram_webhook()` currently accepts posts from **any** channel where the bot is an admin. Add filtering if the destination channel should not be the only input:

```python
if post.chat_id != state.config.source_chat_id:
    return JSONResponse({"ok": True, "ignored": "wrong chat"})
```

---

## 5. Handling Price-Augmented Snapshots

`alwaydata` sends two separate things when `price_augment=true`:

1. **The original signal text** forwarded to the destination channel → arrives via `/webhook` and is parsed/executed.
2. **An HTML price snapshot** sent to `SIGNAL_ADMIN_CHAT_ID` via the Bot API (`signal_processor.py`). This is metadata, not a trade signal.

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

`ssfx_server` should treat this as a diagnostic message and **not** parse it as a trade signal. Options:

- Send snapshots to a different admin chat than the destination channel (recommended).
- If they arrive at the same channel, detect the `<b>Price-augmented signal</b>` header and ignore.

Example guard in `telegram_webhook.py`:

```python
def parse_channel_post(update: dict[str, Any]) -> ChannelPost | None:
    channel_post = update.get("channel_post")
    if not channel_post:
        return None
    text = channel_post.get("text", "") or channel_post.get("caption", "")
    if text.startswith("Price-augmented signal") or "<b>Price-augmented signal</b>" in text:
        return None  # diagnostic snapshot, not a trade signal
    ...
```

---

## 6. Fallback: Long Polling with `getUpdates`

If the public webhook URL is unavailable (local development, tunnel issues, dynamic IP), use the bot token to long-poll Telegram.

Add a small polling task in `ssfx_server/web_app.py`:

```python
import asyncio
from telegram import Bot

async def _poll_updates(state: AppState) -> None:
    bot = Bot(token=state.config.telegram_bot_token)
    offset = 0
    while True:
        try:
            updates = await bot.get_updates(
                offset=offset,
                limit=100,
                timeout=30,
                allowed_updates=["channel_post"],
            )
            for update in updates:
                offset = update.update_id + 1
                post = parse_channel_post(update.to_dict())
                if post is None:
                    continue
                await _process_channel_post(
                    state,
                    post.chat_id,
                    post.message_id,
                    post.text,
                    post.reply_to_message_id,
                )
        except Exception as exc:
            logger.exception("Poll error: %s", exc)
            await asyncio.sleep(5)
```

Start it from the lifespan when `WEBHOOK_HOST` is unset or set to a sentinel like `polling`:

```python
if config.webhook_host.lower() == "polling":
    asyncio.create_task(_poll_updates(state))
```

Trade-off: latency is bounded by the poll interval (Telegram long-polling timeout) plus one RTT. For production, webhook remains superior.

---

## 7. Optional: Bypass Telegram Entirely via Direct HTTP Push

If `alwaydata` and `appwrite-auth` will always be co-deployed or can reach each other, the lowest-latency path is a direct HTTP POST from `alwaydata` to `appwrite-auth`:

```
alwaydata
  │
  └── POST https://ssfx-api.mrme.tech/api/signals/inject
        │  x-admin-key: <ADMIN_API_KEY>
        │  {
        │    "raw_text": "BUY XAUUSD @ 2050 ...",
        │    "symbol": "XAUUSD",
        │    "direction": "BUY",
        │    "signal_type": "NEW",
        │    "entry_price": 2050.0,
        │    "sl": 2045.0,
        │    "tp1": 2055.0,
        │    "chat_id": "-1001661400724",
        │    "message_id": 42
        │  }
        ▼
   ssfx_server/admin_api.py::inject_signal()
```

Pros:
- Removes the Telegram channel hop.
- Removes dependency on the upstream Telethon session.
- Sub-second end-to-end latency.

Cons:
- Requires `alwaydata` to know the downstream URL and admin key.
- Loses the audit trail of the forwarded Telegram message (unless you store it).

Implementation sketch for `alwaydata`:

```python
# In core.py or signal_processor.py
async def _push_to_downstream(self, text: str, message_id: int, chat_id: int):
    import urllib.request, json
    payload = {
        "raw_text": text,
        "symbol": "XAUUSD",  # or extract from text
        "direction": "BUY",  # or extract from text
        "signal_type": "NEW",
        "chat_id": str(chat_id),
        "message_id": message_id,
    }
    req = urllib.request.Request(
        "https://ssfx-api.mrme.tech/api/signals/inject",
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "x-admin-key": DOWNSTREAM_ADMIN_API_KEY,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())
```

> Note: `inject_signal` currently requires a parsed payload (`symbol`, `direction`, etc.). If `alwaydata` only forwards raw text, use the webhook path instead and let the downstream parser handle it.

---

## 8. Security Checklist

1. **Webhook secret:** Telegram bot webhooks cannot carry a custom secret header. Protect the endpoint by:
   - Using a hard-to-guess path (`/webhook` is fine if paired with TLS and source-IP allow-listing).
   - Verifying the payload structure (already done via `parse_channel_post`).
   - Filtering by `post.chat_id == SOURCE_CHAT_ID`.
2. **Admin API key:** Keep `ADMIN_API_KEY` long and random; it protects `/api/signals/inject`.
3. **Bot token:** Store `TELEGRAM_BOT_TOKEN` in `v2.env` only; never commit it.
4. **TLS:** Webhook must be HTTPS. Cloudflare Tunnel in front of the Azure VM already provides this.

---

## 9. Operational Runbook

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

---

## 10. Decision Matrix

| Scenario | Recommended Reception | File/Path |
|----------|----------------------|-----------|
| Production with public URL | **Bot webhook** | `ssfx_server/web_app.py::telegram_webhook` |
| Local dev / no public URL | Long-polling `getUpdates` | add `_poll_updates` task |
| Upstream can reach downstream | Direct HTTP push to `/api/signals/inject` | `admin_api.py::inject_signal` |
| Price-augmented admin snapshots | Ignore in webhook parser | `telegram_webhook.py` diagnostic guard |

---

## 11. Summary

- `appwrite-auth` should receive signals via the **Telegram Bot API using `TELEGRAM_BOT_TOKEN` + webhook**.
- This avoids the need for the downstream to maintain a Telethon/MTProto connection; only the upstream `alwaydata` needs that user-client session to read restricted VIP channels.
- The webhook endpoint is already implemented in `ssfx_server/web_app.py`.
- Add `SOURCE_CHAT_ID` filtering and a price-snapshot guard for robustness.
- Use long-polling only as a fallback.
- For minimum latency and maximum reliability, consider a direct HTTP push from `alwaydata` to `ssfx_server/admin_api.py::inject_signal`.
