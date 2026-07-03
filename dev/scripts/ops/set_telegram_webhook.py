#!/usr/bin/env python3
"""Set the Telegram bot webhook URL.

Usage:
    ./dev.sh set-telegram-webhook [--url https://...]
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Allow importing ssfx_server from the repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "remote-services"))

from telegram import Bot

from ssfx_server.config_loader import load_config

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


def main() -> None:
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


if __name__ == "__main__":
    main()
