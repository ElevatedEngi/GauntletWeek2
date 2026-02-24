# Copyright (C) 2026 OpenEMR Community
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

"""
FastAPI authentication dependency.

Authentication flow:
  1. Extract ``Authorization: Bearer <token>`` header.
  2. Check the 5-minute in-memory token cache.
  3. If AGENT_API_KEY is configured and the token matches, accept it as an
     internal service-to-service call (shared secret, no introspection needed).
  4. If OPENEMR_OAUTH2_INTROSPECT_URL is configured, validate the token via
     OpenEMR's OAuth2 introspection endpoint (RFC 7662).
  5. Cache the resulting UserIdentity for 5 minutes.
  6. Attach the identity to ``request.state.user`` for downstream use.

When neither AGENT_API_KEY nor OPENEMR_OAUTH2_INTROSPECT_URL is configured,
auth is skipped entirely (dev / CI mode). NEVER deploy to production without
configuring at least one of these.

Backward-compatible alias ``verify_api_key`` is preserved so existing route
definitions that return ``None`` continue to work.
"""

import asyncio
import secrets
import time
from dataclasses import dataclass, field

import httpx
from fastapi import HTTPException, Request, status

from chart_summarizer.config import settings
from chart_summarizer.utils.logging import get_logger

logger = get_logger(__name__)

_BEARER_PREFIX = "Bearer "
_TOKEN_CACHE_TTL = 300  # 5 minutes


@dataclass
class UserIdentity:
    """Authenticated user identity extracted from a validated token."""

    user_id: str
    username: str
    role: str = field(default="user")


class _TokenCache:
    """
    Thread-safe in-memory cache for validated token → UserIdentity mappings.

    Reduces calls to the OpenEMR OAuth2 introspection endpoint from once-per-
    request to once per 5-minute window.
    """

    def __init__(self, ttl_seconds: int = _TOKEN_CACHE_TTL) -> None:
        self._data: dict[str, tuple[UserIdentity, float]] = {}
        self._ttl = ttl_seconds
        self._lock = asyncio.Lock()

    async def get(self, token: str) -> UserIdentity | None:
        async with self._lock:
            entry = self._data.get(token)
            if entry is None:
                return None
            identity, expires_at = entry
            if time.monotonic() > expires_at:
                del self._data[token]
                return None
            return identity

    async def set(self, token: str, identity: UserIdentity) -> None:
        async with self._lock:
            self._data[token] = (identity, time.monotonic() + self._ttl)

    def clear(self) -> None:
        """Clear all cached tokens — used in tests."""
        self._data.clear()


# Module-level cache shared across all requests.
_cache = _TokenCache()


def _extract_token(request: Request) -> str:
    """Pull and return the raw token string from the Authorization header."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith(_BEARER_PREFIX):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Authorization header. Expected: Bearer <token>",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = auth_header[len(_BEARER_PREFIX):]
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer token is empty.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token


async def verify_token(request: Request) -> UserIdentity:
    """
    FastAPI dependency: validate the Bearer token and return a UserIdentity.

    Sets ``request.state.user`` as a side-effect so middleware can access
    the identity without re-parsing the header.

    Raises:
        HTTPException(401): Token is missing, malformed, expired, or invalid.
        HTTPException(503): OpenEMR OAuth2 introspection endpoint unreachable.
    """
    configured_key = settings.AGENT_API_KEY.get_secret_value()
    introspect_url = settings.OPENEMR_OAUTH2_INTROSPECT_URL

    # Dev/CI mode — skip all auth when nothing is configured.
    if not configured_key and not introspect_url:
        identity = UserIdentity(user_id="dev", username="dev", role="admin")
        request.state.user = identity
        return identity

    token = _extract_token(request)

    # 1. Check cache first (avoids repeated introspection calls).
    cached = await _cache.get(token)
    if cached:
        request.state.user = cached
        return cached

    # 2. Shared API key — service-to-service auth (no introspection needed).
    if configured_key and secrets.compare_digest(token, configured_key):
        identity = UserIdentity(user_id="service", username="service", role="service")
        await _cache.set(token, identity)
        request.state.user = identity
        return identity

    # 3. OAuth2 token introspection (RFC 7662).
    if not introspect_url:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                introspect_url,
                data={"token": token},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
    except httpx.RequestError as exc:
        logger.error("OAuth2 introspection request failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to reach the OpenEMR OAuth2 introspection endpoint.",
        ) from exc

    if resp.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token introspection failed.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    data = resp.json()
    if not data.get("active"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token is expired or has been revoked.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    identity = UserIdentity(
        user_id=str(data.get("sub") or data.get("username") or "unknown"),
        username=str(data.get("username") or data.get("sub") or "unknown"),
        role=str(data.get("role") or "user"),
    )
    await _cache.set(token, identity)
    request.state.user = identity
    logger.info("OAuth2 token validated | user_id=%s role=%s", identity.user_id, identity.role)
    return identity


async def verify_api_key(request: Request) -> None:
    """
    Backward-compatible alias for ``verify_token`` that returns None.

    Existing routes that use ``Depends(verify_api_key)`` continue to work.
    The UserIdentity is attached to ``request.state.user`` as a side-effect.
    """
    await verify_token(request)