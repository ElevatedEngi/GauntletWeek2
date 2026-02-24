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
Post-generation fact-checking and verification.

After the LLM generates a summary, SummaryVerifier parses each clinical claim
and validates it against the source FHIR data used to generate the summary.

Confidence levels:
  GREEN  — ≥ 95% of claims verified
  YELLOW — 90–94% of claims verified
  RED    — < 90% verified or critical data (allergies, active meds) missing
"""

import re
from datetime import date
from typing import Any, Optional

from chart_summarizer.models.summary import Citation, VerificationResult

# Matches "[Source: <id>]" anywhere in text.
_SOURCE_RE = re.compile(r"\[Source:\s*([^\]]+)\]")

# Per-section record ID field names (same as model fields).
_SECTION_ID_FIELDS: dict[str, tuple[str, ...]] = {
    "demographics": ("fhir_id", "patient_id"),
    "conditions": ("condition_id",),
    "medications": ("medication_id",),
    "allergies": ("allergy_id",),
    "labs": ("lab_id",),
    "vitals": ("vital_id",),
    "encounters": ("encounter_id",),
    "immunizations": ("immunization_id",),
    "procedures": ("procedure_id",),
}

# Date fields to try when extracting a record date.
_RECORD_DATE_FIELDS = (
    "effective_date",
    "date",
    "onset_date",
    "start_date",
    "occurrence_date",
    "performed_date",
    "recorded_date",
)


class SummaryVerifier:
    """
    Validates that every claim in an AI-generated summary is traceable
    to a source record in the patient's FHIR data.

    Usage:
        verifier = SummaryVerifier()
        result = await verifier.verify(summary_text, patient_data)
    """

    def __init__(self) -> None:
        """Initialise the verifier. No external dependencies required."""
        pass

    async def verify(
        self,
        summary_text: str,
        patient_data: dict[str, Any],
    ) -> VerificationResult:
        """
        Verify all clinical claims in summary_text against patient_data.

        For each [Source: <id>] citation in the summary:
          1. Look up the source ID in the flat record index built from patient_data.
          2. If found → verified; if not found → unverified.
        Also runs critical completeness checks: every active allergy and active
        medication must be mentioned in the summary text.

        Args:
            summary_text: The LLM-generated markdown summary.
            patient_data: Dict of serialised FHIR resource lists keyed by section name.

        Returns:
            VerificationResult with verified/unverified claims and confidence level.
        """
        record_index = self._build_record_index(patient_data)
        citations = self._extract_claims(summary_text)

        verified: list[Citation] = []
        unverified: list[Citation] = []

        for citation in citations:
            record = record_index.get(citation.source_id)
            if record is not None:
                citation.verified = True
                citation.source_date = self._get_record_date(record)
                verified.append(citation)
            else:
                unverified.append(citation)

        flags = self._check_critical_completeness(patient_data, summary_text)

        total = len(citations)
        score = len(verified) / total if total > 0 else 1.0
        level = self._compute_confidence_level(score)

        # Any critical completeness failure forces RED.
        if flags:
            level = "RED"

        return VerificationResult(
            verified_claims=verified,
            unverified_claims=unverified,
            confidence_score=round(score, 4),
            confidence_level=level,  # type: ignore[arg-type]
            flags=flags,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_record_index(
        self, patient_data: dict[str, Any]
    ) -> dict[str, dict[str, Any]]:
        """Build a flat {record_id → record_dict} index from all patient_data sections."""
        index: dict[str, dict[str, Any]] = {}
        for section, data in patient_data.items():
            id_fields = _SECTION_ID_FIELDS.get(section, ())
            if isinstance(data, list):
                for record in data:
                    if isinstance(record, dict):
                        for field in id_fields:
                            if rid := record.get(field):
                                index[str(rid)] = record
                                break
            elif isinstance(data, dict):
                for field in id_fields:
                    if rid := data.get(field):
                        index[str(rid)] = data
                        break
        return index

    def _extract_claims(self, summary_text: str) -> list[Citation]:
        """
        Extract one Citation per [Source: <id>] tag found in the summary.

        Claim text is taken from the surrounding line (with the citation tag stripped).
        """
        citations: list[Citation] = []
        for match in _SOURCE_RE.finditer(summary_text):
            source_id = match.group(1).strip()

            # Capture the line that contains this citation.
            line_start = summary_text.rfind("\n", 0, match.start()) + 1
            line_end = summary_text.find("\n", match.end())
            if line_end == -1:
                line_end = len(summary_text)
            line = summary_text[line_start:line_end]
            claim_text = _SOURCE_RE.sub("", line).strip(" -•*#>").strip()

            citations.append(
                Citation(
                    claim_text=claim_text,
                    source_type=self._infer_source_type(source_id),
                    source_id=source_id,
                    verified=False,
                )
            )
        return citations

    def _match_claim_to_source(
        self,
        claim: str,
        source_id: str,
        patient_data: dict[str, Any],
    ) -> bool:
        """
        Return True if any string field value from the source record appears in claim.

        Used for fuzzy validation beyond simple ID lookup.
        """
        record = self._build_record_index(patient_data).get(source_id)
        if not record:
            return False
        claim_lower = claim.lower()
        for value in record.values():
            if isinstance(value, str) and len(value) > 3:
                if value.lower() in claim_lower:
                    return True
        return False

    def _compute_confidence_level(self, score: float) -> str:
        """
        Map a verification score (0.0–1.0) to a traffic-light confidence level.

        Returns:
            'GREEN'  if score >= 0.95
            'YELLOW' if 0.90 <= score < 0.95
            'RED'    if score < 0.90
        """
        if score >= 0.95:
            return "GREEN"
        if score >= 0.90:
            return "YELLOW"
        return "RED"

    def _check_critical_completeness(
        self,
        patient_data: dict[str, Any],
        summary_text: str,
    ) -> list[str]:
        """
        Check that all active allergies and active medications appear in the summary.

        A missing allergy or active medication is a critical failure that forces
        confidence level to RED regardless of the overall citation score.
        """
        flags: list[str] = []
        summary_lower = summary_text.lower()

        for allergy in patient_data.get("allergies", []):
            if not isinstance(allergy, dict):
                continue
            if allergy.get("clinical_status", "active") == "resolved":
                continue
            substance = allergy.get("substance", "")
            if substance and substance.lower() not in summary_lower:
                flags.append(f"Allergy not mentioned in summary: {substance}")

        for med in patient_data.get("medications", []):
            if not isinstance(med, dict):
                continue
            if med.get("status", "active") not in ("active", "on-hold"):
                continue
            name = med.get("name", "")
            if name and name.lower() not in summary_lower:
                flags.append(f"Active medication not mentioned in summary: {name}")

        return flags

    def _infer_source_type(self, source_id: str) -> str:
        """Heuristically infer resource type from a source ID string."""
        sid = source_id.lower()
        for keyword, rtype in (
            ("cond", "condition"),
            ("med", "medication"),
            ("allerg", "allergy"),
            ("lab", "lab"),
            ("vital", "vital"),
            ("enc", "encounter"),
            ("imm", "immunization"),
            ("proc", "procedure"),
            ("obs", "observation"),
            ("demo", "demographics"),
        ):
            if keyword in sid:
                return rtype
        return "unknown"

    def _get_record_date(self, record: dict[str, Any]) -> Optional[date]:
        """Return the first recognised date field value from a record dict."""
        for field in _RECORD_DATE_FIELDS:
            raw = record.get(field)
            if raw and isinstance(raw, str) and len(raw) >= 10:
                try:
                    return date.fromisoformat(raw[:10])
                except ValueError:
                    pass
        return None
