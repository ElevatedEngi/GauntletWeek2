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
FHIR tool: GET patient vital signs history.

FHIR resource: Observation (category=vital-signs)
Endpoint:      GET /Observation?patient={id}&category=vital-signs&_sort=-date&_count=100

Fetches LOINC-coded vital sign observations including blood pressure,
heart rate, weight, height, BMI, temperature, and oxygen saturation.
Blood pressure panels (LOINC 55284-4) are unpacked from component values.
"""

from typing import Any

from chart_summarizer.models.patient import VitalSign
from chart_summarizer.tools.base import FHIRTool, ToolResult
from chart_summarizer.tools.fhir._utils import (
    extract_bundle_entries,
    get_coding_code,
    get_coding_display,
    parse_fhir_datetime,
)

# LOINC codes that map to human-readable vital sign type labels
_LOINC_TO_TYPE: dict[str, str] = {
    "55284-4": "blood-pressure",   # Blood pressure panel
    "8480-6": "blood-pressure",    # Systolic (standalone)
    "8462-4": "blood-pressure",    # Diastolic (standalone)
    "8867-4": "heart-rate",
    "29463-7": "body-weight",
    "8302-2": "body-height",
    "39156-5": "bmi",
    "8310-5": "temperature",
    "59408-5": "oxygen-saturation",
    "2708-6": "oxygen-saturation",  # Alternate O2 sat LOINC
}


class GetVitalsHistoryTool(FHIRTool):
    """Retrieve vital signs history from FHIR Observation resources."""

    @property
    def tool_name(self) -> str:
        return "get_vitals_history"

    @property
    def description(self) -> str:
        return (
            "Fetch recent vital signs including blood pressure, heart rate, "
            "weight, height, BMI, temperature, and oxygen saturation."
        )

    async def execute(self, patient_id: str, **kwargs: Any) -> ToolResult:
        """
        Fetch vital-signs Observation resources for the given patient.

        Args:
            patient_id: OpenEMR patient PID.
            **kwargs: Supports ``count`` (default 100) and ``date_from`` filter.
        """
        count = kwargs.get("count", 100)
        params: dict[str, Any] = {
            "patient": patient_id,
            "category": "vital-signs",
            "_sort": "-date",
            "_count": str(count),
        }
        if date_from := kwargs.get("date_from"):
            params["date"] = f"ge{date_from}"

        try:
            bundle = await self._fhir_get("/Observation", params=params)
            resources = extract_bundle_entries(bundle)
            vitals: list[VitalSign] = []
            for resource in resources:
                parsed = self._parse_vital(resource)
                vitals.extend(parsed)
            return ToolResult(
                tool_name=self.tool_name,
                success=True,
                data=[v.model_dump(mode="json") for v in vitals],
                records_returned=len(vitals),
            )
        except NotImplementedError:
            raise
        except Exception as exc:
            return ToolResult(
                tool_name=self.tool_name,
                success=False,
                error_message=f"Failed to fetch vitals: {exc}",
            )

    def _parse_vital(self, resource: dict[str, Any]) -> list[VitalSign]:
        """
        Map a FHIR Observation (vital-signs) resource to one or more VitalSign models.

        Blood pressure panels contain systolic and diastolic in component[], so
        they expand to a single VitalSign with value like '120/80'.
        """
        code_element = resource.get("code") or {}
        loinc = get_coding_code(code_element, system_substring="loinc")
        vital_type = _LOINC_TO_TYPE.get(loinc or "", get_coding_display(code_element, fallback="unknown"))
        effective_dt = parse_fhir_datetime(
            resource.get("effectiveDateTime")
            or resource.get("effectivePeriod", {}).get("start")
        )
        if effective_dt is None:
            return []

        # Performer
        performers = resource.get("performer") or []
        recorder = performers[0].get("display") if performers else None

        # Blood pressure panel: expand systolic/diastolic from component[]
        components = resource.get("component") or []
        if loinc == "55284-4" and components:
            systolic: str | None = None
            diastolic: str | None = None
            for comp in components:
                comp_code = get_coding_code(comp.get("code") or {}, system_substring="loinc")
                vq = comp.get("valueQuantity") or {}
                val = str(vq.get("value", "")) if vq.get("value") is not None else None
                if comp_code == "8480-6":
                    systolic = val
                elif comp_code == "8462-4":
                    diastolic = val
            if systolic and diastolic:
                bp_value = f"{systolic}/{diastolic}"
            elif systolic:
                bp_value = systolic
            elif diastolic:
                bp_value = diastolic
            else:
                bp_value = "unknown"
            return [
                VitalSign(
                    vital_id=resource.get("id", ""),
                    type="blood-pressure",
                    value=bp_value,
                    unit="mmHg",
                    effective_date=effective_dt,
                    recorder=recorder,
                )
            ]

        # Scalar vital sign
        vq = resource.get("valueQuantity") or {}
        if vq.get("value") is not None:
            value = str(vq["value"])
            unit = vq.get("unit") or vq.get("code")
        elif vs := resource.get("valueString"):
            value = vs
            unit = None
        else:
            return []

        return [
            VitalSign(
                vital_id=resource.get("id", ""),
                type=vital_type,
                value=value,
                unit=unit,
                effective_date=effective_dt,
                recorder=recorder,
            )
        ]
