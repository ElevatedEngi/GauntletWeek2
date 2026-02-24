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
Unit tests for the LangGraph pipeline nodes and helper functions.

All tests use mock tools and a stub LLM provider — no real API calls.
"""

from typing import Any, Optional, Type
from unittest.mock import AsyncMock

import pytest

from chart_summarizer.graph.pipeline import (
    _build_system_prompt,
    _extract_citations,
    _fmt_section,
    _format_patient_data,
    _sort_by_date,
    create_pipeline,
)
from chart_summarizer.llm.base import LLMProvider, LLMResponse
from chart_summarizer.tools.mock import MOCK_PATIENT_IDS, create_mock_tools
from chart_summarizer.verification.verifier import SummaryVerifier


# ---------------------------------------------------------------------------
# Stub LLM provider
# ---------------------------------------------------------------------------


class _StubLLM(LLMProvider):
    """A simple stub LLM that returns a fixed summary with one citation."""

    def __init__(self, response_text: str = "") -> None:
        self._text = response_text or (
            "## \u26a0\ufe0f DRAFT \u2014 AI-GENERATED \u2014 REQUIRES CLINICIAN REVIEW\n\n"
            "Patient has hypertension. [Source: cond-hypertension]\n"
            "Patient takes Lisinopril. [Source: med-lisinopril]\n"
            "Patient is allergic to Penicillin. [Source: allergy-penicillin]\n"
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
            input_tokens=100,
            output_tokens=50,
        )

    async def generate_structured(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        response_model: Type[Any],
    ) -> Any:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Helper function unit tests
# ---------------------------------------------------------------------------


class TestSortByDate:
    def test_sorts_descending(self) -> None:
        records = [
            {"effective_date": "2023-01-01"},
            {"effective_date": "2024-06-15"},
            {"effective_date": "2022-11-30"},
        ]
        result = _sort_by_date(records, "effective_date")
        dates = [r["effective_date"] for r in result]
        assert dates == ["2024-06-15", "2023-01-01", "2022-11-30"]

    def test_none_values_sort_last(self) -> None:
        records = [
            {"onset_date": None},
            {"onset_date": "2024-01-01"},
            {"onset_date": "2023-01-01"},
        ]
        result = _sort_by_date(records, "onset_date")
        assert result[0]["onset_date"] == "2024-01-01"
        assert result[-1]["onset_date"] is None


class TestBuildSystemPrompt:
    def test_contains_specialty(self) -> None:
        prompt = _build_system_prompt("cardiology")
        assert "Cardiology" in prompt

    def test_contains_citation_instruction(self) -> None:
        prompt = _build_system_prompt("primary_care")
        assert "[Source:" in prompt

    def test_underscore_specialty_formatted(self) -> None:
        prompt = _build_system_prompt("primary_care")
        assert "Primary Care" in prompt


class TestFmtSection:
    def test_empty_lines_returns_empty_string(self) -> None:
        assert _fmt_section("TITLE", []) == ""

    def test_non_empty_includes_title(self) -> None:
        result = _fmt_section("MEDICATIONS", ["Lisinopril 10mg"])
        assert "=== MEDICATIONS ===" in result
        assert "Lisinopril 10mg" in result


class TestFormatPatientData:
    def test_demographics_section(self) -> None:
        data = {
            "demographics": {
                "patient_id": "P001",
                "fhir_id": "fhir-P001",
                "first_name": "Jane",
                "last_name": "Doe",
                "date_of_birth": "1991-03-15",
                "sex": "Female",
            }
        }
        result = _format_patient_data(data)
        assert "Jane Doe" in result
        assert "PATIENT DEMOGRAPHICS" in result

    def test_conditions_section(self) -> None:
        data = {
            "conditions": [
                {
                    "condition_id": "cond-001",
                    "display_name": "Hypertension",
                    "icd10_code": "I10",
                    "clinical_status": "active",
                    "onset_date": "2020-01-01",
                }
            ]
        }
        result = _format_patient_data(data)
        assert "cond-001" in result
        assert "Hypertension" in result
        assert "I10" in result

    def test_empty_data_returns_empty(self) -> None:
        assert _format_patient_data({}) == ""

    def test_medications_section(self) -> None:
        data = {
            "medications": [
                {
                    "medication_id": "med-001",
                    "name": "Lisinopril",
                    "dosage": "10 mg",
                    "frequency": "once daily",
                    "route": "oral",
                    "status": "active",
                }
            ]
        }
        result = _format_patient_data(data)
        assert "med-001" in result
        assert "Lisinopril" in result


class TestExtractCitations:
    def test_extracts_source_ids(self) -> None:
        text = "Patient has HTN. [Source: cond-001]\nPatient takes Lisinopril. [Source: med-001]"
        patient_data: dict[str, Any] = {}
        result = _extract_citations(text, patient_data)
        assert len(result) == 2
        ids = {c["source_id"] for c in result}
        assert "cond-001" in ids
        assert "med-001" in ids

    def test_resolves_source_type(self) -> None:
        text = "Blood pressure normal. [Source: vital-bp-001]"
        patient_data = {
            "vitals": [{"vital_id": "vital-bp-001", "type": "blood-pressure", "value": "120/80"}]
        }
        result = _extract_citations(text, patient_data)
        assert result[0]["source_type"] == "vital"

    def test_empty_text_returns_empty(self) -> None:
        result = _extract_citations("No citations here.", {})
        assert result == []

    def test_claim_text_stripped_of_tags(self) -> None:
        text = "Patient is allergic to Penicillin. [Source: allergy-pen]"
        result = _extract_citations(text, {})
        assert "Source" not in result[0]["claim_text"]
        assert "Penicillin" in result[0]["claim_text"]


# ---------------------------------------------------------------------------
# SummaryVerifier tests
# ---------------------------------------------------------------------------


class TestSummaryVerifier:
    async def test_all_verified_returns_green(self) -> None:
        verifier = SummaryVerifier()
        patient_data = {
            "conditions": [{"condition_id": "c1", "clinical_status": "active", "display_name": "HTN"}],
            "medications": [{"medication_id": "m1", "status": "active", "name": "Lisinopril"}],
            "allergies": [{"allergy_id": "a1", "substance": "Penicillin", "clinical_status": "active"}],
        }
        # Summary mentions all active meds/allergies and cites them correctly
        summary = (
            "Patient has HTN. [Source: c1]\n"
            "Takes Lisinopril. [Source: m1]\n"
            "Allergic to Penicillin. [Source: a1]\n"
        )
        result = await verifier.verify(summary, patient_data)
        assert result.confidence_level == "GREEN"
        assert len(result.verified_claims) == 3
        assert len(result.unverified_claims) == 0
        assert not result.flags

    async def test_unknown_source_id_is_unverified(self) -> None:
        verifier = SummaryVerifier()
        patient_data: dict[str, Any] = {}
        summary = "Patient has diabetes. [Source: nonexistent-id]"
        result = await verifier.verify(summary, patient_data)
        assert len(result.unverified_claims) == 1
        assert result.confidence_level == "RED"

    async def test_missing_allergy_forces_red(self) -> None:
        verifier = SummaryVerifier()
        patient_data = {
            "allergies": [
                {"allergy_id": "a1", "substance": "Penicillin", "clinical_status": "active"}
            ],
            "medications": [],
        }
        # Summary does NOT mention Penicillin
        summary = "Patient is healthy. [Source: a1]"
        result = await verifier.verify(summary, patient_data)
        assert any("Penicillin" in f for f in result.flags)
        assert result.confidence_level == "RED"

    async def test_missing_active_med_forces_red(self) -> None:
        verifier = SummaryVerifier()
        patient_data = {
            "medications": [
                {"medication_id": "m1", "name": "Metformin", "status": "active"}
            ],
            "allergies": [],
        }
        # Summary does NOT mention Metformin
        summary = "Patient's blood glucose is stable. [Source: m1]"
        result = await verifier.verify(summary, patient_data)
        assert any("Metformin" in f for f in result.flags)

    async def test_no_citations_scores_100_percent(self) -> None:
        verifier = SummaryVerifier()
        result = await verifier.verify("No citations here.", {})
        assert result.confidence_score == 1.0
        assert result.confidence_level == "GREEN"

    def test_compute_confidence_levels(self) -> None:
        v = SummaryVerifier()
        assert v._compute_confidence_level(1.00) == "GREEN"
        assert v._compute_confidence_level(0.95) == "GREEN"
        assert v._compute_confidence_level(0.94) == "YELLOW"
        assert v._compute_confidence_level(0.90) == "YELLOW"
        assert v._compute_confidence_level(0.89) == "RED"
        assert v._compute_confidence_level(0.00) == "RED"


# ---------------------------------------------------------------------------
# Pipeline integration tests (mock tools + stub LLM)
# ---------------------------------------------------------------------------


class TestPipeline:
    def _make_pipeline(self, llm_text: str = "") -> Any:
        tools = create_mock_tools()
        llm = _StubLLM(response_text=llm_text)
        return create_pipeline(tools=tools, llm_provider=llm)

    async def test_pipeline_completes_for_known_patient(self) -> None:
        pipeline = self._make_pipeline()
        state = await pipeline.ainvoke(
            {
                "patient_id": "TEST-001",
                "specialty": "primary_care",
                "date_range_months": 12,
                "requested_sections": [
                    "demographics", "conditions", "medications",
                    "allergies", "labs", "vitals", "encounters",
                    "immunizations", "procedures",
                ],
                "requesting_provider_id": None,
                "pipeline_errors": [],
            }
        )
        assert "patient_data" in state
        assert "structured_data" in state
        assert "summary_text" in state
        assert "verification_result" in state
        assert "confidence_level" in state

    async def test_retrieve_node_populates_all_sections(self) -> None:
        pipeline = self._make_pipeline()
        state = await pipeline.ainvoke(
            {
                "patient_id": "TEST-002",
                "specialty": "cardiology",
                "date_range_months": 24,
                "requested_sections": [
                    "demographics", "conditions", "medications",
                    "allergies", "labs", "vitals", "encounters",
                    "immunizations", "procedures",
                ],
                "requesting_provider_id": None,
                "pipeline_errors": [],
            }
        )
        pd = state["patient_data"]
        assert "demographics" in pd
        assert "conditions" in pd
        assert "medications" in pd
        assert "allergies" in pd

    async def test_requested_sections_filter_applied(self) -> None:
        pipeline = self._make_pipeline()
        state = await pipeline.ainvoke(
            {
                "patient_id": "TEST-001",
                "specialty": "primary_care",
                "date_range_months": 12,
                "requested_sections": ["demographics", "conditions"],
                "requesting_provider_id": None,
                "pipeline_errors": [],
            }
        )
        sd = state["structured_data"]
        # Only requested sections should appear
        assert "demographics" in sd or "conditions" in sd
        assert "procedures" not in sd

    async def test_unknown_patient_produces_retrieval_errors(self) -> None:
        pipeline = self._make_pipeline()
        state = await pipeline.ainvoke(
            {
                "patient_id": "UNKNOWN-999",
                "specialty": "primary_care",
                "date_range_months": 12,
                "requested_sections": ["demographics"],
                "requesting_provider_id": None,
                "pipeline_errors": [],
            }
        )
        assert len(state.get("retrieval_errors", [])) > 0

    async def test_summary_text_contains_draft_header(self) -> None:
        pipeline = self._make_pipeline()
        state = await pipeline.ainvoke(
            {
                "patient_id": "TEST-001",
                "specialty": "primary_care",
                "date_range_months": 12,
                "requested_sections": ["demographics", "conditions"],
                "requesting_provider_id": None,
                "pipeline_errors": [],
            }
        )
        assert "DRAFT" in state["summary_text"]

    async def test_verification_result_structure(self) -> None:
        pipeline = self._make_pipeline()
        state = await pipeline.ainvoke(
            {
                "patient_id": "TEST-001",
                "specialty": "primary_care",
                "date_range_months": 12,
                "requested_sections": [
                    "demographics", "conditions", "medications", "allergies",
                ],
                "requesting_provider_id": None,
                "pipeline_errors": [],
            }
        )
        vr = state["verification_result"]
        assert "confidence_score" in vr
        assert "confidence_level" in vr
        assert "verified_claims" in vr
        assert "unverified_claims" in vr
        assert state["confidence_level"] in ("GREEN", "YELLOW", "RED")

    async def test_llm_error_captured_in_pipeline_errors(self) -> None:
        class _FailingLLM(_StubLLM):
            async def generate(self, system_prompt, messages, tools=None):  # type: ignore[override]
                raise RuntimeError("LLM offline")

        tools = create_mock_tools()
        pipeline = create_pipeline(tools=tools, llm_provider=_FailingLLM())
        state = await pipeline.ainvoke(
            {
                "patient_id": "TEST-001",
                "specialty": "primary_care",
                "date_range_months": 12,
                "requested_sections": ["demographics"],
                "requesting_provider_id": None,
                "pipeline_errors": [],
            }
        )
        assert "[ERROR]" in state["summary_text"]
        assert len(state.get("pipeline_errors", [])) > 0

    async def test_all_mock_patient_ids_run_successfully(self) -> None:
        pipeline = self._make_pipeline()
        for pid in MOCK_PATIENT_IDS:
            state = await pipeline.ainvoke(
                {
                    "patient_id": pid,
                    "specialty": "primary_care",
                    "date_range_months": 12,
                    "requested_sections": [
                        "demographics", "conditions", "medications",
                        "allergies", "labs", "vitals", "encounters",
                        "immunizations", "procedures",
                    ],
                    "requesting_provider_id": None,
                    "pipeline_errors": [],
                }
            )
            assert state["summary_text"], f"No summary generated for {pid}"
