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
It translates a SummaryRequest into a PipelineState, invokes the LangGraph
pipeline, and maps the result back to a SummaryResponse.
"""

import time
import uuid
from typing import Any

from chart_summarizer.config import settings
from chart_summarizer.graph.pipeline import PipelineState, create_pipeline
from chart_summarizer.models.summary import (
    Citation,
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
    - Prepare the initial PipelineState.
    - Invoke the compiled LangGraph pipeline.
    - Map pipeline outputs to a SummaryResponse.
    - Log the audit event (who, when, which patient, which model).
    """

    def __init__(self, pipeline: Any = None) -> None:
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
        1. Resolves the date range (defaults to SUMMARY_DEFAULT_MONTHS if omitted).
        2. Builds the initial PipelineState.
        3. Invokes the LangGraph pipeline asynchronously.
        4. Converts pipeline outputs into a SummaryResponse.
        5. Writes to the HIPAA audit log (always, even on failure).

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
            initial_state = self._build_initial_state(request)
            final_state = await self._pipeline.ainvoke(initial_state)

            model_used = final_state.get("model_used") or settings.LLM_MODEL

            if final_state.get("summary_text", "").startswith("[ERROR]"):
                audit_status = "failed"
            elif final_state.get("retrieval_errors"):
                audit_status = "partial"

            return self._map_to_response(
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

    def _build_initial_state(self, request: SummaryRequest) -> PipelineState:
        """
        Translate a SummaryRequest into the initial LangGraph PipelineState.

        Computes date_range_months from the explicit DateRange if provided,
        otherwise falls back to SUMMARY_DEFAULT_MONTHS from config.
        """
        if request.date_range:
            delta = request.date_range.end - request.date_range.start
            date_range_months = max(1, int(delta.days / 30))
        else:
            date_range_months = settings.SUMMARY_DEFAULT_MONTHS

        return {  # type: ignore[return-value]
            "patient_id": request.patient_id,
            "specialty": request.specialty,
            "date_range_months": date_range_months,
            "requested_sections": request.requested_sections,
            "requesting_provider_id": request.requesting_provider_id,
            # Pre-populate optional output keys so LangGraph never sees missing keys.
            "patient_data": {},
            "retrieval_errors": [],
            "structured_data": {},
            "summary_text": "",
            "citations": [],
            "model_used": "",
            "input_tokens": 0,
            "output_tokens": 0,
            "verification_result": {},
            "confidence_level": "RED",
            "pipeline_errors": [],
        }

    def _map_to_response(
        self,
        final_state: Any,
        request_id: str,
        start_time: float,
        patient_id: str,
        specialty: str,
    ) -> SummaryResponse:
        """
        Map the final LangGraph pipeline state to a SummaryResponse.

        Determines summary status (complete / partial / failed) from
        pipeline error flags and retrieval errors.
        """
        latency_ms = int((time.time() - start_time) * 1000)

        summary_text: str = final_state.get("summary_text", "")
        retrieval_errors: list[str] = final_state.get("retrieval_errors", [])
        pipeline_errors: list[str] = final_state.get("pipeline_errors", [])

        if summary_text.startswith("[ERROR]"):
            response_status = "failed"
        elif retrieval_errors or pipeline_errors:
            response_status = "partial"
        else:
            response_status = "complete"

        model_used: str = final_state.get("model_used") or settings.LLM_MODEL

        metadata = SummaryMetadata(
            request_id=request_id,
            patient_id=patient_id,
            model_used=model_used,
            provider=settings.LLM_PROVIDER,
            input_tokens=final_state.get("input_tokens", 0),
            output_tokens=final_state.get("output_tokens", 0),
            latency_ms=latency_ms,
            data_sections_retrieved=list(final_state.get("patient_data", {}).keys()),
            specialty_context=specialty,
        )

        # Re-hydrate VerificationResult from the serialised dict in state.
        vr_dict: dict[str, Any] = final_state.get("verification_result") or {}
        if vr_dict:
            verification_result = VerificationResult.model_validate(vr_dict)
        else:
            verification_result = VerificationResult(
                verified_claims=[],
                unverified_claims=[],
                confidence_score=0.0,
                confidence_level="RED",
                flags=pipeline_errors or ["Pipeline did not complete verification."],
            )

        citations = [
            Citation.model_validate(c)
            for c in (final_state.get("citations") or [])
        ]

        confidence_level: str = final_state.get("confidence_level") or "RED"

        return SummaryResponse(
            summary_text=summary_text,
            citations=citations,
            confidence_level=confidence_level,  # type: ignore[arg-type]
            metadata=metadata,
            verification_result=verification_result,
            status=response_status,  # type: ignore[arg-type]
        )

    def _write_audit_log(
        self,
        request_id: str,
        patient_id: str,
        provider_id: str | None,
        model_used: str,
        status: str,
        latency_ms: int,
    ) -> None:
        """
        Write a HIPAA-compliant audit log entry for this summary request.

        Logged fields: request_id, timestamp, provider_id, patient_id (PID only),
        model_used, latency_ms, status.

        Never log: patient name, DOB, SSN, or any PHI beyond the PID.
        """
        if not settings.AUDIT_LOG_ENABLED:
            return
        logger.info(
            "AUDIT | request_id=%s patient_pid=%s provider=%s model=%s status=%s latency_ms=%d",
            request_id,
            patient_id,
            provider_id or "unknown",
            model_used,
            status,
            latency_ms,
        )
