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
Async HTTP client for OpenEMR's FHIR R4 API.

Handles:
  - OAuth2 client-credentials token lifecycle (fetch, cache, auto-refresh)
  - Retry with exponential backoff (1 s, 2 s, 4 s) for 429/5xx/network errors
  - Client-side concurrency cap via asyncio.Semaphore(10)
  - Bundle pagination (follows link[rel=next] up to MAX_PAGES pages)
  - TLS verification enforced (no skip_ssl_verify option)
  - HIPAA-safe logging: no PHI, patient IDs, or credentials in log messages
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_MAX_PAGES: int = 10          # Guard against runaway Bundle pagination
_RETRY_ATTEMPTS: int = 3      # Total attempts per request
_RETRY_DELAYS: list[float] = [1.0, 2.0, 4.0]   # Exponential backoff (seconds)
_REQUEST_TIMEOUT: float = 10.0                   # Per-request timeout (seconds)
_MAX_CONCURRENT: int = 10     # asyncio.Semaphore limit for FHIR requests
_TOKEN_EXPIRY_BUFFER: int = 30  # Refresh token this many seconds before expiry


# ---------------------------------------------------------------------------
# Token cache
# ---------------------------------------------------------------------------


class _TokenCache:
    """
    Thread-safe in-process cache for a single OAuth2 bearer token.

    Uses an asyncio.Lock to prevent concurrent token-fetch races ("thundering herd").
    """

    def __init__(self) -> None:
        self._token: Optional[str] = None
        self._expires_at: float = 0.0
        self.lock: asyncio.Lock = asyncio.Lock()

    @property
    def is_valid(self) -> bool:
        return (
            self._token is not None
            and time.monotonic() < self._expires_at - _TOKEN_EXPIRY_BUFFER
        )

    def store(self, token: str, expires_in: int) -> None:
        self._token = token
        self._expires_at = time.monotonic() + expires_in

    def get(self) -> Optional[str]:
        return self._token if self.is_valid else None

    def invalidate(self) -> None:
        self._token = None
        self._expires_at = 0.0


# ---------------------------------------------------------------------------
# FHIR client
# ---------------------------------------------------------------------------


