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
Hallucination checker for Chart Summarizer AI outputs.

Extracts factual claims from a generated clinical summary and verifies each
against the source FHIR bundle data.

Supported claim types:
  - medication  : drug names and dosages (e.g. "Lisinopril 10mg")
  - diagnosis   : ICD-10 codes and condition names
  - lab_value   : numeric measurements with units (e.g. "A1C 7.8%")
  - date        : encounter and onset dates

Usage (CLI):
    python hallucination_checker.py \\
        --summary summary.txt \\
        --source eval/datasets/synthetic_patients/01_simple_healthy_adult.json

Usage (library):
    from hallucination_checker import check_hallucinations
    report = check_hallucinations(summary_text, fhir_bundle)
    print(f"Verification rate: {report['verification_rate']:.1%}")
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Claim extraction patterns
# ---------------------------------------------------------------------------

# Medication: drug name (title-case or common lowercase) optionally followed
# by a dose (number + unit).
_MED_PATTERN = re.compile(
    r"\b([A-Z][a-z]{2,}(?:[a-z]+)?|metformin|aspirin|insulin|warfarin|apixaban"
    r"|lisinopril|furosemide|atorvastatin|sertraline|buspirone|metoprolol"
    r"|levothyroxine|allopurinol|tamsulosin|amlodipine|tiotropium|albuterol)"
    r"(?:\s+(\d+(?:\.\d+)?\s*(?:mg|mcg|units?|IU|g)\s*(?:/day|daily|BID|TID|QID|weekly|PRN)?))?"
    r"\b",
    re.IGNORECASE,
)

# ICD-10 code pattern
_ICD10_PATTERN = re.compile(r"\b([A-Z]\d{2}(?:\.\d{1,3})?)\b")

# Lab value: number with common units
_LAB_PATTERN = re.compile(
    r"(\d+\.?\d*)\s*(mg/dL|mmol/L|g/dL|IU/L|mcg/dL|pg/mL|mL/min|%"
    r"|ng/mL|mcg/mL|mEq/L|U/L|cells/\xb5L)",
    re.IGNORECASE,
)

