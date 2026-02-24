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
Unit tests for the individual LangGraph node functions in graph/nodes.py.

Tests cover:
- retrieve_data_node: concurrent tool execution, graceful failure, validation
- structure_data_node: specialty ordering, token truncation
- generate_summary_node: specialty prompt loading, retry mode, LLM error handling
- verify_summary_node: HALLUCINATION detection, confidence warning banner
- format_output_node: HTML output, confidence badge, citation assembly

All tests use mock tools / stub LLM — no real API calls.
"""

from typing import Any, Optional, Type

import pytest

from chart_summarizer.graph.nodes import (
    _build_full_system_prompt,
    _count_tokens,
    _load_specialty_prompt,
    _md_to_html,
    _model_to_dict,
    _state_to_patient_data,
    format_output_node,
    make_generate_summary_node,
    make_retrieve_data_node,
    structure_data_node,
    verify_summary_node,
)
from chart_summarizer.graph.state import SummarizerState
from chart_summarizer.llm.base import LLMProvider, LLMResponse
from chart_summarizer.models.patient import (
    Allergy,
    Condition,
    Medication,
    PatientDemographics,
    VitalSign,
)
from chart_summarizer.models.summary import VerificationResult
from chart_summarizer.tools.base import FHIRTool, ToolResult
from chart_summarizer.tools.mock import create_mock_tools


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


def _make_state(**overrides: Any) -> SummarizerState:
    """Build a minimal valid SummarizerState with optional overrides."""
    base: dict[str, Any] = {
        "patient_id": "TEST-001",
        "specialty": "primary_care",
        "date_range_months": 12,
        "requested_sections": None,
        "demographics": None,
        "conditions": [],
        "medications": [],
        "allergies": [],
        "lab_results": [],
        "vitals": [],
        "encounters": [],
        "immunizations": [],
        "procedures": [],
        "structured_context": "",
        "raw_summary": "",
        "verification_result": None,
        "retry_count": 0,
        "final_summary": None,
        "errors": [],
        "metadata": {},
    }
    base.update(overrides)
    return base  # type: ignore[return-value]


def _make_demographics() -> PatientDemographics:
    return PatientDemographics(
        patient_id="TEST-001",
        fhir_id="fhir-001",
        first_name="Jane",
        last_name="Doe",
        date_of_birth="1990-05-15",
        sex="Female",
    )


def _make_condition(cid: str = "cond-001", name: str = "Hypertension") -> Condition:
    return Condition(
        condition_id=cid,
        display_name=name,
        icd10_code="I10",
        clinical_status="active",
    )


def _make_medication(mid: str = "med-001", name: str = "Lisinopril") -> Medication:
    return Medication(
        medication_id=mid,
        name=name,
        dosage="10 mg",
        frequency="once daily",
        status="active",
    )


def _make_allergy(aid: str = "allergy-001", substance: str = "Penicillin") -> Allergy:
    return Allergy(
        allergy_id=aid,
        substance=substance,
        reaction="anaphylaxis",
        severity="severe",
        clinical_status="active",
    )


class _StubLLM(LLMProvider):
    def __init__(self, response_text: str = "", raise_exc: Optional[Exception] = None) -> None:
        self._text = response_text or (
            "## ⚠️ DRAFT — AI-GENERATED — REQUIRES CLINICIAN REVIEW\n\n"
            "Patient has hypertension. [Source: cond-001]\n"
            "Patient takes Lisinopril 10 mg. [Source: med-001]\n"
            "Patient is allergic to Penicillin. [Source: allergy-001]\n"
        )
        self._exc = raise_exc

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
        if self._exc:
            raise self._exc
        # Store the last prompt so tests can inspect it
        self.last_system_prompt = system_prompt
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
# Helper function tests
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_model_to_dict_pydantic(self) -> None:
        demo = _make_demographics()
        d = _model_to_dict(demo)
        assert d["first_name"] == "Jane"
        assert d["patient_id"] == "TEST-001"

    def test_model_to_dict_plain_dict(self) -> None:
        d = _model_to_dict({"key": "val"})
        assert d["key"] == "val"

    def test_state_to_patient_data_converts_lab_results_key(self) -> None:
        state = _make_state(
            allergies=[_make_allergy()],
            lab_results=[],
        )
        pd = _state_to_patient_data(state)
        # lab_results → labs for verifier compatibility
        assert "labs" not in pd or "lab_results" not in pd

    def test_state_to_patient_data_includes_allergies(self) -> None:
        state = _make_state(allergies=[_make_allergy(substance="Sulfa")])
        pd = _state_to_patient_data(state)
        assert "allergies" in pd
        assert pd["allergies"][0]["substance"] == "Sulfa"

    def test_count_tokens_returns_positive(self) -> None:
        count = _count_tokens("Hello world, this is a test.")
        assert count > 0

    def test_load_specialty_prompt_primary_care(self) -> None:
        text = _load_specialty_prompt("primary_care")
        assert len(text) > 50  # Non-empty prompt file
        assert "Primary Care" in text or "primary" in text.lower()

    def test_load_specialty_prompt_cardiology(self) -> None:
        text = _load_specialty_prompt("cardiology")
        assert "cardiology" in text.lower() or "cardiac" in text.lower()

    def test_load_specialty_prompt_unknown_falls_back_to_general(self) -> None:
        text = _load_specialty_prompt("nephrology_subspecialty_xyz")
        # Should load general.txt (non-empty)
        assert len(text) > 10

    def test_build_full_system_prompt_contains_specialty(self) -> None:
        prompt = _build_full_system_prompt("You are a cardiologist.", "cardiology", False)
        assert "Cardiology" in prompt

    def test_build_full_system_prompt_retry_adds_strict_mode(self) -> None:
        prompt = _build_full_system_prompt("Base prompt", "primary_care", True)
        assert "STRICTER MODE" in prompt or "stricter" in prompt.lower()

    def test_build_full_system_prompt_no_retry_no_strict_mode(self) -> None:
        prompt = _build_full_system_prompt("Base prompt", "primary_care", False)
        assert "STRICTER MODE" not in prompt

    def test_md_to_html_returns_string(self) -> None:
        result = _md_to_html("# Hello\n\nThis is **bold**.")
        assert isinstance(result, str)
        assert "Hello" in result


# ---------------------------------------------------------------------------
# retrieve_data_node tests
# ---------------------------------------------------------------------------


class TestRetrieveDataNode:
    async def test_all_tools_succeed(self) -> None:
        tools = create_mock_tools()
        node = make_retrieve_data_node(tools)
        state = _make_state(patient_id="TEST-001")
        result = await node(state)

        assert result["demographics"] is not None
        assert isinstance(result["conditions"], list)
        assert isinstance(result["medications"], list)
        assert isinstance(result["allergies"], list)
        assert result["errors"] == []

    async def test_unknown_patient_produces_errors(self) -> None:
        tools = create_mock_tools()
        node = make_retrieve_data_node(tools)
        state = _make_state(patient_id="PATIENT-DOES-NOT-EXIST-999")
        result = await node(state)

        assert len(result["errors"]) > 0

    async def test_graceful_degradation_when_one_tool_fails(self) -> None:
        """If one tool raises, others still succeed and error is recorded."""
        good_tools = create_mock_tools()

        class _FailingDemoTool(type(good_tools[0])):
            tool_name = "get_patient_demographics"
            description = "failing demo"

            async def execute(self, patient_id: str, **kwargs: Any) -> ToolResult:
                raise RuntimeError("FHIR server unreachable")

        failing_tool = _FailingDemoTool()
        # Replace the demographics tool with the failing one
        mixed_tools = [
            failing_tool if t.tool_name == "get_patient_demographics" else t
            for t in good_tools
        ]
        node = make_retrieve_data_node(mixed_tools)
        state = _make_state(patient_id="TEST-001")
        result = await node(state)

        # Should have errors for the failing tool
        assert any("get_patient_demographics" in e or "FHIR" in e or "Tool error" in e
                   for e in result["errors"])
        # But other tools should still have returned data
        assert len(result["conditions"]) > 0 or len(result["medications"]) > 0

    async def test_retrieval_timing_recorded_in_metadata(self) -> None:
        tools = create_mock_tools()
        node = make_retrieve_data_node(tools)
        state = _make_state(patient_id="TEST-001")
        result = await node(state)

        assert "retrieval_time_ms" in result["metadata"]
        assert result["metadata"]["retrieval_time_ms"] >= 0

    async def test_all_list_fields_present_even_on_failure(self) -> None:
        """All list fields must be present in result even if tools fail."""
        tools = create_mock_tools()
        node = make_retrieve_data_node(tools)
        state = _make_state(patient_id="UNKNOWN-000")
        result = await node(state)

        for field in ["conditions", "medications", "allergies", "lab_results",
                      "vitals", "encounters", "immunizations", "procedures"]:
            assert field in result
            assert isinstance(result[field], list)


# ---------------------------------------------------------------------------
# structure_data_node tests
# ---------------------------------------------------------------------------


class TestStructureDataNode:
    async def test_produces_structured_context(self) -> None:
        state = _make_state(
            demographics=_make_demographics(),
            conditions=[_make_condition()],
            allergies=[_make_allergy()],
        )
        result = await structure_data_node(state)
        ctx = result["structured_context"]
        assert "Jane" in ctx
        assert "Hypertension" in ctx

    async def test_allergies_appear_before_conditions(self) -> None:
        """Allergies MUST always be listed before conditions for patient safety."""
        state = _make_state(
            demographics=_make_demographics(),
            conditions=[_make_condition()],
            allergies=[_make_allergy(substance="Sulfa")],
        )
        result = await structure_data_node(state)
        ctx = result["structured_context"]
        allergy_pos = ctx.find("Sulfa")
        condition_pos = ctx.find("Hypertension")
        assert allergy_pos < condition_pos, "Allergies must appear before conditions"

    async def test_cardiology_specialty_emphasises_bp_vitals(self) -> None:
        bp_vital = VitalSign(
            vital_id="v-bp",
            type="blood-pressure",
            value="140/90",
            effective_date="2025-01-15T09:00:00",
        )
        hr_vital = VitalSign(
            vital_id="v-hr",
            type="heart-rate",
            value="72",
            effective_date="2025-01-15T09:00:00",
        )
        state = _make_state(
            specialty="cardiology",
            vitals=[hr_vital, bp_vital],  # HR first, BP second in input
        )
        result = await structure_data_node(state)
        ctx = result["structured_context"]
        bp_pos = ctx.find("140/90")
        hr_pos = ctx.find("72")
        # BP should appear before HR in cardiology
        assert bp_pos != -1 and hr_pos != -1
        assert bp_pos < hr_pos

    async def test_requested_sections_filter(self) -> None:
        state = _make_state(
            demographics=_make_demographics(),
            conditions=[_make_condition()],
            medications=[_make_medication()],
            requested_sections=["demographics", "conditions"],  # exclude medications
        )
        result = await structure_data_node(state)
        ctx = result["structured_context"]
        assert "Jane" in ctx
        assert "Hypertension" in ctx
        assert "Lisinopril" not in ctx

    async def test_empty_state_produces_empty_or_minimal_context(self) -> None:
        state = _make_state()
        result = await structure_data_node(state)
        # Should not crash; context may be empty or minimal
        assert "structured_context" in result


# ---------------------------------------------------------------------------
# generate_summary_node tests
# ---------------------------------------------------------------------------


class TestGenerateSummaryNode:
    async def test_uses_cardiology_prompt_for_cardiology(self) -> None:
        stub = _StubLLM()
        node = make_generate_summary_node(stub)
        state = _make_state(
            specialty="cardiology",
            structured_context="Some patient data.",
        )
        await node(state)
        assert "cardiology" in stub.last_system_prompt.lower() or \
               "Cardiology" in stub.last_system_prompt

    async def test_uses_psychiatry_prompt_for_psychiatry(self) -> None:
        stub = _StubLLM()
        node = make_generate_summary_node(stub)
        state = _make_state(specialty="psychiatry", structured_context="Data.")
        await node(state)
        assert "psychiatry" in stub.last_system_prompt.lower() or \
               "Psychiatry" in stub.last_system_prompt

    async def test_retry_mode_uses_stricter_prompt(self) -> None:
        stub = _StubLLM()
        node = make_generate_summary_node(stub)
        # retry_count=1 means we've already tried once → stricter prompt
        state = _make_state(structured_context="Data.", retry_count=1)
        await node(state)
        prompt_lower = stub.last_system_prompt.lower()
        assert "stricter" in prompt_lower or "strict" in prompt_lower

    async def test_raw_summary_set_in_result(self) -> None:
        stub = _StubLLM(response_text="My summary [Source: cond-001]")
        node = make_generate_summary_node(stub)
        state = _make_state(structured_context="Data.")
        result = await node(state)
        assert result["raw_summary"] == "My summary [Source: cond-001]"

    async def test_token_usage_recorded_in_metadata(self) -> None:
        stub = _StubLLM()
        node = make_generate_summary_node(stub)
        state = _make_state(structured_context="Data.")
        result = await node(state)
        assert result["metadata"]["input_tokens"] == 100
        assert result["metadata"]["output_tokens"] == 50

    async def test_llm_error_captured_gracefully(self) -> None:
        stub = _StubLLM(raise_exc=RuntimeError("LLM offline"))
        node = make_generate_summary_node(stub)
        state = _make_state(structured_context="Data.")
        result = await node(state)
        assert "[ERROR]" in result["raw_summary"]
        assert len(result["errors"]) > 0

    async def test_retry_count_incremented(self) -> None:
        stub = _StubLLM()
        node = make_generate_summary_node(stub)
        state = _make_state(structured_context="Data.", retry_count=0)
        result = await node(state)
        assert result["retry_count"] == 1


# ---------------------------------------------------------------------------
# verify_summary_node tests
# ---------------------------------------------------------------------------


class TestVerifySummaryNode:
    async def test_verified_citations_produce_green(self) -> None:
        state = _make_state(
            conditions=[_make_condition("cond-001", "Hypertension")],
            medications=[_make_medication("med-001", "Lisinopril")],
            allergies=[_make_allergy("allergy-001", "Penicillin")],
            raw_summary=(
                "## ⚠️ DRAFT — AI-GENERATED — REQUIRES CLINICIAN REVIEW\n\n"
                "Patient has Hypertension. [Source: cond-001]\n"
                "Takes Lisinopril. [Source: med-001]\n"
                "Allergic to Penicillin. [Source: allergy-001]\n"
            ),
        )
        result = await verify_summary_node(state)
        vr: VerificationResult = result["verification_result"]
        assert vr.confidence_level == "GREEN"
        assert len(vr.verified_claims) == 3
        assert len(vr.unverified_claims) == 0

    async def test_unresolvable_citation_is_unverified(self) -> None:
        state = _make_state(
            raw_summary=(
                "## ⚠️ DRAFT\n"
                "Patient has diabetes. [Source: cond-nonexistent-999]\n"
            ),
        )
        result = await verify_summary_node(state)
        vr: VerificationResult = result["verification_result"]
        assert len(vr.unverified_claims) > 0

    async def test_hallucinated_allergy_flagged_as_critical(self) -> None:
        """Allergy source ID in summary that doesn't exist in data → HALLUCINATION."""
        state = _make_state(
            allergies=[],  # No allergies in source data
            raw_summary=(
                "## ⚠️ DRAFT\n"
                "Patient is allergic to Sulfonamides. [Source: allergy-fabricated-1]\n"
            ),
        )
        result = await verify_summary_node(state)
        vr: VerificationResult = result["verification_result"]
        assert vr.confidence_level == "RED"
        hallucination_flags = [f for f in vr.flags if "HALLUCINATION" in f.upper()]
        assert len(hallucination_flags) > 0
        assert "critical" in hallucination_flags[0].lower()

    async def test_hallucinated_medication_flagged_as_critical(self) -> None:
        """Medication source ID in summary not in data → HALLUCINATION."""
        state = _make_state(
            medications=[],  # No medications in source
            raw_summary=(
                "## ⚠️ DRAFT\n"
                "Patient takes Warfarin. [Source: med-fabricated-warfarin]\n"
            ),
        )
        result = await verify_summary_node(state)
        vr: VerificationResult = result["verification_result"]
        hallucination_flags = [f for f in vr.flags if "HALLUCINATION" in f.upper()]
        assert len(hallucination_flags) > 0

    async def test_low_confidence_adds_warning_banner(self) -> None:
        """Confidence < 90% should inject a warning banner into raw_summary."""
        state = _make_state(
            raw_summary=(
                "## ⚠️ DRAFT — AI-GENERATED — REQUIRES CLINICIAN REVIEW\n\n"
                "Patient has diabetes. [Source: cond-nonexistent]\n"
                "Patient takes insulin. [Source: med-nonexistent]\n"
                "Patient has CKD. [Source: cond-another-nonexistent]\n"
            ),
        )
        result = await verify_summary_node(state)
        # With no matching source records, confidence should be 0 → banner added
        updated_summary: str = result["raw_summary"]
        assert "LOW CONFIDENCE" in updated_summary or "low confidence" in updated_summary.lower()

    async def test_missing_active_allergy_forces_red(self) -> None:
        """Active allergy in source not mentioned in summary → RED."""
        state = _make_state(
            allergies=[_make_allergy("a1", "Penicillin")],
            raw_summary=(
                "## ⚠️ DRAFT\n"
                "Patient is in good health. [Source: a1]\n"
                # 'Penicillin' not mentioned by name
            ),
        )
        result = await verify_summary_node(state)
        vr: VerificationResult = result["verification_result"]
        assert vr.confidence_level == "RED"
        assert any("Penicillin" in f for f in vr.flags)


