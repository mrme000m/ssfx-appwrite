"""FastAPI webhook server for SSFX v2."""
from __future__ import annotations

import asyncio
import logging
import secrets
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from telegram import Bot

from market_data_service.signal_experience.classifier import extract_author
from market_data_service.signal_experience.scorer import SignalExperienceScorer
from market_data_service.signal_experience.store import SignalExperienceStore
from market_data_service.signal_experience.updater import SignalExperienceUpdater
from ssfx_parser import RawMessage, SignalStatus
from ssfx_trader.config import AccountConfig
from ssfx_trader.factory import create_follower, create_parser
from ssfx_trader.follower import AccountFollower
from ssfx_trader.stores.appwrite_account_store import AppwriteAccountStore
from ssfx_trader.stores.mongo_store import MongoSignalStore
from ssfx_trader.stores.noop_store import NoOpSignalStore

from . import admin_api
from .config_loader import ServerConfig, load_config
from .telegram_webhook import parse_channel_post

logger = logging.getLogger(__name__)


class AppState:
    def __init__(self, config: ServerConfig):
        self.config = config
        # Try MongoDB for signal history; fall back to no-op if unavailable
        try:
            self.signal_store = MongoSignalStore(config.mongo_uri, config.mongo_database)
        except Exception as exc:
            logger.warning("MongoDB unavailable (%s); signal history disabled", exc)
            self.signal_store = NoOpSignalStore()
        self.parser = create_parser(config.agent_config())
        self.followers: dict[str, AccountFollower] = {}
        self._follower_tasks: set[asyncio.Task] = set()
        self.experience_scorer: SignalExperienceScorer | None = None
        self.experience_updater: SignalExperienceUpdater | None = None
        if config.signal_experience_enabled:
            try:
                exp_store = SignalExperienceStore(database_id=config.signal_experience_database_id)
                self.experience_scorer = SignalExperienceScorer(
                    exp_store,
                    block_threshold=config.signal_experience_block_threshold,
                    reduce_threshold=config.signal_experience_reduce_threshold,
                )
                self.experience_updater = SignalExperienceUpdater(exp_store)
                logger.info("Signal experience scoring enabled")
            except Exception as exc:
                logger.warning("Failed to initialize signal experience scorer: %s", exc)

    async def start(self) -> None:
        # Use Appwrite exclusively; never fall back to MongoDB
        if not self.config.appwrite_api_key:
            logger.error("APPWRITE_API_KEY is required. Set it in v2.env.")
            return
        self.account_store = AppwriteAccountStore()
        source = "Appwrite"
        accounts = self.account_store.list_accounts()
        if not accounts:
            logger.warning("No accounts configured. Create one in the SSFX config UI.")

        for doc in accounts:
            try:
                cfg = AccountConfig.from_mongo(doc)
                follower = create_follower(
                    cfg,
                    self.config.mongo_uri,
                    self.config.mongo_database,
                    signal_store=self.signal_store,
                    account_store=self.account_store,
                    data_service_base_url=self.config.dataservice_base_url,
                    data_service_api_key=self.config.dataservice_api_key,
                    experience_updater=self.experience_updater,
                )
                await follower._executor._backend.connect()
                task = asyncio.create_task(follower.start())
                self._follower_tasks.add(task)
                task.add_done_callback(self._follower_tasks.discard)
                self.followers[cfg.name] = follower
                logger.info("Started follower for account %s (source: %s)", cfg.name, source)
            except Exception as exc:
                logger.error("Failed to start follower for %s: %s", doc.get("_id"), exc)

    async def stop(self) -> None:
        for follower in self.followers.values():
            follower.stop()
        if self._follower_tasks:
            await asyncio.gather(*self._follower_tasks, return_exceptions=True)
        await self.parser.close()

    def _follower_tasks_done(self, task: asyncio.Task) -> None:
        self._follower_tasks.discard(task)


