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
FastAPI middleware: request logging and HIPAA audit trail.

Middleware stack (applied in order, innermost first):
  1. RequestLoggingMiddleware — logs request/response metadata (no PHI in URLs).
  2. AuditMiddleware           — writes to the HIPAA audit log for /summarize calls.

Authentication is handled at the route level via FastAPI dependencies.
"""

import time
import uuid
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from chart_summarizer.config import settings
from chart_summarizer.utils.logging import get_logger

logger = get_logger(__name__)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """
    Log every incoming request and its response status/latency.

    HIPAA note: URL paths are logged but must never contain PHI.
    Patient IDs are only accepted in the request body (POST /summarize),
    never as URL path segments or query parameters.
    """

    def __init__(self, app: ASGIApp) -> None:
        """Initialise the middleware with the ASGI app."""
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Process the request, log metadata, and forward to the next handler."""
        request_id = str(uuid.uuid4())
        start = time.monotonic()

        # Attach request_id so downstream handlers can include it in logs
        request.state.request_id = request_id

        logger.info(
            "REQUEST | id=%s method=%s path=%s",
            request_id,
            request.method,
            request.url.path,
        )

        response = await call_next(request)

        latency_ms = int((time.monotonic() - start) * 1000)
        logger.info(
            "RESPONSE | id=%s status=%d latency_ms=%d",
            request_id,
            response.status_code,
            latency_ms,
        )

        # Attach request ID to response headers for tracing
        response.headers["X-Request-ID"] = request_id
        return response


class AuditMiddleware(BaseHTTPMiddleware):
    """
    Write HIPAA audit log entries for all /summarize requests.

    Captures: request_id, timestamp, HTTP status, and latency.
    The route handler is responsible for logging patient_pid and model details,
    since those are only available after the request body is parsed.

    TODO:
        - Optionally forward audit events to CloudWatch Logs or an audit database.
    """

    def __init__(self, app: ASGIApp) -> None:
        """Initialise the audit middleware."""
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Pass through non-summarize requests; write audit for /summarize."""
        if not settings.AUDIT_LOG_ENABLED:
            return await call_next(request)

        if request.url.path != "/summarize":
            return await call_next(request)

        start = time.monotonic()
        response = await call_next(request)
        latency_ms = int((time.monotonic() - start) * 1000)

        request_id = getattr(request.state, "request_id", "unknown")
        logger.info(
            "AUDIT | request_id=%s path=%s status=%d latency_ms=%d",
            request_id,
            request.url.path,
            response.status_code,
            latency_ms,
        )

        return response
