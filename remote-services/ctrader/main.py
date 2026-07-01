"""Entry point for the unified ctrader service."""
from __future__ import annotations

import uvicorn

from ctrader.web_app import app, config


def main() -> None:
    uvicorn.run(
        app,
        host=config.host,
        port=config.port,
        log_level=config.log_level.lower(),
    )


if __name__ == "__main__":
    main()