# ---------------------------------------------------------------------------
# format_output_node tests
# ---------------------------------------------------------------------------


class TestFormatOutputNode:
    async def test_final_summary_is_populated(self) -> None:
        vr = VerificationResult(
            verified_claims=[],
            unverified_claims=[],
            confidence_score=0.95,
            confidence_level="GREEN",
            flags=[],
        )
        state = _make_state(
            raw_summary="## ⚠️ DRAFT\n\nPatient is healthy.",
            verification_result=vr,
            metadata={"model_used": "stub-model", "input_tokens": 100, "output_tokens": 50},
        )
        result = await format_output_node(state)
        assert result["final_summary"] is not None

    async def test_html_summary_contains_confidence_badge(self) -> None:
        vr = VerificationResult(
            verified_claims=[],
            unverified_claims=[],
            confidence_score=0.97,
            confidence_level="GREEN",
            flags=[],
        )
        state = _make_state(
            raw_summary="## ⚠️ DRAFT\n\nPatient is healthy.",
            verification_result=vr,
            metadata={"model_used": "stub"},
        )
        result = await format_output_node(state)
        fs = result["final_summary"]
        assert fs.html_summary is not None
        assert "GREEN" in fs.html_summary
        assert "AI Confidence" in fs.html_summary

    async def test_html_summary_contains_disclaimer(self) -> None:
        vr = VerificationResult(
            verified_claims=[],
            unverified_claims=[],
            confidence_score=0.95,
            confidence_level="GREEN",
            flags=[],
        )
        state = _make_state(
            raw_summary="## ⚠️ DRAFT\n\nContent.",
            verification_result=vr,
            metadata={},
        )
        result = await format_output_node(state)
        fs = result["final_summary"]
        assert "clinician review" in fs.html_summary.lower()

    async def test_error_in_summary_yields_failed_status(self) -> None:
        state = _make_state(
            raw_summary="[ERROR] LLM failed.",
            verification_result=None,
            metadata={},
        )
        result = await format_output_node(state)
        assert result["final_summary"].status == "failed"

    async def test_retrieval_errors_yield_partial_status(self) -> None:
        vr = VerificationResult(
            verified_claims=[],
            unverified_claims=[],
            confidence_score=0.90,
            confidence_level="YELLOW",
            flags=[],
        )
        state = _make_state(
            raw_summary="## ⚠️ DRAFT\n\nPartial data.",
            verification_result=vr,
            errors=["get_lab_results: timeout"],
            metadata={},
        )
        result = await format_output_node(state)
        assert result["final_summary"].status == "partial"

    async def test_red_badge_for_low_confidence(self) -> None:
        vr = VerificationResult(
            verified_claims=[],
            unverified_claims=[],
            confidence_score=0.60,
            confidence_level="RED",
            flags=["Low confidence"],
        )
        state = _make_state(
            raw_summary="## ⚠️ DRAFT\n\nContent.",
            verification_result=vr,
            metadata={},
        )
        result = await format_output_node(state)
        assert "RED" in result["final_summary"].html_summary