state: AppState | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global state
    config = load_config()
    logging.basicConfig(level=getattr(logging, config.log_level.upper(), logging.INFO))
    state = AppState(config)
    await state.start()
    admin_api.set_state(state)
    if state.config.webhook_host.lower() == "polling":
        logger.info("Webhook host set to 'polling'; starting getUpdates fallback")
        asyncio.create_task(_poll_updates(state))
    else:
        logger.info("Webhook mode: %s", config.webhook_url)
    yield
    await state.stop()


app = FastAPI(title="SSFX v2", lifespan=lifespan)

_CORS_ORIGINS = [
    origin.strip()
    for origin in (
        f"{load_config().admin_site_origin},"
        "http://localhost:8001,http://127.0.0.1:8001,"
        "http://localhost:3000,http://127.0.0.1:3000,"
        "http://localhost:5000,http://127.0.0.1:5000"
    ).split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(admin_api.router)


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "accounts": list(state.followers.keys()) if state else [],
        "active_positions": {
            name: follower._executor.active_position_count
            for name, follower in (state.followers.items() if state else [])
        },
    }


@app.post(state.config.webhook_path if state else "/webhook")
async def telegram_webhook(request: Request, background_tasks: BackgroundTasks) -> JSONResponse:
    if state is None:
        return JSONResponse({"ok": False, "error": "server not initialized"}, status_code=503)

    # Verify Telegram webhook secret token
    secret_token = state.config.telegram_webhook_secret_token
    if secret_token:
        provided_token = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if not provided_token or not secrets.compare_digest(secret_token, provided_token):
            logger.warning(
                "Telegram webhook signature verification failed. "
                "Expected secret token but got: %s",
                "<missing>" if not provided_token else "<mismatch>",
            )
            return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)

    try:
        update = await request.json()
    except Exception as exc:
        logger.warning("Invalid webhook payload: %s", exc)
        return JSONResponse({"ok": False, "error": "invalid json"}, status_code=400)

    post = parse_channel_post(update)
    if post is None:
        return JSONResponse({"ok": True, "ignored": "not a channel post"})

    if post.chat_id != state.config.source_chat_id:
        logger.info(
            "Ignoring post from chat %s (expected source_chat_id %s)",
            post.chat_id,
            state.config.source_chat_id,
        )
        return JSONResponse({"ok": True, "ignored": "wrong chat"})

    logger.info(
        "Webhook from chat=%s message=%s reply_to=%s text=%r",
        post.chat_id,
        post.message_id,
        post.reply_to_message_id,
        post.text[:80],
    )

    background_tasks.add_task(
        _process_channel_post,
        state,
        post.chat_id,
        post.message_id,
        post.text,
        post.reply_to_message_id,
    )

    return JSONResponse({"ok": True})


async def _call_signal_intent_agent(
    app_state: AppState,
    text: str,
    message_id: int,
    chat_id: str,
    reply_to_message_id: int | None,
    today_msgs: list[Any],
) -> dict[str, Any] | None:
    if not app_state.config.agent_intent_enabled:
        return None
    base = app_state.config.agent_harness_base_url.rstrip("/")
    url = f"{base}/agent/v1/signal/intent"
    recent = []
    for msg in today_msgs:
        if msg.message_id == message_id:
            continue
        recent.append({
            "message_id": msg.message_id,
            "reply_to_message_id": msg.reply_to_message_id,
            "text": msg.text[:400],
        })
    payload = {
        "raw_text": text,
        "message_id": message_id,
        "chat_id": chat_id,
        "reply_to_message_id": reply_to_message_id,
        "recent_messages": recent[-20:],
        "open_positions": [],
    }
    try:
        async with httpx.AsyncClient(timeout=2.5) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:
        logger.warning("Signal intent agent call failed: %s", exc)
        return None


