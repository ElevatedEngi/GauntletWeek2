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
FHIR tool: GET patient immunization records.

FHIR resource: Immunization
Endpoint:      GET /Immunization?patient={id}&_sort=-date&_count=100

Fetches CVX-coded immunization records with vaccine name, occurrence
date, dose number, lot number, and administering provider.
"""

from typing import Any

from chart_summarizer.models.patient import Immunization
from chart_summarizer.tools.base import FHIRTool, ToolResult
from chart_summarizer.tools.fhir._utils import (
    extract_bundle_entries,
    get_coding_code,
    get_coding_display,
    parse_fhir_date,
    parse_fhir_datetime,
)


class GetImmunizationsTool(FHIRTool):
    """Retrieve immunization records from FHIR Immunization resources."""

    @property
    def tool_name(self) -> str:
        return "get_immunizations"

    @property
    def description(self) -> str:
        return (
            "Fetch immunization history including vaccine name (CVX code), "
            "occurrence date, dose number, lot number, and administering provider."
        )

    async def execute(self, patient_id: str, **kwargs: Any) -> ToolResult:
        """
        Fetch Immunization resources for the given patient.

        Args:
            patient_id: OpenEMR patient PID.
            **kwargs: Supports ``count`` (default 100) and ``date_from`` filter.
        """
        count = kwargs.get("count", 100)
        params: dict[str, Any] = {
            "patient": patient_id,
            "_sort": "-date",
            "_count": str(count),
            "status": "completed,not-done",
        }
        if date_from := kwargs.get("date_from"):
            params["date"] = f"ge{date_from}"

        try:
            bundle = await self._fhir_get("/Immunization", params=params)
            resources = extract_bundle_entries(bundle)
            immunizations = [self._parse_immunization(r) for r in resources]
            return ToolResult(
                tool_name=self.tool_name,
                success=True,
                data=[imm.model_dump(mode="json") for imm in immunizations],
                records_returned=len(immunizations),
            )
        except NotImplementedError:
            raise
        except Exception as exc:
            return ToolResult(
                tool_name=self.tool_name,
                success=False,
                error_message=f"Failed to fetch immunizations: {exc}",
            )

    def _parse_immunization(self, resource: dict[str, Any]) -> Immunization:
        """Map a FHIR Immunization resource dict to an Immunization model."""
        vaccine_code = resource.get("vaccineCode") or {}
        vaccine_name = get_coding_display(vaccine_code, fallback="Unknown vaccine")
        cvx_code = get_coding_code(vaccine_code, system_substring="cvx")

        # Occurrence date: occurrenceDateTime or occurrenceString
        occurrence_date = None
        if occ_dt := resource.get("occurrenceDateTime"):
            # parse as date (date portion only)
            parsed = parse_fhir_datetime(occ_dt)
            occurrence_date = parsed.date() if parsed else parse_fhir_date(occ_dt)
        elif occ_str := resource.get("occurrenceString"):
            occurrence_date = parse_fhir_date(occ_str)

        # Dose number: protocolApplied[0].doseNumberPositiveInt or doseNumberString
        protocols = resource.get("protocolApplied") or []
        dose_number: str | None = None
        if protocols:
            proto = protocols[0]
            dose_int = proto.get("doseNumberPositiveInt")
            dose_str = proto.get("doseNumberString")
            if dose_int is not None:
                dose_number = str(dose_int)
            elif dose_str:
                dose_number = dose_str

        # Performer: first entry with function "AP" (administering provider) or first
        performers = resource.get("performer") or []
        administered_by: str | None = None
        for perf in performers:
            actor = perf.get("actor") or {}
            if actor.get("display"):
                administered_by = actor["display"]
                break

        return Immunization(
            immunization_id=resource.get("id", ""),
            vaccine_name=vaccine_name,
            cvx_code=cvx_code,
            dose_number=dose_number,
            occurrence_date=occurrence_date,
            status=resource.get("status", "unknown"),
            administered_by=administered_by,
            lot_number=resource.get("lotNumber"),
        )
