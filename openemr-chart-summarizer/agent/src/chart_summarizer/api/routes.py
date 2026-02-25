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
FastAPI route definitions.  All routes are mounted under the /api/v1 prefix
(set in main.py via include_router prefix).

Endpoints:
  POST /api/v1/summarize                        — Generate a patient chart summary
  GET  /api/v1/summarize/{summary_id}           — Retrieve a cached summary
  GET  /api/v1/summarize/{summary_id}/citations — Retrieve citations for a summary
  POST /api/v1/summarize/{summary_id}/feedback  — Submit clinician feedback
  GET  /api/v1/conversations/{session_id}       — Retrieve a conversation session + turns
  GET  /api/v1/conversations?patient_id=X      — List conversation sessions for a patient
  GET  /api/v1/health                           — Service health check (no auth)
  GET  /api/v1/config                           — Non-sensitive configuration (auth)
"""

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from chart_summarizer.api.auth import UserIdentity, verify_api_key, verify_token
from chart_summarizer.api.middleware import write_audit_record
from chart_summarizer.api.rate_limiter import get_rate_limiter
from chart_summarizer.config import settings
from chart_summarizer.db.engine import get_db_session
from chart_summarizer.models.summary import Citation, SummaryRequest, SummaryResponse
from chart_summarizer.services.summary_service import SummaryService
from chart_summarizer.utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter()

# Module-level service instance (created once, reused across requests)
_summary_service: SummaryService | None = None
_START_TIME = time.monotonic()


def get_summary_service() -> SummaryService:
    """Dependency: return (or lazily create) the SummaryService singleton."""
    global _summary_service
    if _summary_service is None:
        _summary_service = SummaryService()
    return _summary_service


# ---------------------------------------------------------------------------
# Health check — no auth required
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    status: str
    version: str
    uptime_seconds: int
    llm_provider: str
    fhir_connected: bool


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Service health check",
    tags=["Operations"],
)
async def health_check() -> HealthResponse:
    """
    Returns service health status.

    No authentication required — used by Docker HEALTHCHECK, load balancers,
    and monitoring. Does NOT include any PHI or sensitive configuration.
    """
    return HealthResponse(
        status="ok",
        version="0.1.0",
        uptime_seconds=int(time.monotonic() - _START_TIME),
        llm_provider=settings.LLM_PROVIDER,
        fhir_connected=bool(settings.OPENEMR_FHIR_BASE_URL),
    )


# ---------------------------------------------------------------------------
# Non-sensitive config — auth required
# ---------------------------------------------------------------------------


class ConfigResponse(BaseModel):
    available_specialties: list[str]
    default_date_range_months: int
    available_llm_models: list[str]
    rate_limit_per_hour: int
    audit_log_enabled: bool
    hipaa_mode: bool


@router.get(
    "/config",
    response_model=ConfigResponse,
    summary="Runtime configuration (non-sensitive)",
    tags=["Operations"],
)
async def get_config(
    _user: UserIdentity = Depends(verify_token),
) -> ConfigResponse:
    """
    Return the current non-sensitive runtime configuration.

    Authentication required. API keys and secrets are never returned.
    """
    return ConfigResponse(
        available_specialties=[
            "primary_care",
            "cardiology",
            "psychiatry",
            "pediatrics",
            "neurology",
            "oncology",
            "endocrinology",
            "nephrology",
        ],
        default_date_range_months=settings.SUMMARY_DEFAULT_MONTHS,
        available_llm_models=[settings.LLM_MODEL],
        rate_limit_per_hour=settings.RATE_LIMIT_PER_HOUR,
        audit_log_enabled=settings.AUDIT_LOG_ENABLED,
        hipaa_mode=settings.HIPAA_MODE,
    )


# ---------------------------------------------------------------------------
# Summary generation
# ---------------------------------------------------------------------------


@router.post(
    "/summarize",
    summary="Generate a patient chart summary",
    tags=["Summarizer"],
)
async def create_summary(
    request: Request,
    body: SummaryRequest,
    service: SummaryService = Depends(get_summary_service),
    user: UserIdentity = Depends(verify_token),
    db: AsyncSession = Depends(get_db_session),
) -> JSONResponse:
    """
    Generate an AI-powered clinical summary for the specified patient.

    - Rate limit: RATE_LIMIT_PER_HOUR requests per user per rolling hour.
    - Timeout: 60 seconds — returns 504 if exceeded.
    - Returns 200 for complete, 206 for partial (some data sections unavailable).
    - Error responses never include PHI.
    - Successful summaries are cached in the database for retrieval.
    """
    request_id = getattr(request.state, "request_id", "unknown")
    user_id = user.user_id
    start_ts = time.monotonic()

    # --- Rate limiting (sliding window, per user) ---
    limiter = get_rate_limiter()
    allowed, retry_after = await limiter.check_and_increment(user_id)
    if not allowed:
        if settings.AUDIT_LOG_ENABLED:
            await write_audit_record(
                db,
                request_id=request_id,
                user_id=user_id,
                patient_id=body.patient_id,
                action="summarize",
                outcome="rate_limited",
                response_time_ms=0,
            )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Rate limit exceeded. You may submit up to "
                f"{settings.RATE_LIMIT_PER_HOUR} summaries per hour."
            ),
            headers={"Retry-After": str(retry_after)},
        )

    logger.info(
        "Summary requested | request_id=%s patient_pid=%s specialty=%s user_id=%s",
        request_id,
        body.patient_id,
        body.specialty,
        user_id,
    )

    # --- Generate with 60-second hard timeout ---
    try:
        response: SummaryResponse = await asyncio.wait_for(
            service.generate_summary(body, db),
            timeout=60.0,
        )
    except asyncio.TimeoutError:
        elapsed_ms = int((time.monotonic() - start_ts) * 1000)
        if settings.AUDIT_LOG_ENABLED:
            await write_audit_record(
                db,
                request_id=request_id,
                user_id=user_id,
                patient_id=body.patient_id,
                action="summarize",
                outcome="failure",
                response_time_ms=elapsed_ms,
            )
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Summary generation timed out after 60 seconds.",
        )
    except NotImplementedError:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Summary generation is not yet implemented.",
        )
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - start_ts) * 1000)
        logger.error("Summary generation failed: %s", type(exc).__name__)
        if settings.AUDIT_LOG_ENABLED:
            await write_audit_record(
                db,
                request_id=request_id,
                user_id=user_id,
                patient_id=body.patient_id,
                action="summarize",
                outcome="failure",
                response_time_ms=elapsed_ms,
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred during summary generation.",
        ) from exc

    elapsed_ms = int((time.monotonic() - start_ts) * 1000)
    outcome = "partial" if response.status == "partial" else "success"

    # --- Cache summary and write audit record ---
    if settings.AUDIT_LOG_ENABLED:
        summary_id = response.metadata.request_id
        expires_at = datetime.now(timezone.utc) + timedelta(hours=settings.SUMMARY_CACHE_TTL_HOURS)
        try:
            await db.execute(
                text(
                    "INSERT OR REPLACE INTO summary_cache "
                    "(id, patient_id, specialty, created_at, expires_at, "
                    " summary_json, confidence_score, created_by) "
                    "VALUES (:id, :pid, :spec, :created, :expires, "
                    "        :sjson, :score, :creator)"
                ),
                {
                    "id": summary_id,
                    "pid": body.patient_id,
                    "spec": body.specialty,
                    "created": datetime.now(timezone.utc),
                    "expires": expires_at,
                    "sjson": response.model_dump_json(),
                    "score": response.verification_result.confidence_score,
                    "creator": user_id,
                },
            )
            await db.commit()
        except Exception as cache_exc:
            logger.warning("Failed to cache summary: %s", type(cache_exc).__name__)

        token_count = (
            (response.metadata.input_tokens or 0)
            + (response.metadata.output_tokens or 0)
        )
        await write_audit_record(
            db,
            request_id=request_id,
            user_id=user_id,
            patient_id=body.patient_id,
            action="summarize",
            outcome=outcome,
            response_time_ms=elapsed_ms,
            llm_model=response.metadata.model_used,
            token_count=token_count,
        )

    http_status = (
        status.HTTP_206_PARTIAL_CONTENT
        if response.status == "partial"
        else status.HTTP_200_OK
    )
    return JSONResponse(content=response.model_dump(mode="json"), status_code=http_status)


# ---------------------------------------------------------------------------
# Retrieve a cached summary by ID
# ---------------------------------------------------------------------------


@router.get(
    "/summarize/{summary_id}",
    response_model=SummaryResponse,
    summary="Retrieve a previously generated summary",
    tags=["Summarizer"],
)
async def get_summary(
    request: Request,
    summary_id: str,
    user: UserIdentity = Depends(verify_token),
    db: AsyncSession = Depends(get_db_session),
) -> JSONResponse:
    """
    Retrieve a cached summary by its ID (= ``metadata.request_id``).

    Summaries are cached for SUMMARY_CACHE_TTL_HOURS hours. Returns 404 after
    expiry or if the ID was never generated.
    """
    request_id = getattr(request.state, "request_id", "unknown")
    user_id = user.user_id

    try:
        result = await db.execute(
            text(
                "SELECT summary_json, patient_id FROM summary_cache "
                "WHERE id = :sid AND expires_at > :now"
            ),
            {"sid": summary_id, "now": datetime.now(timezone.utc)},
        )
        row = result.fetchone()
    except Exception as exc:
        logger.error("Cache lookup failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve summary.",
        ) from exc

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Summary '{summary_id}' not found or has expired.",
        )

    if settings.AUDIT_LOG_ENABLED:
        await write_audit_record(
            db,
            request_id=request_id,
            user_id=user_id,
            patient_id=row.patient_id,
            action="view",
            outcome="success",
            response_time_ms=0,
        )

    return JSONResponse(content=json.loads(row.summary_json))


# ---------------------------------------------------------------------------
# Retrieve citations for a cached summary
# ---------------------------------------------------------------------------


class CitationsResponse(BaseModel):
    summary_id: str
    citations: list[Citation]


@router.get(
    "/summarize/{summary_id}/citations",
    response_model=CitationsResponse,
    summary="Retrieve detailed citations for a summary",
    tags=["Summarizer"],
)
async def get_summary_citations(
    summary_id: str,
    user: UserIdentity = Depends(verify_token),
    db: AsyncSession = Depends(get_db_session),
) -> CitationsResponse:
    """
    Return the citation list for a previously generated summary.

    Each citation maps a ``[Source: <id>]`` tag in the summary text to the
    underlying FHIR resource, enabling clinicians to trace every claim back
    to the original medical record.
    """
    try:
        result = await db.execute(
            text(
                "SELECT summary_json FROM summary_cache "
                "WHERE id = :sid AND expires_at > :now"
            ),
            {"sid": summary_id, "now": datetime.now(timezone.utc)},
        )
        row = result.fetchone()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve citations.",
        ) from exc

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Summary '{summary_id}' not found or has expired.",
        )

    summary_data = json.loads(row.summary_json)
    citations = summary_data.get("citations", [])
    return CitationsResponse(summary_id=summary_id, citations=citations)


# ---------------------------------------------------------------------------
# Clinician feedback
# ---------------------------------------------------------------------------


class FeedbackRequest(BaseModel):
    action: Literal["approved", "edited", "rejected"]
    edits: Optional[str] = None
    notes: Optional[str] = None


class FeedbackResponse(BaseModel):
    summary_id: str
    recorded: bool


@router.post(
    "/summarize/{summary_id}/feedback",
    response_model=FeedbackResponse,
    status_code=status.HTTP_200_OK,
    summary="Submit clinician feedback on a summary",
    tags=["Summarizer"],
)
async def submit_feedback(
    request: Request,
    summary_id: str,
    feedback: FeedbackRequest,
    user: UserIdentity = Depends(verify_token),
    db: AsyncSession = Depends(get_db_session),
) -> FeedbackResponse:
    """
    Record clinician feedback for evaluation and continuous improvement.

    - **approved**: Summary accepted as-is before clinical use.
    - **edited**: Clinician corrected inaccuracies (edits field should describe what).
    - **rejected**: Summary was too inaccurate to be useful.

    Feedback is stored in the audit log and used for the eval improvement cycle.
    The ``edits`` and ``notes`` fields must never contain PHI.
    """
    request_id = getattr(request.state, "request_id", "unknown")
    user_id = user.user_id

    try:
        result = await db.execute(
            text("SELECT patient_id FROM summary_cache WHERE id = :sid"),
            {"sid": summary_id},
        )
        row = result.fetchone()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to look up summary.",
        ) from exc

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Summary '{summary_id}' not found.",
        )

    patient_id = row.patient_id

    if settings.AUDIT_LOG_ENABLED:
        await write_audit_record(
            db,
            request_id=request_id,
            user_id=user_id,
            patient_id=patient_id,
            action=f"feedback:{feedback.action}",
            outcome="success",
            response_time_ms=0,
        )

    logger.info(
        "Clinician feedback | summary_id=%s action=%s user_id=%s",
        summary_id,
        feedback.action,
        user_id,
    )

    return FeedbackResponse(summary_id=summary_id, recorded=True)

# ---------------------------------------------------------------------------
# Conversation history
# ---------------------------------------------------------------------------


class ConversationTurnResponse(BaseModel):
    turn_number: int
    summary_id: Optional[str]
    confidence_level: str
    created_at: str


class ConversationSessionResponse(BaseModel):
    session_id: str
    patient_id: str
    specialty: str
    created_at: str
    expires_at: str
    turn_count: int
    turns: list[ConversationTurnResponse]


class ConversationListItem(BaseModel):
    session_id: str
    specialty: str
    created_at: str
    expires_at: str
    turn_count: int
    is_expired: bool


@router.get(
    "/conversations/{session_id}",
    response_model=ConversationSessionResponse,
    summary="Retrieve a conversation session and its turns",
    tags=["Conversations"],
)
async def get_conversation(
    session_id: str,
    user: UserIdentity = Depends(verify_token),
    db: AsyncSession = Depends(get_db_session),
) -> ConversationSessionResponse:
    """
    Retrieve a conversation session and all its completed turns.

    Returns the session metadata and a list of turns in chronological order.
    Each turn references the summary_id that can be retrieved via
    GET /api/v1/summarize/{summary_id}.
    """
    result = await db.execute(
        text(
            "SELECT id, patient_id, specialty, created_at, expires_at, turn_count "
            "FROM conversation_sessions WHERE id = :sid"
        ),
        {"sid": session_id},
    )
    session = result.fetchone()
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{session_id}' not found.",
        )

    turns_result = await db.execute(
        text(
            "SELECT turn_number, summary_id, confidence_level, created_at "
            "FROM conversation_turns WHERE session_id = :sid ORDER BY turn_number ASC"
        ),
        {"sid": session_id},
    )
    turns = [
        ConversationTurnResponse(
            turn_number=r.turn_number,
            summary_id=r.summary_id,
            confidence_level=r.confidence_level,
            created_at=str(r.created_at),
        )
        for r in turns_result.fetchall()
    ]
    return ConversationSessionResponse(
        session_id=session.id,
        patient_id=session.patient_id,
        specialty=session.specialty,
        created_at=str(session.created_at),
        expires_at=str(session.expires_at),
        turn_count=session.turn_count,
        turns=turns,
    )


@router.get(
    "/conversations",
    response_model=list[ConversationListItem],
    summary="List conversation sessions for a patient",
    tags=["Conversations"],
)
async def list_conversations(
    patient_id: str,
    user: UserIdentity = Depends(verify_token),
    db: AsyncSession = Depends(get_db_session),
) -> list[ConversationListItem]:
    """
    List all conversation sessions for a given patient PID, newest first.

    Use this to resume an existing session by passing the returned
    session_id in the next POST /summarize request.

    Returns the 20 most recent sessions. Expired sessions are included
    with ``is_expired: true`` so the UI can display history.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    result = await db.execute(
        text(
            "SELECT id, specialty, created_at, expires_at, turn_count "
            "FROM conversation_sessions "
            "WHERE patient_id = :pid ORDER BY created_at DESC LIMIT 20"
        ),
        {"pid": patient_id},
    )
    return [
        ConversationListItem(
            session_id=r.id,
            specialty=r.specialty,
            created_at=str(r.created_at),
            expires_at=str(r.expires_at),
            turn_count=r.turn_count,
            is_expired=str(r.expires_at) < now_iso,
        )
        for r in result.fetchall()
    ]