async def _process_channel_post(
    app_state: AppState,
    chat_id: str,
    message_id: int,
    text: str,
    reply_to_message_id: int | None,
) -> None:
    timestamp_ms = int(__import__("time").time() * 1000)

    raw_msg = RawMessage(
        chat_id=chat_id,
        message_id=message_id,
        text=text,
        reply_to_message_id=reply_to_message_id,
        timestamp_ms=timestamp_ms,
    )
    app_state.signal_store.save_raw_message(raw_msg)

    today_msgs = app_state.signal_store.get_today_messages(chat_id)

    # Ask the agent harness to classify intent and link to prior messages
    intent_result = await _call_signal_intent_agent(
        app_state, text, message_id, chat_id, reply_to_message_id, today_msgs
    )
    if intent_result:
        result = intent_result.get("result", {})
        intent = result.get("intent", "unknown")
        linked = result.get("linked_message_id")
        if linked and reply_to_message_id is None:
            reply_to_message_id = linked
        logger.info(
            "Signal intent for message %s: %s (linked=%s, conf=%.2f)",
            message_id,
            intent,
            linked,
            result.get("confidence", 0.0),
        )

    context_lines: list[str] = []
    for msg in today_msgs:
        if msg.message_id == message_id:
            continue
        reply_info = f" [reply to msg #{msg.reply_to_message_id}]" if msg.reply_to_message_id else ""
        snippet = msg.text[:500] if len(msg.text) > 500 else msg.text
        context_lines.append(f"[msg #{msg.message_id}]{reply_info}: {snippet}")

    # Append agent intent guidance to parser context
    if intent_result:
        intent_note = f"Agent intent classification: {intent_result.get('result', {}).get('intent', 'unknown')}"
        reasoning = intent_result.get("result", {}).get("reasoning", "")
        if reasoning:
            intent_note += f" — {reasoning}"
        context_lines.append(intent_note)

    signal = await app_state.parser.parse(
        raw_text=text,
        message_id=message_id,
        chat_id=chat_id,
        reply_to_message_id=reply_to_message_id,
        timestamp_ms=timestamp_ms,
        context=context_lines,
    )

    if signal is None:
        logger.info("No signal parsed from message %s", message_id)
        return

    if app_state.experience_scorer is not None:
        author = extract_author(text)
        signal = app_state.experience_scorer.enrich(signal, author_name=author)
        logger.info(
            "Signal experience score for message %s: %.2f (%s)",
            message_id,
            signal.quality_score,
            signal.experience_action,
        )

    signal.status = SignalStatus.EMITTED
    app_state.signal_store.save_signal(signal)
    logger.info(
        "Signal emitted: %s %s %s entry=%s confidence=%.2f parser=%s",
        signal.signal_type,
        signal.direction,
        signal.symbol,
        signal.entry_price,
        signal.parse_confidence,
        signal.parser_used,
    )

    for follower in app_state.followers.values():
        await follower.on_signal(signal)


async def _poll_updates(app_state: AppState) -> None:
    """Long-polling fallback using Telegram Bot getUpdates."""
    bot = Bot(token=app_state.config.telegram_bot_token)
    offset = 0
    try:
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
                    if post.chat_id != app_state.config.source_chat_id:
                        logger.info(
                            "Poll ignoring post from chat %s (expected %s)",
                            post.chat_id,
                            app_state.config.source_chat_id,
                        )
                        continue
                    await _process_channel_post(
                        app_state,
                        post.chat_id,
                        post.message_id,
                        post.text,
                        post.reply_to_message_id,
                    )
            except Exception as exc:
                logger.exception("Poll error: %s", exc)
                await asyncio.sleep(5)
    finally:
        await bot.session.close()


def main() -> None:
    import uvicorn

    config = load_config()
    logging.basicConfig(level=getattr(logging, config.log_level.upper(), logging.INFO))
    uvicorn.run(
        "ssfx_server.web_app:app",
        host="0.0.0.0",
        port=config.webhook_port,
        log_level=config.log_level.lower(),
    )
