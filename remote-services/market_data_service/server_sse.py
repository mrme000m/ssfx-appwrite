"""MCP Market Data Service — SSE/HTTP network transport."""

from __future__ import annotations

import logging

from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.routing import Mount

from .server import (
    app_lifespan,
    server,
)

logger = logging.getLogger(__name__)

# SSE transport endpoint — clients POST messages here
MESSAGE_ENDPOINT = "/messages/"
sse_transport = SseServerTransport(MESSAGE_ENDPOINT)


async def handle_sse(scope, receive, send) -> None:
    """SSE endpoint — handles ASGI lifecycle directly for connect_sse."""
    request = Request(scope, receive, send)
    async with sse_transport.connect_sse(
        request.scope, request.receive, request._send  # type: ignore[reportPrivateUsage]
    ) as (read_stream, write_stream):
        # The lifespan is managed by Starlette (app level), not per-connection.
        # Temporarily swap to a no-op lifespan so server.run() doesn't
        # re-trigger init/shutdown on every SSE connection.
        from mcp.server.lowlevel.server import lifespan as noop_lifespan
        original_lifespan = server.lifespan
        server.lifespan = noop_lifespan  # type: ignore[assignment]
        try:
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
                raise_exceptions=True,
            )
        finally:
            server.lifespan = original_lifespan  # type: ignore[assignment]


# Starlette ASGI app — Mount treats handle_sse as a raw ASGI app,
# bypassing Route's request_response wrapper which forces a second Response.
#
# Starlette iterates routes in order and uses the FIRST match.  Mount("/sse")
# is a prefix match, so it would swallow POST requests to /sse/messages/...
# (where the MCP SDK tells clients to post).  We must list the more-specific
# /sse/messages mount BEFORE the catch-all /sse mount.
starlette_app = Starlette(
    debug=False,
    routes=[
        Mount("/sse/messages", app=sse_transport.handle_post_message),
        Mount("/sse", app=handle_sse),
        Mount("/messages", app=sse_transport.handle_post_message),
    ],
    lifespan=app_lifespan,
)


def main() -> None:
    import uvicorn

    from .config import get_settings

    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    host = getattr(settings, "server_host", "0.0.0.0")
    port = getattr(settings, "server_port", 9001)
    logger.info("Starting MCP SSE server on http://%s:%d/sse", host, port)
    uvicorn.run(
        starlette_app,
        host=host,
        port=port,
    )


if __name__ == "__main__":
    main()
