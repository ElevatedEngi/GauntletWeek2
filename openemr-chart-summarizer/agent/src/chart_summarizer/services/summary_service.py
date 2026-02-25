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
SummaryService — high-level orchestrator for the chart summary pipeline.

This service is the single entry point for the FastAPI layer.
It validates a SummaryRequest, invokes the LangGraph pipeline, and returns
the assembled SummaryResponse from format_output_node.
"""

import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from chart_summarizer.config import settings
from chart_summarizer.graph.pipeline import create_pipeline
from chart_summarizer.graph.state import SummarizerState
from chart_summarizer.models.summary import (
    SummaryMetadata,
    SummaryRequest,
    SummaryResponse,
    VerificationResult,
)
from chart_summarizer.utils.logging import get_logger

logger = get_logger(__name__)


class SummaryService:
    """
    Orchestrates the end-to-end chart summary generation pipeline.

    Responsibilities:
    - Validate and enrich the incoming SummaryRequest.
    - Resolve or create a conversation session (multi-turn history support).
    - Build the initial SummarizerState, including any prior turn context.
    - Invoke the compiled LangGraph pipeline.
    - Return the SummaryResponse assembled by format_output_node.
    - Persist the completed turn to the conversation history.
    - Write a HIPAA-compliant audit log entry.
    """

    def __init__(self, pipeline: Optional[Any] = None) -> None:
        """
        Compile the LangGraph pipeline once at service startup.

        Args:
            pipeline: Optional pre-built pipeline (used for testing).
                      Defaults to create_pipeline() which uses mock tools.
        """
        self._pipeline = pipeline if pipeline is not None else create_pipeline()

    async def generate_summary(
        self, request: SummaryRequest, db: AsyncSession
    ) -> SummaryResponse:
        """
        Generate a patient chart summary for the given request.

        Steps:
        1. Resolve or create a conversation session.
        2. Load prior turn history for the session (capped at 5 turns).
        3. Resolve date range (explicit DateRange or SUMMARY_DEFAULT_MONTHS).
        4. Build initial SummarizerState (with conversation history).
        5. Invoke the LangGraph pipeline asynchronously.
        6. Return the SummaryResponse from format_output_node.
        7. Persist the completed turn to conversation history.
        8. Write HIPAA audit log (always, even on failure).

        Args:
            request: Validated SummaryRequest from the API layer.
            db:      Active async SQLAlchemy session (request-scoped).

        Returns:
            SummaryResponse containing the generated summary and metadata.
        """
        request_id = str(uuid.uuid4())
        start_time = time.time()
        audit_status = "complete"
        model_used = settings.LLM_MODEL

        # Resolve conversation session and load prior history
        user_id = request.requesting_provider_id or "unknown"
        session_id = await self._get_or_create_session(db, request, user_id)
        conversation_history = await self._load_conversation_history(db, session_id)

        try:
            initial_state = self._build_initial_state(
                request, request_id, session_id, conversation_history
            )
            final_state: SummarizerState = await self._pipeline.ainvoke(initial_state)

            final_summary: Optional[SummaryResponse] = final_state.get("final_summary")

            if final_summary is not None:
                model_used = final_summary.metadata.model_used or settings.LLM_MODEL
                audit_status = final_summary.status
                # Attach session_id to response so client can continue the conversation
                final_summary.session_id = session_id
                final_summary.metadata.session_id = session_id
                # Persist the completed turn (only on non-failure)
                if final_summary.status != "failed":
                    await self._save_conversation_turn(
                        db, session_id, request, final_summary
                    )
                return final_summary

            # Fallback if format_output_node produced nothing (catastrophic failure)
            logger.error("format_output_node produced no final_summary; building fallback")
            audit_status = "failed"
            return self._build_fallback_response(
                final_state=final_state,
                request_id=request_id,
                start_time=start_time,
                patient_id=request.patient_id,
                specialty=request.specialty,
                session_id=session_id,
            )

        except Exception:
            audit_status = "failed"
            raise

        finally:
            latency_ms = int((time.time() - start_time) * 1000)
            self._write_audit_log(
                request_id=request_id,
                patient_id=request.patient_id,
                provider_id=request.requesting_provider_id,
                model_used=model_used,
                status=audit_status,
                latency_ms=latency_ms,
            )

    # ------------------------------------------------------------------
    # Session lifecycle helpers
    # ------------------------------------------------------------------

    async def _get_or_create_session(
        self,
        db: AsyncSession,
        request: SummaryRequest,
        user_id: str,
    ) -> str:
        """
        Return an existing active session_id or INSERT a new one.

        If request.session_id is provided and valid (not expired, same patient),
        the existing session is returned. Otherwise a new session is created
        silently — the caller never receives an error for a stale/wrong session.

        Returns:
            UUID string for the active conversation session.
        """
        now = datetime.now(timezone.utc)

        if request.session_id:
            row = await db.execute(
                text(
                    "SELECT id FROM conversation_sessions "
                    "WHERE id = :sid AND patient_id = :pid AND expires_at > :now"
                ),
                {
                    "sid": request.session_id,
                    "pid": request.patient_id,
                    "now": now.isoformat(),
                },
            )
            existing = row.fetchone()
            if existing:
                return request.session_id
            logger.warning(
                "session_id=%s is expired or does not match patient; creating new session",
                request.session_id,
            )

        session_id = str(uuid.uuid4())
        expires_at = now + timedelta(hours=settings.CONVERSATION_SESSION_TTL_HOURS)
        await db.execute(
            text(
                "INSERT INTO conversation_sessions "
                "(id, patient_id, specialty, created_at, expires_at, created_by, turn_count) "
                "VALUES (:id, :pid, :spec, :created, :expires, :creator, 0)"
            ),
            {
                "id": session_id,
                "pid": request.patient_id,
                "spec": request.specialty,
                "created": now.isoformat(),
                "expires": expires_at.isoformat(),
                "creator": user_id,
            },
        )
        await db.commit()
        return session_id

    async def _load_conversation_history(
        self,
        db: AsyncSession,
        session_id: str,
        max_turns: int = 5,
    ) -> list[dict]:
        """
        Load the last max_turns from the session as LLM message dicts.

        Returns a chronologically-ordered list of
        {"role": "user"|"assistant", "content": str} pairs ready for
        injection into the LLM messages list.

        History is capped at max_turns to keep context size bounded.
        At ~1,500 tokens per turn, 5 turns adds ~7,500 tokens — well
        within the 80K token context limit.
        """
        result = await db.execute(
            text(
                "SELECT request_json, summary_text FROM conversation_turns "
                "WHERE session_id = :sid "
                "ORDER BY turn_number DESC LIMIT :n"
            ),
            {"sid": session_id, "n": max_turns},
        )
        rows = list(reversed(result.fetchall()))  # oldest-first

        messages: list[dict] = []
        for row in rows:
            try:
                req_data = json.loads(row.request_json)
                sections = req_data.get("requested_sections", [])
                specialty = req_data.get("specialty", "primary_care")
                user_content = (
                    f"Please summarize this patient's chart "
                    f"(specialty: {specialty}, "
                    f"sections: {', '.join(sections) if sections else 'all'})."
                )
            except Exception:
                user_content = "Please provide a chart summary."
            messages.append({"role": "user", "content": user_content})
            messages.append({"role": "assistant", "content": row.summary_text})
        return messages

    async def _save_conversation_turn(
        self,
        db: AsyncSession,
        session_id: str,
        request: SummaryRequest,
        response: SummaryResponse,
    ) -> None:
        """
        Append a completed turn to the conversation history.

        Inserts into conversation_turns and increments the session's
        turn_count denormalised counter. Failures are logged but never
        raised — they must not interrupt the clinical workflow.
        """
        try:
            result = await db.execute(
                text(
                    "SELECT turn_count FROM conversation_sessions WHERE id = :sid"
                ),
                {"sid": session_id},
            )
            row = result.fetchone()
            turn_number = (row.turn_count if row else 0) + 1

            request_snapshot = json.dumps({
                "specialty": request.specialty,
                "date_range_months": request.date_range_months,
                "requested_sections": request.requested_sections,
            })

            await db.execute(
                text(
                    "INSERT INTO conversation_turns "
                    "(session_id, turn_number, summary_id, request_json, "
                    " summary_text, confidence_level, created_at) "
                    "VALUES (:sid, :num, :smid, :rjson, :stext, :clevel, :cat)"
                ),
                {
                    "sid": session_id,
                    "num": turn_number,
                    "smid": response.metadata.request_id,
                    "rjson": request_snapshot,
                    "stext": response.summary_text,
                    "clevel": response.confidence_level,
                    "cat": datetime.utcnow().isoformat(),
                },
            )
            await db.execute(
                text(
                    "UPDATE conversation_sessions SET turn_count = :n WHERE id = :sid"
                ),
                {"n": turn_number, "sid": session_id},
            )
            await db.commit()
        except Exception as exc:
            logger.error(
                "CONVERSATION_TURN_SAVE_FAILED | session_id=%s error=%s",
                session_id,
                type(exc).__name__,
            )

    # ------------------------------------------------------------------
    # Pipeline helpers
    # ------------------------------------------------------------------

    def _build_initial_state(
        self,
        request: SummaryRequest,
        request_id: str,
        session_id: str,
        conversation_history: list[dict],
    ) -> SummarizerState:
        """Translate a SummaryRequest into the initial SummarizerState."""
        if request.date_range:
            delta = request.date_range.end - request.date_range.start
            date_range_months = max(1, int(delta.days / 30))
        else:
            date_range_months = settings.SUMMARY_DEFAULT_MONTHS

        return {  # type: ignore[return-value]
            # Input
            "patient_id": request.patient_id,
            "specialty": request.specialty,
            "date_range_months": date_range_months,
            "requested_sections": request.requested_sections or None,
            # Data (empty until retrieve_data_node runs)
            "demographics": None,
            "conditions": [],
            "medications": [],
            "allergies": [],
            "lab_results": [],
            "vitals": [],
            "encounters": [],
            "immunizations": [],
            "procedures": [],
            # Processing
            "structured_context": "",
            "raw_summary": "",
            "verification_result": None,
            # Control
            "retry_count": 0,
            # Conversation context
            "session_id": session_id,
            "conversation_history": conversation_history,
            # Output
            "final_summary": None,
            "errors": [],
            "metadata": {
                "request_id": request_id,
                "requesting_provider_id": request.requesting_provider_id,
            },
        }

    def _build_fallback_response(
        self,
        final_state: Any,
        request_id: str,
        start_time: float,
        patient_id: str,
        specialty: str,
        session_id: str,
    ) -> SummaryResponse:
        """Build a minimal SummaryResponse on catastrophic pipeline failure."""
        latency_ms = int((time.time() - start_time) * 1000)
        errors: list[str] = final_state.get("errors") or []
        metadata = SummaryMetadata(
            request_id=request_id,
            patient_id=patient_id,
            model_used=settings.LLM_MODEL,
            provider=settings.LLM_PROVIDER,
            input_tokens=0,
            output_tokens=0,
            latency_ms=latency_ms,
            data_sections_retrieved=[],
            specialty_context=specialty,
            session_id=session_id,
        )
        vr = VerificationResult(
            verified_claims=[],
            unverified_claims=[],
            confidence_score=0.0,
            confidence_level="RED",
            flags=errors or ["Pipeline did not produce a summary."],
        )
        return SummaryResponse(
            summary_text="[ERROR] Summary generation failed. See errors for details.",
            citations=[],
            confidence_level="RED",
            metadata=metadata,
            verification_result=vr,
            status="failed",
            session_id=session_id,
        )

    def _write_audit_log(
        self,
        request_id: str,
        patient_id: str,
        provider_id: Optional[str],
        model_used: str,
        status: str,
        latency_ms: int,
    ) -> None:
        """
        Write a HIPAA-compliant audit log entry.

        Logged: request_id, timestamp, provider_id, patient_id (PID only),
                model_used, latency_ms, status.
        Never logged: patient name, DOB, SSN, or any PHI beyond the PID.
        """
        if not settings.AUDIT_LOG_ENABLED:
            return
        logger.info(
            "AUDIT | request_id=%s patient_pid=%s provider=%s model=%s "
            "status=%s latency_ms=%d",
            request_id,
            patient_id,
            provider_id or "unknown",
            model_used,
            status,
            latency_ms,
        )