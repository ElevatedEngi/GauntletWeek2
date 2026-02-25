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
Compare two Chart Summarizer evaluation runs and highlight regressions.

A regression is any metric that dropped by more than REGRESSION_THRESHOLD
(default 5%) between run A (baseline) and run B (current).

Usage:
    # Compare two results.json files directly:
    python compare_runs.py \\
        --run-a eval/results/baseline/results.json \\
        --run-b eval/results/current/results.json

    # Or compare result directories (looks for results.json inside):
    python compare_runs.py \\
        --run-a eval/results/run_20260101/ \\
        --run-b eval/results/run_20260201/ \\
        --output comparison.md

    # Custom regression threshold:
    python compare_runs.py --run-a ... --run-b ... --threshold 0.03
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REGRESSION_THRESHOLD = 0.05  # 5% drop flags a regression


# ---------------------------------------------------------------------------
# Loading helpers
# ---------------------------------------------------------------------------

def load_results(path: Path) -> list[dict[str, Any]]:
    """
    Load a results.json file.

    Accepts either a direct path to results.json or a directory containing one.

    Args:
        path: Path to results.json or its parent directory.

    Returns:
        List of per-case result dicts.

    Raises:
        FileNotFoundError: If results.json is not found.
        ValueError: If the file is not valid JSON or not a list.
    """
    if path.is_dir():
        path = path / "results.json"

    if not path.exists():
        raise FileNotFoundError(f"Results file not found: {path}")

    with path.open(encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError(f"Expected a JSON list in {path}, got {type(data).__name__}")

    return data


# ---------------------------------------------------------------------------
# Metric extraction
# ---------------------------------------------------------------------------

def _get_metrics(result: dict[str, Any]) -> dict[str, float]:
    """Extract comparable numeric metrics from a single case result."""
    completeness_results = result.get("completeness_results", {})
    if completeness_results:
        n_pass = sum(1 for v in completeness_results.values() if v)
        completeness = n_pass / max(1, len(completeness_results))
    else:
        completeness = 1.0 if result.get("completeness_passed", True) else 0.0

    return {
        "accuracy": float(result.get("accuracy", 1.0)),
        "completeness": completeness,
        "passed": 1.0 if result.get("passed") else 0.0,
        "rouge_l": float(result.get("rouge_l", -1.0)),
        "hallucination_score": float(result.get("hallucination_score", -1.0)),
    }


def _aggregate(results: list[dict[str, Any]]) -> dict[str, float]:
    """Compute aggregate metrics across all cases."""
    if not results:
        return {}

    total = len(results)
    passed = sum(1 for r in results if r.get("passed"))
    accuracies = [r["accuracy"] for r in results if "accuracy" in r]
    rouges = [r["rouge_l"] for r in results if r.get("rouge_l", -1.0) >= 0]
    hall = [r["hallucination_score"] for r in results if r.get("hallucination_score", -1.0) >= 0]

    agg: dict[str, float] = {
        "pass_rate": passed / total,
        "total_cases": float(total),
        "passed_cases": float(passed),
    }
    if accuracies:
        agg["mean_accuracy"] = sum(accuracies) / len(accuracies)
    if rouges:
        agg["mean_rouge_l"] = sum(rouges) / len(rouges)
    if hall:
        agg["mean_hallucination_score"] = sum(hall) / len(hall)
    return agg


# ---------------------------------------------------------------------------
# Comparison logic
# ---------------------------------------------------------------------------

def compare_runs(
    results_a: list[dict[str, Any]],
    results_b: list[dict[str, Any]],
    threshold: float = REGRESSION_THRESHOLD,
) -> dict[str, Any]:
    """
    Compare two eval runs and return a structured comparison report.

    Args:
        results_a:  Baseline run (the older/reference run).
        results_b:  Current run (the one being evaluated for regressions).
        threshold:  Minimum drop (0.05 = 5%) that flags a regression.

    Returns:
        Dict with keys:
          - aggregate_a, aggregate_b : overall metrics for each run
          - per_case                 : list of per-case comparisons
          - regressions              : list of detected regressions
          - improvements            : list of detected improvements
          - has_regressions         : bool
    """
    index_a = {r["case_id"]: r for r in results_a}
    index_b = {r["case_id"]: r for r in results_b}
    common_ids = sorted(set(index_a) & set(index_b))
    only_a = sorted(set(index_a) - set(index_b))
    only_b = sorted(set(index_b) - set(index_a))

    per_case: list[dict[str, Any]] = []
    regressions: list[dict[str, Any]] = []
    improvements: list[dict[str, Any]] = []

    for case_id in common_ids:
        ra = index_a[case_id]
        rb = index_b[case_id]
        ma = _get_metrics(ra)
        mb = _get_metrics(rb)

        case_regressions: list[dict[str, str]] = []
        case_improvements: list[dict[str, str]] = []

        for metric in ("accuracy", "completeness", "passed", "rouge_l", "hallucination_score"):
            va = ma.get(metric, -1.0)
            vb = mb.get(metric, -1.0)
            if va < 0 or vb < 0:
                continue  # metric not available in both runs
            delta = vb - va
            if delta < -threshold:
                case_regressions.append({
                    "metric": metric,
                    "run_a": f"{va:.4f}",
                    "run_b": f"{vb:.4f}",
                    "delta": f"{delta:+.4f}",
                })
                regressions.append({
                    "case_id": case_id,
                    "metric": metric,
                    "run_a": va,
                    "run_b": vb,
                    "delta": delta,
                })
            elif delta > threshold:
                case_improvements.append({
                    "metric": metric,
                    "run_a": f"{va:.4f}",
                    "run_b": f"{vb:.4f}",
                    "delta": f"{delta:+.4f}",
                })
                improvements.append({
                    "case_id": case_id,
                    "metric": metric,
                    "run_a": va,
                    "run_b": vb,
                    "delta": delta,
                })

        per_case.append({
            "case_id": case_id,
            "passed_a": ra.get("passed", False),
            "passed_b": rb.get("passed", False),
            "metrics_a": {k: round(v, 4) for k, v in ma.items() if v >= 0},
            "metrics_b": {k: round(v, 4) for k, v in mb.items() if v >= 0},
            "regressions": case_regressions,
            "improvements": case_improvements,
        })

    return {
        "aggregate_a": _aggregate(results_a),
        "aggregate_b": _aggregate(results_b),
        "per_case": per_case,
        "regressions": regressions,
        "improvements": improvements,
        "only_in_a": only_a,
        "only_in_b": only_b,
        "has_regressions": len(regressions) > 0,
        "threshold": threshold,
    }


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _format_text(comparison: dict[str, Any], run_a_label: str, run_b_label: str) -> str:
    """Format comparison as a human-readable text table."""
    lines: list[str] = []
    agg_a = comparison["aggregate_a"]
    agg_b = comparison["aggregate_b"]
    threshold = comparison["threshold"]

    def _row(label: str, key: str) -> str:
        va = agg_a.get(key, float("nan"))
        vb = agg_b.get(key, float("nan"))
        delta = vb - va if (va == va and vb == vb) else float("nan")  # nan check
        flag = " ⬇ REGRESSION" if delta < -threshold else (" ⬆ improved" if delta > threshold else "")
        return f"  {label:<30} {va:>8.1%}   {vb:>8.1%}   {delta:>+8.1%}  {flag}"

    lines += [
        "=" * 70,
        "  CHART SUMMARIZER — EVAL COMPARISON REPORT",
        "=" * 70,
        f"  Baseline (A) : {run_a_label}",
        f"  Current  (B) : {run_b_label}",
        f"  Threshold    : {threshold:.0%} drop = regression",
        "",
        f"  {'AGGREGATE METRIC':<30} {'Run A':>8}   {'Run B':>8}   {'Delta':>8}",
        "  " + "-" * 60,
        _row("Pass rate", "pass_rate"),
    ]
    if "mean_accuracy" in agg_a or "mean_accuracy" in agg_b:
        lines.append(_row("Mean accuracy", "mean_accuracy"))
    if "mean_rouge_l" in agg_a or "mean_rouge_l" in agg_b:
        lines.append(_row("Mean ROUGE-L", "mean_rouge_l"))
    if "mean_hallucination_score" in agg_a or "mean_hallucination_score" in agg_b:
        lines.append(_row("Mean hallucination score", "mean_hallucination_score"))

    lines += ["", "=" * 70, "  PER-CASE SUMMARY", "=" * 70]
    for case in comparison["per_case"]:
        pa = "PASS" if case["passed_a"] else "FAIL"
        pb = "PASS" if case["passed_b"] else "FAIL"
        change = ""
        if pa == "PASS" and pb == "FAIL":
            change = " ← REGRESSION"
        elif pa == "FAIL" and pb == "PASS":
            change = " ← fixed"
        lines.append(f"  {case['case_id']:<40} A={pa}  B={pb}{change}")
        for reg in case.get("regressions", []):
            lines.append(
                f"    ⬇ {reg['metric']}: {reg['run_a']} → {reg['run_b']} ({reg['delta']})"
            )

    if comparison["only_in_a"]:
        lines += ["", f"  Cases only in A (removed): {', '.join(comparison['only_in_a'])}"]
    if comparison["only_in_b"]:
        lines += [f"  Cases only in B (new):     {', '.join(comparison['only_in_b'])}"]

    lines += ["", "=" * 70]
    if comparison["has_regressions"]:
        lines.append(f"  [REGRESSIONS DETECTED] {len(comparison['regressions'])} metric(s) dropped >{threshold:.0%}")
    else:
        lines.append("  [NO REGRESSIONS] All comparable metrics are stable or improved.")
    lines.append("=" * 70)

    return "\n".join(lines)


def _format_markdown(comparison: dict[str, Any], run_a_label: str, run_b_label: str) -> str:
    """Format comparison as a GitHub-flavoured Markdown report."""
    agg_a = comparison["aggregate_a"]
    agg_b = comparison["aggregate_b"]
    threshold = comparison["threshold"]

    def _pct(v: float) -> str:
        return f"{v:.1%}" if v == v else "—"

    def _delta(key: str) -> str:
        va = agg_a.get(key, float("nan"))
        vb = agg_b.get(key, float("nan"))
        if va != va or vb != vb:
            return "—"
        d = vb - va
        icon = " ⬇️" if d < -threshold else (" ⬆️" if d > threshold else ""
        )
        return f"`{d:+.1%}`{icon}"

    lines = [
        "# Eval Comparison Report",
        "",
        f"| | **Baseline (A)** | **Current (B)** | **Delta** |",
        "|---|---|---|---|",
        f"| Run | `{run_a_label}` | `{run_b_label}` | |",
        f"| Pass rate | {_pct(agg_a.get('pass_rate', float('nan')))} | "
        f"{_pct(agg_b.get('pass_rate', float('nan')))} | {_delta('pass_rate')} |",
        f"| Mean accuracy | {_pct(agg_a.get('mean_accuracy', float('nan')))} | "
        f"{_pct(agg_b.get('mean_accuracy', float('nan')))} | {_delta('mean_accuracy')} |",
        f"| Mean ROUGE-L | {_pct(agg_a.get('mean_rouge_l', float('nan')))} | "
        f"{_pct(agg_b.get('mean_rouge_l', float('nan')))} | {_delta('mean_rouge_l')} |",
        "",
    ]

    if comparison["has_regressions"]:
        lines += [
            f"## ⬇️ Regressions Detected ({len(comparison['regressions'])})",
            "",
            "| Case | Metric | Run A | Run B | Delta |",
            "|---|---|---|---|---|",
        ]
        for reg in comparison["regressions"]:
            lines.append(
                f"| `{reg['case_id']}` | {reg['metric']} "
                f"| {reg['run_a']:.4f} | {reg['run_b']:.4f} | `{reg['delta']:+.4f}` |"
            )
        lines.append("")
    else:
        lines += ["## ✅ No Regressions Detected", ""]

    if comparison["improvements"]:
        lines += [
            f"## ⬆️ Improvements ({len(comparison['improvements'])})",
            "",
            "| Case | Metric | Run A | Run B | Delta |",
            "|---|---|---|---|---|",
        ]
        for imp in comparison["improvements"]:
            lines.append(
                f"| `{imp['case_id']}` | {imp['metric']} "
                f"| {imp['run_a']:.4f} | {imp['run_b']:.4f} | `{imp['delta']:+.4f}` |"
            )
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare two Chart Summarizer eval runs and flag regressions."
    )
    parser.add_argument(
        "--run-a",
        type=Path,
        required=True,
        metavar="PATH",
        help="Baseline run: path to results.json or its parent directory.",
    )
    parser.add_argument(
        "--run-b",
        type=Path,
        required=True,
        metavar="PATH",
        help="Current run: path to results.json or its parent directory.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional path to write a Markdown comparison report.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=REGRESSION_THRESHOLD,
        help=f"Metric drop that triggers a regression flag (default: {REGRESSION_THRESHOLD}).",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()

    try:
        results_a = load_results(args.run_a)
    except (FileNotFoundError, ValueError) as exc:
        print(f"[ERROR] Run A: {exc}", file=sys.stderr)
        return 1

    try:
        results_b = load_results(args.run_b)
    except (FileNotFoundError, ValueError) as exc:
        print(f"[ERROR] Run B: {exc}", file=sys.stderr)
        return 1

    comparison = compare_runs(results_a, results_b, threshold=args.threshold)

    label_a = str(args.run_a)
    label_b = str(args.run_b)

    text_report = _format_text(comparison, label_a, label_b)
    print(text_report)

    if args.output:
        md_report = _format_markdown(comparison, label_a, label_b)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(md_report, encoding="utf-8")
        print(f"\nMarkdown report written to: {args.output}")

    return 1 if comparison["has_regressions"] else 0


if __name__ == "__main__":
    sys.exit(main())