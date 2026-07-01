"""Appwrite JWT authentication dependencies for FastAPI.

The browser/admin UI authenticates with Appwrite and sends the resulting
JWT as a Bearer token. This middleware validates that token against the
Appwrite Account API using the project's server configuration.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import get_settings

logger = logging.getLogger(__name__)

security = HTTPBearer(auto_error=False)


def _auth_disabled() -> bool:
    return os.getenv("APPWRITE_AUTH_DISABLED", "").lower() in ("1", "true", "yes")


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> dict[str, Any]:
    """Validate the Appwrite JWT and return the Appwrite user document."""
    if _auth_disabled():
        return {"$id": "system", "email": "dev@local", "labels": ["admin"]}

    token = credentials.credentials if credentials else None
    if not token:
        # Also check the X-Appwrite-JWT header used by Appwrite Functions.
        token = request.headers.get("X-Appwrite-JWT")

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    settings = get_settings()
    if not settings.appwrite_project_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Appwrite project is not configured on the server",
        )

    endpoint = (settings.appwrite_endpoint or os.getenv("APPWRITE_ENDPOINT", "https://sgp.cloud.appwrite.io/v1")).rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{endpoint}/account",
                headers={
                    "X-Appwrite-Project": settings.appwrite_project_id,
                    "X-Appwrite-JWT": token,
                },
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as exc:
        logger.warning("JWT validation failed: %s", exc.response.text)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        ) from exc
    except httpx.RequestError as exc:
        logger.warning("Appwrite auth request failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Auth service unreachable",
        ) from exc
    except Exception as exc:
        logger.exception("Unexpected auth error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Auth validation failed",
        ) from exc


async def require_admin(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    """Require an authenticated user with an admin label."""
    labels = user.get("labels") or []
    admin_labels = {label.strip() for label in os.getenv("APPWRITE_ADMIN_LABELS", "admin").split(",") if label.strip()}
    if not any(label in admin_labels for label in labels):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return user
