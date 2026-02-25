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
Evaluation runner for the Chart Summarizer Agent.

Loads synthetic patient test cases from eval/datasets/synthetic_patients/,
generates summaries for each using the full pipeline (inline, no HTTP server
required), and scores them against expected outcomes.

Scoring:
  1. Completeness  — every expected_item keyword must appear in the summary.
  2. Factual accuracy — confidence_score from the pipeline verifier (>= threshold).
  3. Status         — response.status must be in expected_status.
  4. Safety gates   — cases marked is_safety_gate=true cause an immediate CI failure.

CI gate: exit code 1 if any case fails.

Usage:
    # Inline mode (default — no server required):
    python eval/scripts/run_eval.py

    # With explicit dataset / output dirs:
    python eval/scripts/run_eval.py \\
        --dataset-dir eval/datasets/synthetic_patients \\
        --output-dir  eval/results

    # Override accuracy threshold:
    python eval/scripts/run_eval.py --accuracy-threshold 0.90

Environment variables:
    EVAL_USE_REAL_LLM=true   — use the configured LLM provider instead of
                               the built-in deterministic stub (requires
                               LLM_API_KEY to be set).
"""

import argparse
import asyncio
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Optional ROUGE-L scoring (graceful degradation if rouge-score not installed).
try:
    from rouge_score import rouge_scorer as _rouge_scorer_lib  # type: ignore[import]
    _ROUGE_AVAILABLE = True
except ImportError:
    _ROUGE_AVAILABLE = False

# ---------------------------------------------------------------------------
# Make the chart_summarizer package importable when this script is run
# directly from the repo root without installation.
# ---------------------------------------------------------------------------
_AGENT_SRC = Path(__file__).parent.parent.parent / "agent" / "src"
if _AGENT_SRC.is_dir() and str(_AGENT_SRC) not in sys.path:
    sys.path.insert(0, str(_AGENT_SRC))


from chart_summarizer.graph.pipeline import create_pipeline  # noqa: E402
from chart_summarizer.llm.base import LLMProvider, LLMResponse  # noqa: E402
from chart_summarizer.models.summary import SummaryRequest, SummaryResponse  # noqa: E402
from chart_summarizer.services.summary_service import SummaryService  # noqa: E402
from chart_summarizer.tools.mock import create_mock_tools  # noqa: E402


# ---------------------------------------------------------------------------
# Inline deterministic stub LLM
# ---------------------------------------------------------------------------

# Matches the [RECORD-ID] prefix embedded by _format_patient_data()
_RECORD_ID_RE = re.compile(r"^\[([A-Z]{2,4}-\d{3}-\d{2,})\](.+)$", re.MULTILINE)

_CONFIDENCE_RANK = {"RED": 0, "YELLOW": 1, "GREEN": 2}


class _EvalLLM(LLMProvider):
    """
    Deterministic inline stub LLM for CI eval runs without a real API key.

    Reads the formatted patient data embedded in the user message and generates
    a DRAFT summary that cites every record with [Source: <record-id>].  This
    makes the pipeline verifier confirm all citations -> high confidence score.
    """

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
        raise NotImplementedError("_EvalLLM does not support structured generation.")


# ---------------------------------------------------------------------------
# Test case loading
# ---------------------------------------------------------------------------


def load_test_cases(dataset_dir: Path) -> list[dict[str, Any]]:
    """
    Load all synthetic patient test cases from the dataset directory.

    Each test case is a JSON file with these keys:
      case_id           : unique identifier
      description       : human-readable description of the test
      summary_request   : SummaryRequest fields (patient_id, specialty, etc.)
      expected_items    : dict[category -> list[keyword]] — must appear in summary
      min_confidence_level : "GREEN" | "YELLOW" | null (skip confidence check)
      expected_status   : list of acceptable status values
      is_safety_gate    : bool (optional) — failure is an immediate CI blocker

    Args:
        dataset_dir: Directory containing *.json test case files.

    Returns:
        List of test case dicts, sorted by case_id.

    Raises:
        FileNotFoundError: If dataset_dir does not exist.
        ValueError: If any JSON file is malformed.
    """
    if not dataset_dir.is_dir():
        raise FileNotFoundError(f"Dataset directory not found: {dataset_dir}")

    cases: list[dict[str, Any]] = []
    for path in sorted(dataset_dir.glob("*.json")):
        try:
            with path.open(encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Malformed JSON in {path.name}: {exc}") from exc

        if "case_id" not in data or "summary_request" not in data:
            raise ValueError(
                f"{path.name} is missing required keys 'case_id' or 'summary_request'."
            )
        cases.append(data)

    return cases


# ---------------------------------------------------------------------------
# Scoring functions
# ---------------------------------------------------------------------------


def score_completeness(
    summary_text: str,
    expected_items: dict[str, list[str]],
) -> dict[str, bool]:
    """
    Check that every expected keyword appears in the generated summary.

    Args:
        summary_text:   Generated summary (case-insensitive search).
        expected_items: Mapping of category name -> list of required keywords.

    Returns:
        Dict mapping "<category>:<keyword>" -> True/False (present/absent).
    """
    lower = summary_text.lower()
    results: dict[str, bool] = {}
    for category, keywords in expected_items.items():
        for keyword in keywords:
            key = f"{category}:{keyword}"
            results[key] = keyword.lower() in lower
    return results


def score_rouge_l(generated: str, reference: str) -> float:
    """
    Compute ROUGE-L F1 score between generated and gold-standard summaries.

    Returns -1.0 (sentinel) if rouge-score is not installed or reference is empty.
    """
    if not _ROUGE_AVAILABLE or not reference or not generated:
        return -1.0
    scorer = _rouge_scorer_lib.RougeScorer(["rougeL"], use_stemmer=True)
    return scorer.score(reference, generated)["rougeL"].fmeasure


def score_hallucination_check(
    summary_text: str,
    must_not_appear: list[str],
) -> tuple[float, list[str]]:
    """
    Verify no must_not_appear phrases are present in the generated summary.

    Returns (score, found_list). score = 1.0 if clean, penalised per hallucination.
    """
    if not must_not_appear:
        return 1.0, []
    lower = summary_text.lower()
    found = [fact for fact in must_not_appear if fact.lower() in lower]
    score = max(0.0, 1.0 - len(found) / max(1, len(must_not_appear)))
    return score, found


def score_factual_accuracy(response: SummaryResponse) -> float:
    """
    Return the pipeline verifier's confidence_score (0.0-1.0).

    The verifier already checked every [Source: <id>] citation against the
    actual patient data records — we reuse its output rather than re-running.

    Args:
        response: SummaryResponse from the pipeline.

    Returns:
        Float 0.0-1.0.  Returns 1.0 if no citations were made.
    """
    return response.verification_result.confidence_score


# ---------------------------------------------------------------------------
# Per-case evaluation
# ---------------------------------------------------------------------------


async def evaluate_case_inline(
    test_case: dict[str, Any],
    llm_provider: Optional[LLMProvider] = None,
    accuracy_threshold: float = 0.95,
) -> dict[str, Any]:
    """
    Run a single test case through the full pipeline (in-process, no HTTP).

    Args:
        test_case:          Test case dict from load_test_cases().
        llm_provider:       LLM to use.  Defaults to _EvalLLM (stub).
        accuracy_threshold: Minimum confidence_score to pass the accuracy check.

    Returns:
        Dict with: case_id, passed, accuracy, completeness_results,
                   completeness_passed, status, confidence_level, flags,
                   error (if the pipeline raised unexpectedly).
    """
    case_id: str = test_case["case_id"]
    is_safety_gate: bool = test_case.get("is_safety_gate", False)

    llm = llm_provider or _EvalLLM()
    pipeline = create_pipeline(tools=create_mock_tools(), llm_provider=llm)
    service = SummaryService(pipeline=pipeline)

    req_data: dict[str, Any] = test_case["summary_request"]
    try:
        request = SummaryRequest(**req_data)
    except Exception as exc:
        return {
            "case_id": case_id,
            "passed": False,
            "error": f"Invalid SummaryRequest: {exc}",
            "is_safety_gate": is_safety_gate,
        }

    t0 = time.monotonic()
    error: Optional[str] = None
    response: Optional[SummaryResponse] = None

    try:
        response = await service.generate_summary(request)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"

    latency_ms = int((time.monotonic() - t0) * 1000)

    if error or response is None:
        return {
            "case_id": case_id,
            "passed": False,
            "error": error or "No response returned.",
            "latency_ms": latency_ms,
            "is_safety_gate": is_safety_gate,
        }

    # --- Score completeness ---
    expected_items: dict[str, list[str]] = test_case.get("expected_items", {})
    completeness_results = score_completeness(response.summary_text, expected_items)
    completeness_passed = all(completeness_results.values())

    # --- Score factual accuracy ---
    accuracy = score_factual_accuracy(response)
    accuracy_passed = accuracy >= accuracy_threshold

    # --- Hallucination detection (must_not_appear) ---
    must_not_appear: list[str] = test_case.get("must_not_appear", [])
    hallucination_score, hallucinated_found = score_hallucination_check(
        response.summary_text, must_not_appear
    )
    hallucination_passed = hallucination_score >= 1.0  # clean = no hits

    # --- ROUGE-L similarity to gold standard ---
    gold_standard: str = test_case.get("gold_standard_summary", "")
    rouge_l = score_rouge_l(response.summary_text, gold_standard)

    # --- Check status ---
    expected_statuses: list[str] = test_case.get("expected_status", ["complete", "partial"])
    status_passed = response.status in expected_statuses

    # --- Check confidence level ---
    min_level: Optional[str] = test_case.get("min_confidence_level")
    if min_level is not None:
        actual_rank = _CONFIDENCE_RANK.get(response.confidence_level, 0)
        required_rank = _CONFIDENCE_RANK.get(min_level, 0)
        confidence_passed = actual_rank >= required_rank
    else:
        confidence_passed = True

    passed = (
        completeness_passed
        and accuracy_passed
        and status_passed
        and confidence_passed
        and (hallucination_passed or not must_not_appear)  # only gate if list was provided
    )

    result: dict[str, Any] = {
        "case_id": case_id,
        "description": test_case.get("description", ""),
        "passed": passed,
        "accuracy": round(accuracy, 4),
        "accuracy_passed": accuracy_passed,
        "accuracy_threshold": accuracy_threshold,
        "completeness_results": completeness_results,
        "completeness_passed": completeness_passed,
        "status": response.status,
        "status_passed": status_passed,
        "confidence_level": response.confidence_level,
        "confidence_passed": confidence_passed,
        "min_confidence_level": min_level,
        "latency_ms": latency_ms,
        "is_safety_gate": is_safety_gate,
        "flags": response.verification_result.flags,
        # New Prompt5 metrics
        "hallucination_score": round(hallucination_score, 4),
        "hallucination_passed": hallucination_passed,
        "hallucinated_phrases": hallucinated_found,
    }
    if rouge_l >= 0:
        result["rouge_l"] = round(rouge_l, 4)
    return result


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def write_results(results: list[dict[str, Any]], output_dir: Path) -> None:
    """
    Write evaluation results to the output directory.

    Outputs:
      results.json — structured results for CI / LangSmith parsing
      report.txt   — human-readable pass/fail table

    Args:
        results:    List of per-case result dicts.
        output_dir: Directory where output files will be written.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    json_path = output_dir / "results.json"
    json_path.write_text(
        json.dumps(results, indent=2, default=str),
        encoding="utf-8",
    )

    total = len(results)
    n_passed = sum(1 for r in results if r.get("passed"))
    n_failed = total - n_passed
    safety_failures = [
        r for r in results if not r.get("passed") and r.get("is_safety_gate")
    ]

    lines: list[str] = [
        "=" * 70,
        "  CHART SUMMARIZER AGENT — EVALUATION REPORT",
        "=" * 70,
        f"  Total cases        : {total}",
        f"  Passed             : {n_passed}",
        f"  Failed             : {n_failed}",
        f"  Safety gate fails  : {len(safety_failures)}",
        "=" * 70,
        "",
    ]

    for r in results:
        icon = "PASS" if r.get("passed") else "FAIL"
        gate = " [SAFETY GATE]" if r.get("is_safety_gate") and not r.get("passed") else ""
        lines.append(f"[{icon}] {r.get('case_id', '?')}{gate}")
        desc = r.get("description", "")
        if desc:
            lines.append(f"      {desc}")

        if "error" in r:
            lines.append(f"      ERROR: {r['error']}")
        else:
            acc = r.get("accuracy", 0.0)
            hall = r.get("hallucination_score", -1.0)
            rouge = r.get("rouge_l", -1.0)
            metrics_str = (
                f"Accuracy={acc:.2%}  "
                f"Status={r.get('status', '?')}  "
                f"Confidence={r.get('confidence_level', '?')}"
            )
            if hall >= 0:
                metrics_str += f"  Hallucination={hall:.2%}"
            if rouge >= 0:
                metrics_str += f"  ROUGE-L={rouge:.3f}"
            lines.append(f"      {metrics_str}")
            missing = [k for k, v in r.get("completeness_results", {}).items() if not v]
            if missing:
                lines.append(f"      MISSING ITEMS: {', '.join(missing)}")
            hallucinated = r.get("hallucinated_phrases", [])
            if hallucinated:
                lines.append(f"      HALLUCINATIONS: {', '.join(hallucinated)}")
            flags = r.get("flags", [])
            if flags:
                lines.append(f"      FLAGS: {'; '.join(flags)}")
        lines.append("")

    lines += ["=" * 70, "  END OF REPORT", "=" * 70]

    report_path = output_dir / "report.txt"
    report_path.write_text("\n".join(lines), encoding="utf-8")

    # Append to history.jsonl for trend tracking over time.
    history_path = output_dir / "history.jsonl"
    history_record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total": total,
        "passed": n_passed,
        "failed": n_failed,
        "safety_gate_failures": len(safety_failures),
        "results": results,
    }
    with history_path.open("a", encoding="utf-8") as hf:
        hf.write(json.dumps(history_record, default=str) + "\n")

    print(f"Results written to: {json_path}")
    print(f"Report written to:  {report_path}")
    print(f"History updated:    {history_path}")


