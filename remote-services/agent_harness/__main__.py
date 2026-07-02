"""Entry point for the agent harness service."""

from __future__ import annotations

import logging
import os

import uvicorn

from .config import get_settings


def main() -> None:
    settings = get_settings()
    log_level = os.environ.get("AGENT_HARNESS_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    uvicorn.run(
        "agent_harness.api:app",
        host=settings.agent_harness_host,
        port=settings.agent_harness_port,
        log_level=log_level.lower(),
    )


if __name__ == "__main__":
    main()
