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
  2. AuditMiddleware           — writes HTTP-level audit for patient-data paths.

Patient-level detail (patient_id, LLM model, token counts) is written by the
route handlers via ``write_audit_record()``, since the request body is only
available after parsing.

HIPAA rules enforced here:
  - URL paths are logged but must never contain PHI.
  - Patient IDs are only accepted in request bodies (POST), never in URLs.
  - Audit log entries are append-only (INSERT only — no UPDATE/DELETE).
"""

import time
import uuid
from datetime import datetime
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from chart_summarizer.config import settings
from chart_summarizer.utils.logging import get_logger

logger = get_logger(__name__)

# Paths that involve patient data and must generate audit records.
_AUDITED_BASE_PATHS = ("/summarize",)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Attach security headers to every response (Helmet-style).

    Headers set:
      - X-Content-Type-Options: nosniff        (prevent MIME-sniffing)
      - X-Frame-Options: DENY                  (block iframe embedding)
      - X-XSS-Protection: 1; mode=block        (legacy XSS filter)
      - Strict-Transport-Security              (enforce HTTPS, 1 year)
      - Content-Security-Policy                (restrict resource origins)
      - Referrer-Policy                        (limit referrer leakage)
      - Permissions-Policy                     (disable unused browser features)
      - Server header removed                  (hide runtime fingerprint)
    """

    # CSP appropriate for a JSON API service — the agent renders no HTML.
    _CSP = "default-src 'none'; frame-ancestors 'none'; form-action 'none'"

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )
        response.headers["Content-Security-Policy"] = self._CSP
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "geolocation=(), microphone=(), camera=()"
        )
        # Remove the Server header to avoid advertising the runtime version.
        response.headers.pop("server", None)
        return response


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """
    Log every incoming request and its response status/latency.

    HIPAA note: URL paths are logged but must never contain PHI.
    Patient IDs are only accepted in the request body (POST /summarize),
    never as URL path segments or query parameters.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        request_id = str(uuid.uuid4())
        start = time.monotonic()

        request.state.request_id = request_id
        if not hasattr(request.state, "user"):
            request.state.user = None

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

        response.headers["X-Request-ID"] = request_id
        return response


class AuditMiddleware(BaseHTTPMiddleware):
    """
    Write HTTP-level audit log entries for all paths that involve patient data.

    Records request_id, user_id, HTTP path, status code, and latency.
    Patient-level detail (patient_id, LLM model, token counts) is written by
    the route handler via ``write_audit_record()``.

    When AUDIT_LOG_ENABLED is False (e.g. in tests), the middleware is a no-op.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if not settings.AUDIT_LOG_ENABLED:
            return await call_next(request)

        # Strip the /api/v1 prefix for matching.
        path = request.url.path
        base_path = path[len("/api/v1"):] if path.startswith("/api/v1") else path

        audited = any(
            base_path == p or base_path.startswith(p + "/")
            for p in _AUDITED_BASE_PATHS
        )
        if not audited:
            return await call_next(request)

        start = time.monotonic()
        response = await call_next(request)
        latency_ms = int((time.monotonic() - start) * 1000)

        request_id = getattr(request.state, "request_id", "unknown")
        user = getattr(request.state, "user", None)
        user_id = user.user_id if user else "unauthenticated"

        logger.info(
            "AUDIT | request_id=%s user_id=%s path=%s status=%d latency_ms=%d",
            request_id,
            user_id,
            path,
            response.status_code,
            latency_ms,
        )

        return response


async def write_audit_record(
    db,
    *,
    request_id: str,
    user_id: str,
    patient_id: str,
    action: str,
    outcome: str,
    response_time_ms: int,
    llm_model: str | None = None,
    token_count: int = 0,
    cost_estimate: float | None = None,
) -> None:
    """
    Write a complete HIPAA audit record to the database.

    Must be called from the route handler (where patient_id is available after
    body parsing). Performs an INSERT only — never UPDATE or DELETE.

    HIPAA compliance:
      - ``patient_id`` stores the OpenEMR PID only — NOT a patient name/DOB.
      - This function must never be passed PHI (names, DOBs, SSNs, etc.).
      - Audit failures are logged but never raise — they must not break
        clinical workflows.

    Args:
        db:               Active async SQLAlchemy session.
        request_id:       UUID from ``request.state.request_id``.
        user_id:          Authenticated user ID from the OAuth2 token.
        patient_id:       OpenEMR patient PID (integer-as-string).
        action:           ``summarize`` | ``view`` | ``feedback``.
        outcome:          ``success`` | ``partial`` | ``failure`` | ``rate_limited``.
        response_time_ms: End-to-end request latency in milliseconds.
        llm_model:        LLM model identifier used for this request.
        token_count:      Total tokens consumed (input + output).
        cost_estimate:    Estimated USD cost, if calculable.
    """
    from sqlalchemy import text

    try:
        await db.execute(
            text(
                """
                INSERT INTO audit_log
                    (timestamp, request_id, user_id, patient_id, action, outcome,
                     response_time_ms, llm_model, token_count, cost_estimate)
                VALUES
                    (:ts, :rid, :uid, :pid, :action, :outcome,
                     :rt, :model, :tokens, :cost)
                """
            ),
            {
                "ts": datetime.utcnow(),
                "rid": request_id,
                "uid": user_id,
                "pid": patient_id,
                "action": action,
                "outcome": outcome,
                "rt": response_time_ms,
                "model": llm_model,
                "tokens": token_count,
                "cost": cost_estimate,
            },
        )
        await db.commit()
    except Exception as exc:
        # Audit failures must never interrupt the clinical workflow.
        logger.error(
            "AUDIT_WRITE_FAILED | request_id=%s error=%s",
            request_id,
            type(exc).__name__,
        )