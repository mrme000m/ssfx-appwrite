"""CLI helpers for the SSFX webhook server."""
from __future__ import annotations

import argparse
import asyncio
import logging

from telegram import Bot

from .config_loader import load_config

logger = logging.getLogger(__name__)


async def _set_webhook(url: str, token: str, secret_token: str) -> None:
    bot = Bot(token=token)
    await bot.set_webhook(
        url=url,
        allowed_updates=["channel_post"],
        secret_token=secret_token,
    )
    info = await bot.get_webhook_info()
    logger.info("Webhook set: %s (secret_token configured: %s)", info.url, bool(secret_token))
    await bot.session.close()


def set_webhook() -> None:
    parser = argparse.ArgumentParser(description="Set Telegram bot webhook")
    parser.add_argument("--url", help="Webhook URL override")
    args = parser.parse_args()

    config = load_config()
    config.require_webhook_secret()
    logging.basicConfig(level=logging.INFO)

    url = args.url or config.webhook_url
    asyncio.run(
        _set_webhook(
            url,
            config.telegram_bot_token,
            config.telegram_webhook_secret_token,
        )
    )
