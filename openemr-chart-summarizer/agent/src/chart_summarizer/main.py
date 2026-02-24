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
FastAPI application entry point.

Creates and configures the FastAPI app instance with:
  - CORS middleware (allows requests from OpenEMR's origin)
  - Request logging and HIPAA audit middleware
  - API routes mounted under /api/v1
  - Database initialisation on startup (audit log + summary cache tables)
"""

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from chart_summarizer.api.middleware import AuditMiddleware, RequestLoggingMiddleware
from chart_summarizer.api.routes import router
from chart_summarizer.config import settings
from chart_summarizer.db.engine import init_db
from chart_summarizer.utils.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Lifespan (startup / shutdown)
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Application lifespan handler.

    Startup: initialise the database, validate configuration.
    Shutdown: flush buffers, close connections.
    """
    logger.info(
        "Chart Summarizer Agent starting | provider=%s model=%s hipaa_mode=%s",
        settings.LLM_PROVIDER,
        settings.LLM_MODEL,
        settings.HIPAA_MODE,
    )

    # Create audit_log and summary_cache tables if they do not exist.
    try:
        await init_db()
    except Exception as exc:
        logger.error("Database initialisation failed: %s", type(exc).__name__)
        # Non-fatal — the app starts but audit logging may not work.

    yield

    logger.info("Chart Summarizer Agent shutting down.")


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------


def create_app() -> FastAPI:
    """
    Construct and configure the FastAPI application.

    Returns:
        Configured FastAPI instance ready to serve requests.
    """
    app = FastAPI(
        title="OpenEMR Chart Summarizer Agent",
        description=(
            "AI-powered patient chart summarization for OpenEMR. "
            "Generates structured clinical summaries from FHIR R4 patient data. "
            "All summaries are DRAFTS requiring clinician review."
        ),
        version="0.1.0",
        license_info={
            "name": "GNU General Public License v3.0",
            "url": "https://www.gnu.org/licenses/gpl-3.0.html",
        },
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # ------------------------------------------------------------------
    # CORS — allow the OpenEMR PHP module to call this service.
    # CORS_ORIGINS env var: comma-separated list or "*" for open demo.
    # Note: allow_credentials must be False when origins contains "*".
    # ------------------------------------------------------------------
    cors_origins = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]
    allow_credentials = "*" not in cors_origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=allow_credentials,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    )

    # ------------------------------------------------------------------
    # Custom middleware (applied last-registered = outermost)
    # ------------------------------------------------------------------
    app.add_middleware(AuditMiddleware)
    app.add_middleware(RequestLoggingMiddleware)

    # ------------------------------------------------------------------
    # Routes — all mounted under /api/v1
    # ------------------------------------------------------------------
    app.include_router(router, prefix="/api/v1")

    return app


# ---------------------------------------------------------------------------
# Module-level app instance (used by uvicorn: chart_summarizer.main:app)
# ---------------------------------------------------------------------------

app = create_app()