# Dates (ISO and written formats)
_DATE_PATTERN = re.compile(
    r"\b(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4}"
    r"|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{1,2},? \d{4})\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Source text builder — flatten FHIR bundle into a searchable string
# ---------------------------------------------------------------------------

def _flatten_fhir_bundle(fhir_bundle: dict[str, Any]) -> str:
    """
    Convert a fhir_bundle dict into a single lowercase text blob for searching.

    Recursively stringifies every value in the bundle, so medication names,
    ICD codes, lab values, and dates are all searchable by simple substring.
    """

    def _recurse(obj: Any) -> str:
        if isinstance(obj, str):
            return obj
        if isinstance(obj, (int, float)):
            return str(obj)
        if isinstance(obj, dict):
            return " ".join(_recurse(v) for v in obj.values())
        if isinstance(obj, list):
            return " ".join(_recurse(item) for item in obj)
        return ""

    return _recurse(fhir_bundle).lower()


# ---------------------------------------------------------------------------
# Claim extraction
# ---------------------------------------------------------------------------

ClaimDetail = dict[str, str]  # {claim, claim_type, matched_in_source}


def _extract_claims(summary_text: str) -> list[dict[str, str]]:
    """
    Extract all verifiable factual claims from the summary text.

    Returns a list of dicts with keys: ``claim``, ``claim_type``.
    Deduplicates by (claim, type) pair.
    """
    seen: set[tuple[str, str]] = set()
    claims: list[dict[str, str]] = []

    def _add(claim: str, claim_type: str) -> None:
        key = (claim.strip().lower(), claim_type)
        if key not in seen:
            seen.add(key)
            claims.append({"claim": claim.strip(), "claim_type": claim_type})

    # Medications
    for match in _MED_PATTERN.finditer(summary_text):
        full = match.group(0).strip()
        if len(full) > 3:  # skip very short matches (e.g. "a")
            _add(full, "medication")

    # ICD-10 codes
    for match in _ICD10_PATTERN.finditer(summary_text):
        _add(match.group(1), "diagnosis")

    # Lab values
    for match in _LAB_PATTERN.finditer(summary_text):
        _add(f"{match.group(1)} {match.group(2)}", "lab_value")

    # Dates
    for match in _DATE_PATTERN.finditer(summary_text):
        _add(match.group(1), "date")

    return claims


# ---------------------------------------------------------------------------
# Claim verification against FHIR source
# ---------------------------------------------------------------------------

def _verify_claim(claim: dict[str, str], source_text: str) -> tuple[bool, str]:
    """
    Check whether a claim appears in the flattened FHIR source text.

    Returns (verified: bool, reason: str).
    """
    needle = claim["claim"].lower()

    # For medications with dose (e.g. "Lisinopril 10mg"), try both the full
    # string and just the drug name.
    if claim["claim_type"] == "medication":
        # Extract drug name portion (before dose)
        drug_name = re.split(r"\s+\d", needle)[0].strip()
        if drug_name in source_text:
            return True, f"drug name '{drug_name}' found in source"
        if needle in source_text:
            return True, "full medication string found in source"
        return False, f"'{needle}' not found in any FHIR section"

    if needle in source_text:
        return True, f"'{needle}' found in source"

    # For lab values, also try just the numeric part
    if claim["claim_type"] == "lab_value":
        num_part = re.match(r"[\d.]+", needle)
        if num_part and num_part.group() in source_text:
            return True, f"numeric value '{num_part.group()}' found in source"

    return False, f"'{needle}' not found in FHIR source data"


# ---------------------------------------------------------------------------
# Main API
# ---------------------------------------------------------------------------

def check_hallucinations(
    summary_text: str,
    fhir_bundle: dict[str, Any],
) -> dict[str, Any]:
    """
    Verify every factual claim in ``summary_text`` against ``fhir_bundle``.

    Args:
        summary_text: Generated clinical summary (plain text or Markdown).
        fhir_bundle:  fhir_bundle dict from a synthetic patient JSON file,
                      or any dict with FHIR R4 resource data.

    Returns:
        HallucinationReport dict:
          - total_claims      : int
          - verified_claims   : list of {claim, claim_type, matched_in_source}
          - unverified_claims : list of {claim, claim_type, reason}
          - verification_rate : float (0.0–1.0)
          - hallucination_score : float (1.0 = clean, 0.0 = all hallucinated)
    """
    source_text = _flatten_fhir_bundle(fhir_bundle)
    claims = _extract_claims(summary_text)

    verified: list[dict[str, str]] = []
    unverified: list[dict[str, str]] = []

    for claim in claims:
        ok, reason = _verify_claim(claim, source_text)
        if ok:
            verified.append({**claim, "matched_in_source": reason})
        else:
            unverified.append({**claim, "reason": reason})

    total = len(claims)
    verification_rate = len(verified) / max(1, total)
    hallucination_score = 1.0 - (len(unverified) / max(1, total))

    return {
        "total_claims": total,
        "verified_claims": verified,
        "unverified_claims": unverified,
        "verification_rate": round(verification_rate, 4),
        "hallucination_score": round(hallucination_score, 4),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Verify factual claims in an AI-generated clinical summary "
            "against source FHIR data."
        )
    )
    parser.add_argument(
        "--summary",
        type=Path,
        required=True,
        help="Path to a plain-text summary file.",
    )
    parser.add_argument(
        "--source",
        type=Path,
        required=True,
        help=(
            "Path to the source data. Either a synthetic patient JSON "
            "(contains fhir_bundle key) or a bare FHIR bundle JSON."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output the full report as JSON (default: human-readable text).",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()

    try:
        summary_text = args.summary.read_text(encoding="utf-8")
    except FileNotFoundError:
        print(f"[ERROR] Summary file not found: {args.summary}", file=sys.stderr)
        return 1

    try:
        source_data = json.loads(args.source.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"[ERROR] Could not load source: {exc}", file=sys.stderr)
        return 1

    # Accept either a full synthetic patient JSON (has fhir_bundle key)
    # or a bare FHIR bundle dict.
    fhir_bundle = source_data.get("fhir_bundle", source_data)

    report = check_hallucinations(summary_text, fhir_bundle)

    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    print(f"\nHallucination Check Report")
    print("=" * 50)
    print(f"Total claims extracted : {report['total_claims']}")
    print(f"Verified               : {len(report['verified_claims'])}")
    print(f"Unverified             : {len(report['unverified_claims'])}")
    print(f"Verification rate      : {report['verification_rate']:.1%}")
    print(f"Hallucination score    : {report['hallucination_score']:.1%}")

    if report["unverified_claims"]:
        print("\nUnverified claims (potential hallucinations):")
        for c in report["unverified_claims"]:
            print(f"  [{c['claim_type']:12s}] \"{c['claim']}\"")
            print(f"               Reason: {c['reason']}")

    if report["verified_claims"]:
        print(f"\nVerified claims ({len(report['verified_claims'])}):")
        for c in report["verified_claims"]:
            print(f"  [{c['claim_type']:12s}] \"{c['claim']}\"")

    verdict = (
        "[CLEAN] No hallucinations detected."
        if not report["unverified_claims"]
        else f"[WARNING] {len(report['unverified_claims'])} unverified claim(s) — manual review recommended."
    )
    print(f"\n{verdict}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())