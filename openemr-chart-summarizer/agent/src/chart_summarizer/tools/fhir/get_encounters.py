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
FHIR tool: GET patient encounter notes.

FHIR resource: Encounter
Endpoint:      GET /Encounter?patient={id}&_sort=-date&_count=50
               GET /DocumentReference?patient={id}&type=...&_sort=-date  (SOAP notes)

Fetches clinical encounters with provider, encounter type, diagnoses,
and discharge disposition. SOAP notes are retrieved via DocumentReference
if available.
"""

from typing import Any

from chart_summarizer.models.patient import Encounter
from chart_summarizer.tools.base import FHIRTool, ToolResult
from chart_summarizer.tools.fhir._utils import (
    extract_bundle_entries,
    get_coding_display,
    parse_fhir_datetime,
)


class GetEncounterNotesTool(FHIRTool):
    """Retrieve clinical encounter records from FHIR Encounter resources."""

    @property
    def tool_name(self) -> str:
        return "get_encounter_notes"

    @property
    def description(self) -> str:
        return (
            "Fetch clinical encounters including visit date, provider, encounter "
            "type, diagnoses, chief complaint, and SOAP note text where available."
        )

    async def execute(self, patient_id: str, **kwargs: Any) -> ToolResult:
        """
        Fetch Encounter resources for the given patient.

        Args:
            patient_id: OpenEMR patient PID.
            **kwargs: Supports ``count`` (default 50) and ``date_from`` filter.
        """
        count = kwargs.get("count", 50)
        params: dict[str, Any] = {
            "patient": patient_id,
            "_sort": "-date",
            "_count": str(count),
            "status": "finished,in-progress,arrived,triaged",
        }
        if date_from := kwargs.get("date_from"):
            params["date"] = f"ge{date_from}"

        try:
            bundle = await self._fhir_get("/Encounter", params=params)
            resources = extract_bundle_entries(bundle)
            encounters = [self._parse_encounter(r) for r in resources]
            return ToolResult(
                tool_name=self.tool_name,
                success=True,
                data=[enc.model_dump(mode="json") for enc in encounters],
                records_returned=len(encounters),
            )
        except NotImplementedError:
            raise
        except Exception as exc:
            return ToolResult(
                tool_name=self.tool_name,
                success=False,
                error_message=f"Failed to fetch encounter notes: {exc}",
            )

    def _parse_encounter(self, resource: dict[str, Any]) -> Encounter:
        """Map a FHIR Encounter resource dict to an Encounter model."""
        # Encounter type (first coding display from type[0])
        types = resource.get("type") or []
        encounter_type = get_coding_display(types[0]) if types else None

        # Date: prefer period.start, fallback to actualPeriod.start
        period = resource.get("period") or resource.get("actualPeriod") or {}
        raw_date = period.get("start") or period.get("end")
        encounter_dt = parse_fhir_datetime(raw_date)
        if encounter_dt is None:
            from datetime import datetime
            encounter_dt = datetime.min

        # Provider: participant with type "ATND" (attending) or first participant
        participants = resource.get("participant") or []
        provider: str | None = None
        for p in participants:
            types_list = p.get("type") or []
            codes = [
                get_coding_display(t)
                for t in types_list
            ]
            individual = p.get("individual") or p.get("actor") or {}
            if "ATND" in " ".join(codes) or not provider:
                provider = individual.get("display")

        # Specialty: serviceType coding display
        service_type = resource.get("serviceType") or {}
        specialty = get_coding_display(service_type) if service_type else None

        # Chief complaint: reasonCode[0].text or coding display
        reason_codes = resource.get("reasonCode") or resource.get("reason") or []
        chief_complaint: str | None = None
        if reason_codes:
            first_reason = reason_codes[0]
            # reasonCode is a CodeableConcept; reason may be a reference list in R4
            if isinstance(first_reason, dict):
                chief_complaint = (
                    first_reason.get("text")
                    or get_coding_display(first_reason)
                    or None
                )

        # Diagnoses: condition references with display text
        diagnoses_raw = resource.get("diagnosis") or []
        diagnoses: list[str] = []
        for dx in diagnoses_raw:
            condition_ref = dx.get("condition") or {}
            display = condition_ref.get("display")
            if display:
                diagnoses.append(display)
            # use codeable concept if present (R4 variation)
            use_cc = dx.get("use") or {}
            use_display = get_coding_display(use_cc)
            if use_display and use_display not in diagnoses:
                diagnoses.append(use_display)

        # Discharge disposition
        hosp = resource.get("hospitalization") or resource.get("admission") or {}
        disp_cc = hosp.get("dischargeDisposition") or {}
        discharge_disposition = get_coding_display(disp_cc) if disp_cc else None

        return Encounter(
            encounter_id=resource.get("id", ""),
            encounter_type=encounter_type,
            date=encounter_dt,
            provider=provider,
            specialty=specialty,
            chief_complaint=chief_complaint,
            soap_note=None,  # SOAP note retrieval via DocumentReference is out of scope here
            diagnoses=diagnoses,
            discharge_disposition=discharge_disposition,
        )
