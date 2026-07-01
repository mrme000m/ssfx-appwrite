"""Entry point for market-data-service.

Usage:
    python -m market_data_service                    # Start MCP server (stdio)
    python -m market_data_service server             # Start MCP server (stdio)
    python -m market_data_service server-sse         # Start MCP server (SSE/HTTP)
    python -m market_data_service data-service       # Start standalone Data Service
    python -m market_data_service api-server         # Start OpenPI REST API server
"""

from __future__ import annotations

import sys


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "server"

    if mode in ("--help", "-h"):
        print(__doc__)
        sys.exit(0)

    if mode in ("server", "stdio"):
        from .server import main as server_main
        server_main()
    elif mode in ("server-sse", "sse"):
        from .server_sse import main as sse_main
        sse_main()
    elif mode in ("data-service", "data"):
        from .data_service import main as ds_main
        ds_main()
    elif mode in ("api-server", "api"):
        from .api_server import main as api_main
        api_main()
    else:
        print(f"Unknown mode: {mode}")
        print("Usage: python -m market_data_service [server|server-sse|data-service|api-server]")
        sys.exit(1)


if __name__ == "__main__":
    main()
