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
FHIR tool: GET patient procedure history.

FHIR resource: Procedure
Endpoint:      GET /Procedure?patient={id}&_sort=-date&_count=100

Fetches CPT/SNOMED-coded procedures with name, performed date, performer,
body site, status, and notes.
"""

from typing import Any

from chart_summarizer.models.patient import Procedure
from chart_summarizer.tools.base import FHIRTool, ToolResult
from chart_summarizer.tools.fhir._utils import (
    extract_bundle_entries,
    get_coding_code,
    get_coding_display,
    parse_fhir_date,
    parse_fhir_datetime,
)


class GetProceduresTool(FHIRTool):
    """Retrieve procedure history from FHIR Procedure resources."""

    @property
    def tool_name(self) -> str:
        return "get_procedures"

    @property
    def description(self) -> str:
        return (
            "Fetch surgical and procedural history including procedure name "
            "(CPT/SNOMED), performed date, performer, body site, and status."
        )

    async def execute(self, patient_id: str, **kwargs: Any) -> ToolResult:
        """
        Fetch Procedure resources for the given patient.

        Args:
            patient_id: OpenEMR patient PID.
            **kwargs: Supports ``count`` (default 100) and ``date_from`` filter.
        """
        count = kwargs.get("count", 100)
        params: dict[str, Any] = {
            "patient": patient_id,
            "_sort": "-date",
            "_count": str(count),
        }
        if date_from := kwargs.get("date_from"):
            params["date"] = f"ge{date_from}"

        try:
            bundle = await self._fhir_get("/Procedure", params=params)
            resources = extract_bundle_entries(bundle)
            procedures = [self._parse_procedure(r) for r in resources]
            return ToolResult(
                tool_name=self.tool_name,
                success=True,
                data=[proc.model_dump(mode="json") for proc in procedures],
                records_returned=len(procedures),
            )
        except NotImplementedError:
            raise
        except Exception as exc:
            return ToolResult(
                tool_name=self.tool_name,
                success=False,
                error_message=f"Failed to fetch procedures: {exc}",
            )

    def _parse_procedure(self, resource: dict[str, Any]) -> Procedure:
        """Map a FHIR Procedure resource dict to a Procedure model."""
        code_element = resource.get("code") or {}
        name = get_coding_display(code_element, fallback="Unknown procedure")
        cpt_code = get_coding_code(code_element, system_substring="cpt")
        snomed_code = get_coding_code(code_element, system_substring="snomed")

        # Performed date: performedDateTime | performedPeriod.start | performedString
        performed_date = None
        if pdt := resource.get("performedDateTime"):
            parsed_dt = parse_fhir_datetime(pdt)
            performed_date = parsed_dt.date() if parsed_dt else parse_fhir_date(pdt)
        elif pp := resource.get("performedPeriod"):
            parsed_dt = parse_fhir_datetime(pp.get("start") or "")
            performed_date = parsed_dt.date() if parsed_dt else None
        elif ps := resource.get("performedString"):
            performed_date = parse_fhir_date(ps)

        # Performer: first performer's actor display
        performers = resource.get("performer") or []
        performer: str | None = None
        if performers:
            actor = performers[0].get("actor") or {}
            performer = actor.get("display")

        # Body site: first bodySite coding display
        body_sites = resource.get("bodySite") or []
        body_site: str | None = None
        if body_sites:
            body_site = get_coding_display(body_sites[0])

        # Notes: note[].text concatenated
        notes_list = resource.get("note") or []
        notes_text: str | None = None
        if notes_list:
            parts = [n.get("text", "") for n in notes_list if n.get("text")]
            notes_text = " ".join(parts) if parts else None

        return Procedure(
            procedure_id=resource.get("id", ""),
            cpt_code=cpt_code,
            snomed_code=snomed_code,
            name=name,
            performed_date=performed_date,
            status=resource.get("status", "unknown"),
            performer=performer,
            body_site=body_site,
            notes=notes_text,
        )