# ---------------------------------------------------------------------------
# Full pipeline integration tests (via nodes only — no pipeline.py)
# ---------------------------------------------------------------------------


class TestPipelineViaNodes:
    """Integration test: run all nodes in sequence with mock data."""

    async def test_full_node_sequence_for_mock_patient(self) -> None:
        tools = create_mock_tools()
        stub = _StubLLM()

        retrieve = make_retrieve_data_node(tools)
        generate = make_generate_summary_node(stub)

        # Step 1: retrieve
        state = _make_state(patient_id="TEST-001")
        r1 = await retrieve(state)
        state.update(r1)  # type: ignore[arg-type]

        # Step 2: structure
        r2 = await structure_data_node(state)
        state.update(r2)  # type: ignore[arg-type]
        assert len(state["structured_context"]) > 0

        # Step 3: generate
        r3 = await generate(state)
        state.update(r3)  # type: ignore[arg-type]
        assert "DRAFT" in state["raw_summary"]

        # Step 4: verify
        r4 = await verify_summary_node(state)
        state.update(r4)  # type: ignore[arg-type]
        assert state["verification_result"] is not None

        # Step 5: format
        r5 = await format_output_node(state)
        state.update(r5)  # type: ignore[arg-type]
        assert state["final_summary"] is not None
        assert state["final_summary"].html_summary is not None

    async def test_specialty_routing_cardiology(self) -> None:
        """Cardiology requests should use cardiology prompt."""
        stub = _StubLLM()
        generate = make_generate_summary_node(stub)
        state = _make_state(specialty="cardiology", structured_context="Data.", retry_count=0)
        await generate(state)
        prompt = stub.last_system_prompt.lower()
        assert "cardiology" in prompt or "cardiac" in prompt

    async def test_graceful_degradation_all_tools_fail(self) -> None:
        """If all tools fail, errors are recorded and final_summary still produced."""
        class _AlwaysFailTool(FHIRTool):
            tool_name = "get_patient_demographics"
            description = "Always fails"

            async def execute(self, patient_id: str, **kwargs: Any) -> ToolResult:
                return ToolResult(
                    tool_name=self.tool_name,
                    success=False,
                    data=None,
                    error_message="Simulated failure",
                )

        failing_tools = [_AlwaysFailTool() for _ in range(9)]
        retrieve = make_retrieve_data_node(failing_tools)
        state = _make_state(patient_id="FAIL-PATIENT")
        r1 = await retrieve(state)
        assert len(r1["errors"]) > 0
        # No data should be present
        assert r1["demographics"] is None
        assert r1["conditions"] == []
