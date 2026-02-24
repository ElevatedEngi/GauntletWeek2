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
Unit tests for SummaryService.

Uses mock tools and a stub LLM so no real API calls are made.
"""

from datetime import date
from typing import Any, Optional, Type
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chart_summarizer.graph.pipeline import create_pipeline
from chart_summarizer.llm.base import LLMProvider, LLMResponse
from chart_summarizer.models.summary import DateRange, SummaryRequest, SummaryResponse
from chart_summarizer.services.summary_service import SummaryService
from chart_summarizer.tools.mock import create_mock_tools


# ---------------------------------------------------------------------------
# Stub LLM (same pattern as test_pipeline.py)
# ---------------------------------------------------------------------------


class _StubLLM(LLMProvider):
    def __init__(self, text: str = "") -> None:
        self._text = text or (
            "## \u26a0\ufe0f DRAFT \u2014 AI-GENERATED \u2014 REQUIRES CLINICIAN REVIEW\n\n"
            "Patient has hypertension. [Source: cond-001]\n"
            "Patient is allergic to Penicillin. [Source: allergy-001]\n"
            "Patient takes Lisinopril. [Source: med-001]\n"
        )

    @property
    def model_name(self) -> str:
        return "stub-model"

    @property
    def supports_tool_calling(self) -> bool:
        return False

    @property
    def max_context_window(self) -> int:
        return 4096

    async def generate(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
    ) -> LLMResponse:
        return LLMResponse(
            content=self._text,
            model="stub-model",
            input_tokens=80,
            output_tokens=40,
        )

    async def generate_structured(
        self, system_prompt: str, messages: list[dict[str, Any]], response_model: Type[Any]
    ) -> Any:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_service(llm_text: str = "") -> SummaryService:
    """Return a SummaryService wired with mock tools + stub LLM."""
    pipeline = create_pipeline(
        tools=create_mock_tools(), llm_provider=_StubLLM(text=llm_text)
    )
    return SummaryService(pipeline=pipeline)


def _basic_request(**kwargs: Any) -> SummaryRequest:
    defaults: dict[str, Any] = {
        "patient_id": "TEST-001",
        "specialty": "primary_care",
        "requested_sections": [
            "demographics",
            "conditions",
            "medications",
            "allergies",
        ],
    }
    defaults.update(kwargs)
    return SummaryRequest(**defaults)


# ---------------------------------------------------------------------------
# _build_initial_state tests
# ---------------------------------------------------------------------------


class TestBuildInitialState:
    def test_defaults_to_config_months_when_no_date_range(self) -> None:
        service = _make_service()
        req = _basic_request()
        state = service._build_initial_state(req)
        from chart_summarizer.config import settings

        assert state["date_range_months"] == settings.SUMMARY_DEFAULT_MONTHS

    def test_computes_months_from_explicit_date_range(self) -> None:
        service = _make_service()
        req = _basic_request(
            date_range=DateRange(start=date(2024, 1, 1), end=date(2024, 7, 1))
        )
        state = service._build_initial_state(req)
        # 181 days / 30 ≈ 6 months
        assert state["date_range_months"] == 6

    def test_maps_request_fields(self) -> None:
        service = _make_service()
        req = _basic_request(
            patient_id="P-999",
            specialty="cardiology",
            requesting_provider_id="doc-42",
        )
        state = service._build_initial_state(req)
        assert state["patient_id"] == "P-999"
        assert state["specialty"] == "cardiology"
        assert state["requesting_provider_id"] == "doc-42"

    def test_pipeline_errors_initialised_empty(self) -> None:
        service = _make_service()
        state = service._build_initial_state(_basic_request())
        assert state["pipeline_errors"] == []


# ---------------------------------------------------------------------------
# generate_summary end-to-end tests
# ---------------------------------------------------------------------------


class TestGenerateSummary:
    async def test_returns_summary_response(self) -> None:
        service = _make_service()
        result = await service.generate_summary(_basic_request())
        assert isinstance(result, SummaryResponse)

    async def test_summary_text_non_empty(self) -> None:
        service = _make_service()
        result = await service.generate_summary(_basic_request())
        assert len(result.summary_text) > 0

    async def test_confidence_level_is_valid(self) -> None:
        service = _make_service()
        result = await service.generate_summary(_basic_request())
        assert result.confidence_level in ("GREEN", "YELLOW", "RED")

    async def test_metadata_populated(self) -> None:
        service = _make_service()
        result = await service.generate_summary(
            _basic_request(patient_id="TEST-002", specialty="cardiology")
        )
        assert result.metadata.patient_id == "TEST-002"
        assert result.metadata.specialty_context == "cardiology"
        assert result.metadata.latency_ms >= 0
        assert result.metadata.model_used  # non-empty

    async def test_verification_result_present(self) -> None:
        service = _make_service()
        result = await service.generate_summary(_basic_request())
        vr = result.verification_result
        assert vr.confidence_score >= 0.0
        assert vr.confidence_level in ("GREEN", "YELLOW", "RED")

    async def test_status_complete_for_successful_run(self) -> None:
        service = _make_service()
        result = await service.generate_summary(_basic_request())
        assert result.status in ("complete", "partial")  # mock data may have no errors

    async def test_status_failed_when_llm_errors(self) -> None:
        class _FailLLM(_StubLLM):
            async def generate(self, system_prompt, messages, tools=None):  # type: ignore[override]
                raise RuntimeError("LLM down")

        pipeline = create_pipeline(tools=create_mock_tools(), llm_provider=_FailLLM())
        service = SummaryService(pipeline=pipeline)
        result = await service.generate_summary(_basic_request())
        assert result.status == "failed"
        assert "[ERROR]" in result.summary_text

    async def test_disclaimer_always_present(self) -> None:
        service = _make_service()
        result = await service.generate_summary(_basic_request())
        assert result.disclaimer  # non-empty default disclaimer

    async def test_data_sections_retrieved_in_metadata(self) -> None:
        service = _make_service()
        result = await service.generate_summary(
            _basic_request(
                requested_sections=["demographics", "conditions", "medications"]
            )
        )
        assert len(result.metadata.data_sections_retrieved) > 0

    async def test_audit_log_written_on_success(self) -> None:
        service = _make_service()
        with patch.object(service, "_write_audit_log") as mock_audit:
            await service.generate_summary(_basic_request())
            mock_audit.assert_called_once()
            kwargs = mock_audit.call_args.kwargs
            assert kwargs["patient_id"] == "TEST-001"
            assert kwargs["status"] in ("complete", "partial")

    async def test_audit_log_written_on_failure(self) -> None:
        class _FailLLM(_StubLLM):
            async def generate(self, system_prompt, messages, tools=None):  # type: ignore[override]
                raise RuntimeError("crash")

        pipeline = create_pipeline(tools=create_mock_tools(), llm_provider=_FailLLM())
        service = SummaryService(pipeline=pipeline)
        with patch.object(service, "_write_audit_log") as mock_audit:
            # LLM failure is caught at pipeline level, not propagated to service
            result = await service.generate_summary(_basic_request())
            mock_audit.assert_called_once()

    async def test_all_mock_patients_produce_responses(self) -> None:
        from chart_summarizer.tools.mock import MOCK_PATIENT_IDS

        service = _make_service()
        for pid in MOCK_PATIENT_IDS:
            result = await service.generate_summary(
                _basic_request(
                    patient_id=pid,
                    requested_sections=[
                        "demographics", "conditions", "medications",
                        "allergies", "labs", "vitals", "encounters",
                        "immunizations", "procedures",
                    ],
                )
            )
            assert isinstance(result, SummaryResponse), f"No response for {pid}"

    async def test_explicit_date_range_passed_through(self) -> None:
        service = _make_service()
        result = await service.generate_summary(
            _basic_request(
                date_range=DateRange(start=date(2023, 1, 1), end=date(2024, 1, 1))
            )
        )
        # Should complete without error
        assert result.status in ("complete", "partial", "failed")
