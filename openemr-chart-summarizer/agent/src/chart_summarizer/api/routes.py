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
FastAPI route definitions.

Endpoints:
  POST /summarize   — Generate a patient chart summary
  GET  /health      — Service health check (used by Docker and load balancers)
  GET  /config      — Return non-sensitive runtime configuration
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from chart_summarizer.api.auth import verify_api_key
from chart_summarizer.config import settings
from chart_summarizer.models.summary import SummaryRequest, SummaryResponse
from chart_summarizer.services.summary_service import SummaryService
from chart_summarizer.utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter()

# Module-level service instance (created once, reused across requests)
_summary_service: SummaryService | None = None


def get_summary_service() -> SummaryService:
    """Dependency: return (or lazily create) the SummaryService singleton."""
    global _summary_service
    if _summary_service is None:
        _summary_service = SummaryService()
    return _summary_service


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    """Response schema for GET /health."""

    status: str
    version: str
    llm_provider: str
    hipaa_mode: bool


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Service health check",
    tags=["Operations"],
)
async def health_check() -> HealthResponse:
    """
    Returns service health status.

    Used by Docker HEALTHCHECK, load balancers, and monitoring.
    Does NOT include any PHI or sensitive configuration.
    """
    return HealthResponse(
        status="ok",
        version="0.1.0",
        llm_provider=settings.LLM_PROVIDER,
        hipaa_mode=settings.HIPAA_MODE,
    )


# ---------------------------------------------------------------------------
# Non-sensitive config
# ---------------------------------------------------------------------------


class ConfigResponse(BaseModel):
    """Response schema for GET /config — non-sensitive settings only."""

    llm_provider: str
    llm_model: str
    summary_default_months: int
    max_encounters_per_summary: int
    audit_log_enabled: bool
    hipaa_mode: bool


@router.get(
    "/config",
    response_model=ConfigResponse,
    summary="Runtime configuration (non-sensitive)",
    tags=["Operations"],
)
async def get_config() -> ConfigResponse:
    """
    Return the current non-sensitive runtime configuration.

    API keys, client secrets, and other sensitive values are never returned.
    """
    return ConfigResponse(
        llm_provider=settings.LLM_PROVIDER,
        llm_model=settings.LLM_MODEL,
        summary_default_months=settings.SUMMARY_DEFAULT_MONTHS,
        max_encounters_per_summary=settings.MAX_ENCOUNTERS_PER_SUMMARY,
        audit_log_enabled=settings.AUDIT_LOG_ENABLED,
        hipaa_mode=settings.HIPAA_MODE,
    )


# ---------------------------------------------------------------------------
# Summary generation
# ---------------------------------------------------------------------------


@router.post(
    "/summarize",
    response_model=SummaryResponse,
    status_code=status.HTTP_200_OK,
    summary="Generate a patient chart summary",
    tags=["Summarizer"],
)
async def create_summary(
    request: SummaryRequest,
    service: SummaryService = Depends(get_summary_service),
    _auth: None = Depends(verify_api_key),
) -> SummaryResponse:
    """
    Generate an AI-powered clinical summary for the specified patient.

    The summary is always returned as a DRAFT that requires clinician review.
    It is never auto-inserted into the medical record.

    - **patient_id**: OpenEMR internal PID (required)
    - **specialty**: Provider specialty context for tailored output
    - **date_range**: Optional data window (defaults to last 12 months)
    - **requested_sections**: Data categories to include

    Requires ``Authorization: Bearer <AGENT_API_KEY>`` when ``AGENT_API_KEY``
    is configured (always required in production).
    """
    logger.info(
        "Summary requested for patient_pid=%s specialty=%s",
        request.patient_id,
        request.specialty,
    )

    try:
        response = await service.generate_summary(request)
    except NotImplementedError:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Summary generation is not yet implemented.",
        )
    except Exception as exc:
        logger.error("Summary generation failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred during summary generation.",
        ) from exc

    return response