# ---------------------------------------------------------------------------
# Async runner helper
# ---------------------------------------------------------------------------


async def _run_all(
    test_cases: list[dict[str, Any]],
    llm_provider: LLMProvider,
    accuracy_threshold: float,
) -> list[dict[str, Any]]:
    """Run all cases concurrently and return results in original order."""
    tasks = [
        evaluate_case_inline(case, llm_provider, accuracy_threshold)
        for case in test_cases
    ]
    return list(await asyncio.gather(*tasks))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    """CLI entry point.  Returns 0 on pass, 1 on failure (CI gate)."""
    parser = argparse.ArgumentParser(
        description="Evaluate the Chart Summarizer Agent against synthetic test cases."
    )
    parser.add_argument(
        "--dataset-dir",
        type=Path,
        default=Path(__file__).parent.parent / "datasets" / "synthetic_patients",
        help="Directory containing synthetic patient JSON test cases.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).parent.parent / "results",
        help="Directory to write evaluation results.",
    )
    parser.add_argument(
        "--accuracy-threshold",
        type=float,
        default=0.95,
        help="Minimum factual accuracy (confidence_score) to pass CI (default: 0.95).",
    )
    args = parser.parse_args()

    print(f"Loading test cases from : {args.dataset_dir}")
    print(f"Accuracy threshold      : {args.accuracy_threshold:.0%}")
    print()

    try:
        test_cases = load_test_cases(args.dataset_dir)
    except (FileNotFoundError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    print(f"Loaded {len(test_cases)} test case(s).\n")

    use_real_llm = os.environ.get("EVAL_USE_REAL_LLM", "").lower() == "true"

    if use_real_llm:
        from chart_summarizer.llm.factory import create_llm_provider  # type: ignore[import]
        llm_provider: LLMProvider = create_llm_provider()
        print(f"LLM: {llm_provider.model_name} (real)\n")
    else:
        llm_provider = _EvalLLM()
        print("LLM: eval-stub (deterministic, no API key needed)\n")

    results = asyncio.run(_run_all(test_cases, llm_provider, args.accuracy_threshold))

    write_results(results, args.output_dir)
    print()

    total = len(results)
    n_passed = sum(1 for r in results if r.get("passed"))
    safety_failures = [
        r for r in results if not r.get("passed") and r.get("is_safety_gate")
    ]

    for r in results:
        icon = "PASS" if r.get("passed") else "FAIL"
        gate = " [SAFETY GATE]" if r.get("is_safety_gate") and not r.get("passed") else ""
        print(f"  [{icon}] {r['case_id']}{gate}")

    print(f"\nResults: {n_passed}/{total} passed.")

    if safety_failures:
        print(
            f"\n[CI FAIL] {len(safety_failures)} safety gate(s) failed — "
            "these are blockers regardless of the overall pass rate."
        )
        return 1

    if n_passed < total:
        print("[CI FAIL] One or more test cases failed.")
        return 1

    print("[CI PASS] All test cases passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
