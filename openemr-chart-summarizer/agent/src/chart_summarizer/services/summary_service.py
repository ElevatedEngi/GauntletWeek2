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

import time
import uuid
from typing import Any, Optional

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
    - Build the initial SummarizerState.
    - Invoke the compiled LangGraph pipeline.
    - Return the SummaryResponse assembled by format_output_node.
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

    async def generate_summary(self, request: SummaryRequest) -> SummaryResponse:
        """
        Generate a patient chart summary for the given request.

        Steps:
        1. Resolve date range (explicit DateRange or SUMMARY_DEFAULT_MONTHS).
        2. Build initial SummarizerState.
        3. Invoke the LangGraph pipeline asynchronously.
        4. Return the SummaryResponse from format_output_node.
        5. Write HIPAA audit log (always, even on failure).

        Args:
            request: Validated SummaryRequest from the API layer.

        Returns:
            SummaryResponse containing the generated summary and metadata.

        Raises:
            Exception: Propagates any unrecoverable pipeline exception after
                       writing the audit log.
        """
        request_id = str(uuid.uuid4())
        start_time = time.time()
        audit_status = "complete"
        model_used = settings.LLM_MODEL

        try:
            initial_state = self._build_initial_state(request, request_id)
            final_state: SummarizerState = await self._pipeline.ainvoke(initial_state)

            final_summary: Optional[SummaryResponse] = final_state.get("final_summary")

            if final_summary is not None:
                model_used = final_summary.metadata.model_used or settings.LLM_MODEL
                audit_status = final_summary.status
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
    # Private helpers
    # ------------------------------------------------------------------

    def _build_initial_state(
        self, request: SummaryRequest, request_id: str
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
