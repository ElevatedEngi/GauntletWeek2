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
FHIR tool: GET Patient demographics.

FHIR resource: Patient
Endpoint:      GET /Patient/{patient_id}
OpenEMR FHIR: /fhir/Patient/{pid}

Fetches: name, date of birth, sex, race (US Core extension),
         ethnicity, primary language, and insurance/coverage data.
"""

from typing import Any

from chart_summarizer.models.patient import PatientDemographics
from chart_summarizer.tools.base import FHIRTool, ToolResult
from chart_summarizer.tools.fhir._utils import (
    get_coding_display,
    parse_fhir_date,
)

_US_CORE_RACE = "us-core-race"
_US_CORE_ETHNICITY = "us-core-ethnicity"


class GetPatientDemographicsTool(FHIRTool):
    """
    Retrieve patient demographic information from the FHIR Patient resource.

    Uses a direct resource GET (not a search Bundle) since we always look
    up by the OpenEMR patient PID, which maps 1:1 to the FHIR Patient ID.
    """

    @property
    def tool_name(self) -> str:
        return "get_patient_demographics"

    @property
    def description(self) -> str:
        return (
            "Fetch patient demographics: name, date of birth, sex, race, "
            "ethnicity, primary language, and insurance information."
        )

    async def execute(self, patient_id: str, **kwargs: Any) -> ToolResult:
        """
        Fetch the FHIR Patient resource for the given patient_id.

        Args:
            patient_id: OpenEMR patient PID (maps to FHIR Patient.id).

        Returns:
            ToolResult with a single PatientDemographics dict.
        """
        try:
            resource = await self._fhir_get(f"/Patient/{patient_id}")
            demographics = self._parse_patient(patient_id, resource)
            return ToolResult(
                tool_name=self.tool_name,
                success=True,
                data=demographics.model_dump(mode="json"),
                records_returned=1,
            )
        except NotImplementedError:
            raise
        except Exception as exc:
            return ToolResult(
                tool_name=self.tool_name,
                success=False,
                error_message=f"Failed to fetch patient demographics: {exc}",
            )

    def _parse_patient(self, patient_id: str, resource: dict[str, Any]) -> PatientDemographics:
        """Map a FHIR Patient resource dict to a PatientDemographics model."""
        # Name: use the first HumanName; prefer official use
        names = resource.get("name") or []
        official = next((n for n in names if n.get("use") == "official"), None)
        name_obj = official or (names[0] if names else {})
        first_name = " ".join(name_obj.get("given") or [])
        last_name = name_obj.get("family") or ""

        # US Core race / ethnicity extensions
        race = self._extract_us_core_extension(resource, _US_CORE_RACE)
        ethnicity = self._extract_us_core_extension(resource, _US_CORE_ETHNICITY)

        # Primary communication language
        communications = resource.get("communication") or []
        primary_lang: str | None = None
        for comm in communications:
            if comm.get("preferred"):
                primary_lang = get_coding_display(comm.get("language"))
                break
        if not primary_lang and communications:
            primary_lang = get_coding_display(communications[0].get("language"))

        return PatientDemographics(
            patient_id=patient_id,
            fhir_id=resource.get("id"),
            first_name=first_name,
            last_name=last_name,
            date_of_birth=parse_fhir_date(resource.get("birthDate")),  # type: ignore[arg-type]
            sex=resource.get("gender", "unknown").capitalize(),
            race=race,
            ethnicity=ethnicity,
            primary_language=primary_lang,
            # Insurance: requires a separate Coverage resource lookup — deferred
            insurance_name=None,
            # Primary care provider: requires CareTeam lookup — deferred
            primary_care_provider=None,
        )

    def _extract_us_core_extension(
        self, resource: dict[str, Any], url_fragment: str
    ) -> str | None:
        """Extract the text value from a US Core race or ethnicity extension."""
        extensions = resource.get("extension") or []
        for ext in extensions:
            url = ext.get("url", "")
            if url_fragment in url:
                # The ombCategory sub-extension holds the display text
                for sub in ext.get("extension") or []:
                    if sub.get("url") == "text":
                        return sub.get("valueString")
        return None
