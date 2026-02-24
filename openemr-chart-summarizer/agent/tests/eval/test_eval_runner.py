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
Eval test suite — 6 test cases with defined expected outcomes.

Satisfies MVPReq: "Simple evaluation: 5+ test cases with expected outcomes."

Each case drives the full LangGraph pipeline (retrieve -> structure ->
summarize -> verify) using mock FHIR tools and the deterministic _EvalLLM
stub.  No LLM API key or running server is required.

Grading per case:
  - Summary is returned without crashing (SummaryResponse)
  - All expected keywords present in summary text (completeness)
  - confidence_level meets minimum requirement
  - response.status is in expected set
  - Factual accuracy (confidence_score) above threshold
"""

import re
import sys
from pathlib import Path
from typing import Any, Optional

import pytest

from chart_summarizer.graph.pipeline import create_pipeline
from chart_summarizer.llm.base import LLMProvider, LLMResponse
from chart_summarizer.models.summary import SummaryRequest, SummaryResponse
from chart_summarizer.services.summary_service import SummaryService
from chart_summarizer.tools.mock import create_mock_tools

# ---------------------------------------------------------------------------
# Inline _EvalLLM (mirrors eval/scripts/run_eval.py — avoids cross-package import)
# ---------------------------------------------------------------------------

_RECORD_ID_RE = re.compile(r"^\[([A-Z]{2,4}-\d{3}-\d{2,})\](.+)$", re.MULTILINE)
_CONFIDENCE_RANK = {"RED": 0, "YELLOW": 1, "GREEN": 2}


class _EvalLLM(LLMProvider):
    """Deterministic stub: generates a DRAFT summary citing every record found in context."""

    @property
    def model_name(self) -> str:
        return "eval-stub"

    @property
    def supports_tool_calling(self) -> bool:
        return False

    @property
    def max_context_window(self) -> int:
        return 128_000

    async def generate(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
    ) -> LLMResponse:
        user_content = "\n".join(str(m.get("content", "")) for m in messages)
        records = _RECORD_ID_RE.findall(user_content)

        lines: list[str] = [
            "## \u26a0\ufe0f DRAFT \u2014 AI-GENERATED \u2014 REQUIRES CLINICIAN REVIEW",
            "",
        ]
        if records:
            for record_id, description in records:
                lines.append(f"- {description.strip()} [Source: {record_id}]")
        else:
            lines.append("No structured patient data was available for this summary.")

        content = "\n".join(lines)
        return LLMResponse(
            content=content,
            model="eval-stub",
            input_tokens=max(1, len(user_content) // 4),
            output_tokens=max(1, len(content) // 4),
        )

    async def generate_structured(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        response_model: Any,
    ) -> Any:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Test case definitions
# ---------------------------------------------------------------------------

_EVAL_CASES = [
    {
        "case_id": "eval-001-simple-adult",
        "description": "Jane Doe (TEST-001) — all sections — primary care",
        "patient_id": "TEST-001",
        "specialty": "primary_care",
        "sections": [
            "demographics", "conditions", "medications", "allergies",
            "labs", "vitals", "encounters", "immunizations",
        ],
        "must_mention": ["penicillin", "lisinopril", "hypertension"],
        "expected_status": ["complete", "partial"],
        "min_confidence_level": "YELLOW",
        "min_accuracy": 0.90,
    },
    {
        "case_id": "eval-002-complex-elderly",
        "description": "Robert Smith (TEST-002) — cardiology — polypharmacy",
        "patient_id": "TEST-002",
        "specialty": "cardiology",
        "sections": [
            "demographics", "conditions", "medications", "allergies",
            "labs", "vitals", "encounters",
        ],
        "must_mention": ["diabetes", "hypertension", "metformin", "furosemide"],
        "expected_status": ["complete", "partial"],
        "min_confidence_level": "YELLOW",
        "min_accuracy": 0.90,
    },
    {
        "case_id": "eval-003-pediatric",
        "description": "Emma Wilson (TEST-003) — pediatrics — asthma",
        "patient_id": "TEST-003",
        "specialty": "pediatrics",
        "sections": [
            "demographics", "conditions", "medications", "allergies",
            "immunizations", "vitals",
        ],
        "must_mention": ["asthma", "albuterol"],
        "expected_status": ["complete", "partial"],
        "min_confidence_level": "YELLOW",
        "min_accuracy": 0.90,
    },
    {
        "case_id": "eval-004-allergy-safety",
        "description": "SAFETY GATE — Penicillin allergy must surface in narrow section request",
        "patient_id": "TEST-001",
        "specialty": "primary_care",
        "sections": ["allergies", "medications"],
        "must_mention": ["penicillin", "lisinopril"],
        "expected_status": ["complete", "partial"],
        "min_confidence_level": "YELLOW",
        "min_accuracy": 0.90,
        "is_safety_gate": True,
    },
    {
        "case_id": "eval-005-partial-sections",
        "description": (
            "TEST-002 with only conditions + medications — section filter correctness. "
            "Confidence will be RED (allergies excluded from request) — that is correct."
        ),
        "patient_id": "TEST-002",
        "specialty": "internal_medicine",
        "sections": ["conditions", "medications"],
        "must_mention": ["diabetes", "hypertension", "metformin"],
        "expected_status": ["complete", "partial"],
        # Confidence is RED by design — the verifier correctly flags that active allergies
        # are absent from the summary because allergies were not a requested section.
        "min_confidence_level": None,
        "min_accuracy": 0.0,
    },
    {
        "case_id": "eval-006-unknown-patient",
        "description": "RESILIENCE TEST — unknown patient must fail gracefully, not crash",
        "patient_id": "UNKNOWN-999",
        "specialty": "primary_care",
        "sections": ["demographics", "conditions", "medications", "allergies"],
        "must_mention": [],
        "expected_status": ["failed", "partial"],
        "min_confidence_level": None,  # no confidence requirement on failure cases
        "min_accuracy": 0.0,
    },
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_service() -> SummaryService:
    pipeline = create_pipeline(tools=create_mock_tools(), llm_provider=_EvalLLM())
    return SummaryService(pipeline=pipeline)


def _confidence_rank(level: str) -> int:
    return _CONFIDENCE_RANK.get(level, 0)


# ---------------------------------------------------------------------------
# Parametrised eval tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", _EVAL_CASES, ids=[c["case_id"] for c in _EVAL_CASES])
async def test_eval_case(case: dict[str, Any]) -> None:
    """
    End-to-end eval: run the full pipeline and assert all expected outcomes.

    For each case:
    - Summary is returned (no unhandled exception)
    - All must_mention keywords appear in the summary text
    - response.status is in expected_status
    - confidence_level meets min_confidence_level (when set)
    - confidence_score meets min_accuracy
    """
    service = _make_service()

    request = SummaryRequest(
        patient_id=case["patient_id"],
        specialty=case["specialty"],
        requested_sections=case["sections"],
    )

    result = await service.generate_summary(request)

    assert isinstance(result, SummaryResponse), "Pipeline must return a SummaryResponse"

    # --- Status ---
    assert result.status in case["expected_status"], (
        f"[{case['case_id']}] status={result.status!r} "
        f"not in {case['expected_status']}"
    )

    # --- Completeness ---
    summary_lower = result.summary_text.lower()
    for keyword in case["must_mention"]:
        assert keyword.lower() in summary_lower, (
            f"[{case['case_id']}] Expected keyword {keyword!r} not found in summary.\n"
            f"Summary excerpt:\n{result.summary_text[:500]}"
        )

    # --- Confidence level ---
    if case["min_confidence_level"] is not None:
        actual_rank = _confidence_rank(result.confidence_level)
        required_rank = _confidence_rank(case["min_confidence_level"])
        assert actual_rank >= required_rank, (
            f"[{case['case_id']}] confidence_level={result.confidence_level!r} "
            f"below minimum {case['min_confidence_level']!r}"
        )

    # --- Factual accuracy ---
    score = result.verification_result.confidence_score
    assert score >= case["min_accuracy"], (
        f"[{case['case_id']}] confidence_score={score:.2%} "
        f"below minimum {case['min_accuracy']:.0%}"
    )


# ---------------------------------------------------------------------------
# Structural tests for the eval infrastructure itself
# ---------------------------------------------------------------------------


class TestEvalInfrastructure:
    def test_at_least_five_eval_cases_defined(self) -> None:
        assert len(_EVAL_CASES) >= 5

    def test_all_case_ids_are_unique(self) -> None:
        ids = [c["case_id"] for c in _EVAL_CASES]
        assert len(ids) == len(set(ids))

    def test_all_cases_have_required_keys(self) -> None:
        required = {"case_id", "patient_id", "specialty", "sections", "expected_status"}
        for case in _EVAL_CASES:
            missing = required - set(case.keys())
            assert not missing, f"{case['case_id']} is missing keys: {missing}"

    def test_safety_gate_case_exists(self) -> None:
        safety_cases = [c for c in _EVAL_CASES if c.get("is_safety_gate")]
        assert len(safety_cases) >= 1, "At least one safety gate case required"

    def test_unknown_patient_case_exists(self) -> None:
        resilience_cases = [c for c in _EVAL_CASES if "unknown" in c["case_id"]]
        assert len(resilience_cases) >= 1, "At least one resilience (unknown patient) case required"

    async def test_eval_llm_generates_draft_header(self) -> None:
        llm = _EvalLLM()
        resp = await llm.generate(
            system_prompt="",
            messages=[{"role": "user", "content": "[MED-001-01] Lisinopril 10 mg | active"}],
        )
        assert "DRAFT" in resp.content

    async def test_eval_llm_cites_records(self) -> None:
        llm = _EvalLLM()
        resp = await llm.generate(
            system_prompt="",
            messages=[{"role": "user", "content": "[ALG-001-01] Penicillin | active\n[MED-001-01] Lisinopril | active"}],
        )
        assert "[Source: ALG-001-01]" in resp.content
        assert "[Source: MED-001-01]" in resp.content

    async def test_eval_llm_handles_no_records(self) -> None:
        llm = _EvalLLM()
        resp = await llm.generate(
            system_prompt="",
            messages=[{"role": "user", "content": "No FHIR data available."}],
        )
        assert "DRAFT" in resp.content
        assert isinstance(resp.content, str)
