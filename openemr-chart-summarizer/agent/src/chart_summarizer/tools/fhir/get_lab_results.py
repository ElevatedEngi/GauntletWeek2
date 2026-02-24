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
FHIR tool: GET patient laboratory results.

FHIR resource: Observation (category=laboratory)
Endpoint:      GET /Observation?patient={id}&category=laboratory&_sort=-date&_count=100

Fetches LOINC-coded lab results with values, units, reference ranges,
and H/L/N interpretation flags.
"""

from typing import Any

from chart_summarizer.models.patient import LabResult
from chart_summarizer.tools.base import FHIRTool, ToolResult
from chart_summarizer.tools.fhir._utils import (
    extract_bundle_entries,
    get_coding_code,
    get_coding_display,
    parse_fhir_datetime,
)


class GetLabResultsTool(FHIRTool):
    """Retrieve recent laboratory results from FHIR Observation resources."""

    @property
    def tool_name(self) -> str:
        return "get_lab_results"

    @property
    def description(self) -> str:
        return (
            "Fetch recent laboratory results including test name (LOINC), "
            "value, unit, reference range, and H/L/N interpretation."
        )

    async def execute(self, patient_id: str, **kwargs: Any) -> ToolResult:
        """
        Fetch laboratory Observation resources for the given patient.

        Args:
            patient_id: OpenEMR patient PID.
            **kwargs: Supports ``count`` (default 100) and ``date_from`` filter.
        """
        count = kwargs.get("count", 100)
        params: dict[str, Any] = {
            "patient": patient_id,
            "category": "laboratory",
            "_sort": "-date",
            "_count": str(count),
            "status": "final,amended,corrected,preliminary",
        }
        if date_from := kwargs.get("date_from"):
            params["date"] = f"ge{date_from}"

        try:
            bundle = await self._fhir_get("/Observation", params=params)
            resources = extract_bundle_entries(bundle)
            labs = [self._parse_lab(r) for r in resources]
            return ToolResult(
                tool_name=self.tool_name,
                success=True,
                data=[lab.model_dump(mode="json") for lab in labs],
                records_returned=len(labs),
            )
        except NotImplementedError:
            raise
        except Exception as exc:
            return ToolResult(
                tool_name=self.tool_name,
                success=False,
                error_message=f"Failed to fetch lab results: {exc}",
            )

    def _parse_lab(self, resource: dict[str, Any]) -> LabResult:
        """Map a FHIR Observation (laboratory) resource dict to a LabResult model."""
        code_element = resource.get("code") or {}

        # Value: try valueQuantity first, then valueString, then valueCodeableConcept
        value: str | None = None
        unit: str | None = None
        if vq := resource.get("valueQuantity"):
            value = str(vq.get("value", ""))
            unit = vq.get("unit") or vq.get("code")
        elif vs := resource.get("valueString"):
            value = vs
        elif vc := resource.get("valueCodeableConcept"):
            value = get_coding_display(vc)

        # Reference range: use the first entry's text, or low–high
        ref_ranges = resource.get("referenceRange") or []
        reference_range: str | None = None
        if ref_ranges:
            rr = ref_ranges[0]
            if text := rr.get("text"):
                reference_range = text
            elif low := rr.get("low", {}).get("value"):
                high = rr.get("high", {}).get("value")
                reference_range = f"{low}–{high}" if high else f">= {low}"

        # Interpretation: H, L, N, A etc.
        interpretations = resource.get("interpretation") or []
        interpretation: str | None = None
        if interpretations:
            interpretation = get_coding_code(interpretations[0])

        # Performer: use the first entry
        performers = resource.get("performer") or []
        ordering_provider = performers[0].get("display") if performers else None

        return LabResult(
            lab_id=resource.get("id", ""),
            loinc_code=get_coding_code(code_element, system_substring="loinc"),
            test_name=get_coding_display(code_element, fallback="Unknown test"),
            value=value,
            unit=unit,
            reference_range=reference_range,
            interpretation=interpretation,
            status=resource.get("status", "unknown"),
            effective_date=parse_fhir_datetime(
                resource.get("effectiveDateTime") or resource.get("effectivePeriod", {}).get("start")
            ),
            ordering_provider=ordering_provider,
        )
