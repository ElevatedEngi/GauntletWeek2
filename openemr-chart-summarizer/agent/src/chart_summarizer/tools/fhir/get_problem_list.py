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
FHIR tool: GET patient problem list (active and resolved conditions).

FHIR resource: Condition
Endpoint:      GET /Condition?patient={id}&category=problem-list-item
               GET /Condition?patient={id}&category=encounter-diagnosis

Fetches ICD-10 coded diagnoses, clinical status, onset/resolution dates.
"""

from typing import Any

from chart_summarizer.models.patient import Condition
from chart_summarizer.tools.base import FHIRTool, ToolResult
from chart_summarizer.tools.fhir._utils import (
    extract_bundle_entries,
    get_coding_code,
    get_coding_display,
    parse_fhir_date,
)


class GetProblemListTool(FHIRTool):
    """Retrieve the patient's problem list from FHIR Condition resources."""

    @property
    def tool_name(self) -> str:
        return "get_problem_list"

    @property
    def description(self) -> str:
        return (
            "Fetch active and resolved conditions from the patient's problem list. "
            "Returns ICD-10 coded diagnoses with onset and resolution dates."
        )

    async def execute(self, patient_id: str, **kwargs: Any) -> ToolResult:
        """
        Fetch Condition resources for the given patient.

        Args:
            patient_id: OpenEMR patient PID.
            **kwargs: Supports ``date_from`` (ISO date string) to filter by onset date.
        """
        try:
            bundle = await self._fhir_get(
                "/Condition",
                params={
                    "patient": patient_id,
                    "category": "problem-list-item,encounter-diagnosis",
                    "_sort": "-onset-date",
                    "_count": "200",
                },
            )
            resources = extract_bundle_entries(bundle)
            conditions = [self._parse_condition(r) for r in resources]
            return ToolResult(
                tool_name=self.tool_name,
                success=True,
                data=[c.model_dump(mode="json") for c in conditions],
                records_returned=len(conditions),
            )
        except NotImplementedError:
            raise
        except Exception as exc:
            return ToolResult(
                tool_name=self.tool_name,
                success=False,
                error_message=f"Failed to fetch problem list: {exc}",
            )

    def _parse_condition(self, resource: dict[str, Any]) -> Condition:
        """Map a FHIR Condition resource dict to a Condition model."""
        code_element = resource.get("code") or {}
        clinical_status_element = resource.get("clinicalStatus") or {}

        notes_list = resource.get("note") or []
        notes = "; ".join(n.get("text", "") for n in notes_list) or None

        recorder_ref = resource.get("recorder") or {}

        return Condition(
            condition_id=resource.get("id", ""),
            icd10_code=get_coding_code(code_element, system_substring="icd-10"),
            display_name=get_coding_display(code_element, fallback="Unknown condition"),
            clinical_status=get_coding_code(clinical_status_element) or "unknown",
            onset_date=parse_fhir_date(resource.get("onsetDateTime")),
            resolved_date=parse_fhir_date(resource.get("abatementDateTime")),
            recorded_by=recorder_ref.get("display"),
            notes=notes,
        )