class FHIRClient:
    """
    Async HTTP client for OpenEMR's FHIR R4 API.

    Usage
    -----
    client = FHIRClient(
        fhir_base_url="http://localhost:8080/fhir",
        client_id="my-client",
        client_secret="secret",
    )
    patient = await client.get("/Patient/123")
    meds     = await client.paginate("/MedicationRequest", {"patient": "123"})
    await client.close()
    """

    def __init__(
        self,
        fhir_base_url: str,
        client_id: str = "",
        client_secret: str = "",
    ) -> None:
        self._fhir_base_url = fhir_base_url.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._token_cache = _TokenCache()
        self._semaphore = asyncio.Semaphore(_MAX_CONCURRENT)
        # Single shared client — reused for connection pooling.
        # verify=True enforces TLS certificate validation (no way to disable it).
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(_REQUEST_TIMEOUT),
            verify=True,
            follow_redirects=True,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get(
        self,
        path: str,
        params: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """
        Authenticated GET to the FHIR API. Returns parsed JSON.

        For Bundle responses this returns only the first page; use
        ``paginate()`` when you need all pages aggregated.

        Args:
            path:   Path relative to the FHIR base URL (e.g. ``/Patient/123``).
            params: Optional query-string parameters.

        Returns:
            Parsed JSON dict.

        Raises:
            httpx.HTTPStatusError for non-recoverable HTTP errors.
        """
        headers = await self._auth_headers()
        url = f"{self._fhir_base_url}{path}"
        async with self._semaphore:
            return await self._get_with_retry(url, headers, params or {})

    async def paginate(
        self,
        path: str,
        params: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        """
        Follow FHIR Bundle pagination (``link[rel=next]``) and aggregate entries.

        Returns a flat list of resource dicts (the ``resource`` key from each
        Bundle entry).  Stops after ``_MAX_PAGES`` pages.

        Args:
            path:   Search path, e.g. ``/MedicationRequest``.
            params: Search parameters for the first page.

        Returns:
            Flat list of FHIR resource dicts from all pages.
        """
        all_resources: list[dict[str, Any]] = []
        page_count = 0
        url: Optional[str] = f"{self._fhir_base_url}{path}"
        query: dict[str, Any] = params or {}

        while url and page_count < _MAX_PAGES:
            headers = await self._auth_headers()
            async with self._semaphore:
                bundle = await self._get_with_retry(
                    url,
                    headers,
                    query if page_count == 0 else {},
                )

            for entry in bundle.get("entry") or []:
                if "resource" in entry:
                    all_resources.append(entry["resource"])

            url = self._next_url(bundle)
            page_count += 1

        if page_count >= _MAX_PAGES and self._next_url(
            {"link": [{"relation": "next", "url": url}]} if url else {}
        ):
            logger.warning(
                "FHIR pagination: stopped at max page limit (%d pages)", _MAX_PAGES
            )

        return all_resources

    async def close(self) -> None:
        """Close the underlying httpx.AsyncClient (call on application shutdown)."""
        await self._http.aclose()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _auth_headers(self) -> dict[str, str]:
        token = await self._get_token()
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/fhir+json",
        }

    async def _get_token(self) -> str:
        """Return a valid OAuth2 bearer token, fetching one if necessary."""
        if cached := self._token_cache.get():
            return cached
        async with self._token_cache.lock:
            # Double-check after acquiring lock (another coroutine may have refreshed)
            if cached := self._token_cache.get():
                return cached
            return await self._fetch_token()

    async def _fetch_token(self) -> str:
        """
        Execute the OAuth2 client-credentials grant against OpenEMR's token endpoint.

        OpenEMR's token endpoint is at ``/oauth2/default/token`` relative to the
        server root (one level above the FHIR base URL).
        """
        # Derive server root: strip /fhir or /fhir/R4 suffix
        oauth_base = self._fhir_base_url
        for suffix in ("/fhir/R4", "/fhir"):
            if oauth_base.endswith(suffix):
                oauth_base = oauth_base[: -len(suffix)]
                break

        token_url = f"{oauth_base}/oauth2/default/token"

        try:
            resp = await self._http.post(
                token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            resp.raise_for_status()
            body = resp.json()
            token: str = body["access_token"]
            expires_in: int = int(body.get("expires_in", 300))
            self._token_cache.store(token, expires_in)
            logger.info(
                "FHIR OAuth2 token obtained (expires_in=%ds)", expires_in
            )
            return token

        except httpx.HTTPStatusError as exc:
            # Log status code only — credentials are never logged
            logger.error(
                "OAuth2 token request failed: HTTP %d", exc.response.status_code
            )
            raise
        except Exception as exc:
            logger.error("OAuth2 token request error: %s", type(exc).__name__)
            raise

    async def _get_with_retry(
        self,
        url: str,
        headers: dict[str, str],
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Perform a GET with exponential backoff retry for transient errors.

        Retries on: 429, 500, 502, 503, 504, httpx.TimeoutException, httpx.ConnectError.
        Handles 401 (expired token) by refreshing the token once and retrying.
        Non-retryable errors (400, 403, 404, etc.) are raised immediately.
        """
        last_exc: Exception = RuntimeError("Retry loop did not execute")

        for attempt in range(1, _RETRY_ATTEMPTS + 1):
            delay = _RETRY_DELAYS[attempt - 1] if attempt <= len(_RETRY_DELAYS) else _RETRY_DELAYS[-1]

            try:
                resp = await self._http.get(url, headers=headers, params=params)

                if resp.status_code == 401:
                    # Token expired mid-flight — refresh once and retry
                    logger.warning("FHIR request: 401 Unauthorized, refreshing token")
                    self._token_cache.invalidate()
                    new_token = await self._fetch_token()
                    headers = {**headers, "Authorization": f"Bearer {new_token}"}
                    resp = await self._http.get(url, headers=headers, params=params)

                resp.raise_for_status()
                return resp.json()

            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                retryable = status in (429, 500, 502, 503, 504)
                if retryable and attempt < _RETRY_ATTEMPTS:
                    logger.warning(
                        "FHIR GET HTTP %d, retry %d/%d in %.1fs",
                        status, attempt, _RETRY_ATTEMPTS, delay,
                    )
                    await asyncio.sleep(delay)
                    last_exc = exc
                else:
                    logger.error("FHIR GET failed: HTTP %d", status)
                    raise

            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                if attempt < _RETRY_ATTEMPTS:
                    logger.warning(
                        "FHIR GET %s, retry %d/%d in %.1fs",
                        type(exc).__name__, attempt, _RETRY_ATTEMPTS, delay,
                    )
                    await asyncio.sleep(delay)
                    last_exc = exc
                else:
                    logger.error(
                        "FHIR GET failed after %d attempts: %s",
                        _RETRY_ATTEMPTS, type(exc).__name__,
                    )
                    raise

        raise last_exc  # Should only be reached on retryable errors exhausted

    @staticmethod
    def _next_url(bundle: dict[str, Any]) -> Optional[str]:
        """Extract the ``next`` page URL from a FHIR Bundle link array."""
        for link in bundle.get("link") or []:
            if link.get("relation") == "next":
                url = link.get("url")
                return str(url) if url else None
        return None
