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

Strategy: shared Bearer token between the OpenEMR PHP module and the Python
agent.  The PHP module sends ``Authorization: Bearer <key>`` on every request
to ``POST /summarize``; this module validates the header using a
constant-time comparison to prevent timing attacks.

Key management:
  - Set ``AGENT_API_KEY`` in the environment (or .env file).
  - The PHP module reads the same value from ``CHART_SUMMARIZER_API_KEY`` env var.
  - When ``AGENT_API_KEY`` is empty the dependency is a no-op, which allows
    local development without any secret configuration.  **Never leave it
    empty in production.**
"""

import secrets

from fastapi import HTTPException, Request, status

from chart_summarizer.config import settings

_BEARER_PREFIX = "Bearer "


async def verify_api_key(request: Request) -> None:
    """
    FastAPI dependency: validate the ``Authorization: Bearer <key>`` header.

    Behaviour:
    - If ``AGENT_API_KEY`` is not configured (empty string), authentication is
      skipped entirely.  This makes local development and CI painless without
      any secrets configuration.
    - If the header is missing or does not start with ``Bearer ``, respond 401.
    - If the provided key does not match the configured key (constant-time
      compare), respond 401.

    Usage::

        @router.post("/summarize")
        async def create_summary(
            request: SummaryRequest,
            _auth: None = Depends(verify_api_key),
        ) -> SummaryResponse:
            ...

    Raises:
        HTTPException(401): When authentication is required and fails.
    """
    configured_key = settings.AGENT_API_KEY.get_secret_value()

    # Auth disabled in dev/CI — skip all checks
    if not configured_key:
        return

    auth_header = request.headers.get("Authorization", "")

    if not auth_header.startswith(_BEARER_PREFIX):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Authorization header. Expected: Bearer <key>",
            headers={"WWW-Authenticate": "Bearer"},
        )

    provided_key = auth_header[len(_BEARER_PREFIX):]

    # secrets.compare_digest prevents timing attacks
    if not secrets.compare_digest(provided_key, configured_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key.",
            headers={"WWW-Authenticate": "Bearer"},
        )
