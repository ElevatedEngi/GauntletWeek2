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
FHIR tool: GET patient medications.

FHIR resource: MedicationRequest
Endpoint:      GET /MedicationRequest?patient={id}&status=active,on-hold,stopped,completed

Fetches medication name (RxNorm coded), dosage instructions, route, status,
prescriber, and indication.
"""

from typing import Any

from chart_summarizer.models.patient import Medication
from chart_summarizer.tools.base import FHIRTool, ToolResult
from chart_summarizer.tools.fhir._utils import (
    extract_bundle_entries,
    get_coding_code,
    get_coding_display,
    parse_fhir_date,
)

# FHIR MedicationRequest statuses to retrieve
_ACTIVE_STATUSES = "active,on-hold,stopped,completed"


class GetMedicationsTool(FHIRTool):
    """
    Retrieve the patient's medication list from FHIR MedicationRequest resources.

    CRITICAL: Medication completeness is a HIPAA/patient-safety requirement.
    Every active medication must appear in the generated summary. This tool
    fetches all non-cancelled, non-entered-in-error MedicationRequests.
    """

    @property
    def tool_name(self) -> str:
        return "get_medications"

    @property
    def description(self) -> str:
        return (
            "Fetch current and recent medications including dosage, frequency, "
            "route, prescriber, and clinical indication."
        )

    async def execute(self, patient_id: str, **kwargs: Any) -> ToolResult:
        """
        Fetch MedicationRequest resources for the given patient.

        Args:
            patient_id: OpenEMR patient PID.
            **kwargs: Supports ``status`` override (default: active,on-hold,stopped,completed).
        """
        status = kwargs.get("status", _ACTIVE_STATUSES)
        try:
            bundle = await self._fhir_get(
                "/MedicationRequest",
                params={
                    "patient": patient_id,
                    "status": status,
                    "_sort": "-authoredon",
                    "_count": "200",
                },
            )
            resources = extract_bundle_entries(bundle)
            medications = [self._parse_medication(r) for r in resources]
            return ToolResult(
                tool_name=self.tool_name,
                success=True,
                data=[m.model_dump(mode="json") for m in medications],
                records_returned=len(medications),
            )
        except NotImplementedError:
            raise
        except Exception as exc:
            return ToolResult(
                tool_name=self.tool_name,
                success=False,
                error_message=f"Failed to fetch medications: {exc}",
            )

    def _parse_medication(self, resource: dict[str, Any]) -> Medication:
        """Map a FHIR MedicationRequest resource dict to a Medication model."""
        med_element = resource.get("medicationCodeableConcept") or {}

        # Dosage: use the first dosageInstruction entry
        dosage_instructions = resource.get("dosageInstruction") or []
        dosage_text: str | None = None
        frequency: str | None = None
        route: str | None = None

        if dosage_instructions:
            di = dosage_instructions[0]
            dosage_text = di.get("doseAndRate", [{}])[0].get("doseQuantity", {}).get(
                "value"
            )
            if dosage_text:
                unit = di.get("doseAndRate", [{}])[0].get("doseQuantity", {}).get("unit", "")
                dosage_text = f"{dosage_text} {unit}".strip()
            else:
                # Fall back to free-text dosage
                dosage_text = di.get("text")

            timing = di.get("timing") or {}
            repeat = timing.get("repeat") or {}
            if repeat.get("frequency") and repeat.get("period"):
                frequency = f"{repeat['frequency']} per {repeat['period']} {repeat.get('periodUnit', '')}"
            else:
                frequency = di.get("text")  # use full text as fallback

            route = get_coding_display(di.get("route"))

        # Indication
        reason_codes = resource.get("reasonCode") or []
        indication = get_coding_display(reason_codes[0]) if reason_codes else None

        # Prescriber
        requester_ref = resource.get("requester") or {}

        return Medication(
            medication_id=resource.get("id", ""),
            name=get_coding_display(med_element, fallback="Unknown medication"),
            rxnorm_code=get_coding_code(med_element, system_substring="rxnorm"),
            dosage=dosage_text,
            frequency=frequency,
            route=route or None,
            status=resource.get("status", "unknown"),
            start_date=parse_fhir_date(resource.get("authoredOn")),
            end_date=None,  # MedicationRequest doesn't reliably carry an end date
            prescriber=requester_ref.get("display"),
            indication=indication,
        )
