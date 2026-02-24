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
FHIR tool: GET patient allergies and adverse reactions.

FHIR resource: AllergyIntolerance
Endpoint:      GET /AllergyIntolerance?patient={id}

CRITICAL: All active allergies MUST appear in every generated summary.
Missing an allergy forces the verification confidence to RED.
"""

from typing import Any

from chart_summarizer.models.patient import Allergy
from chart_summarizer.tools.base import FHIRTool, ToolResult
from chart_summarizer.tools.fhir._utils import (
    extract_bundle_entries,
    get_coding_code,
    get_coding_display,
    parse_fhir_date,
)


class GetAllergiesTool(FHIRTool):
    """
    Retrieve allergy and adverse reaction records from FHIR AllergyIntolerance resources.

    Patient safety note: This tool intentionally fetches ALL allergy records
    (active, inactive, resolved) so that the verifier can confirm completeness.
    """

    @property
    def tool_name(self) -> str:
        return "get_allergies"

    @property
    def description(self) -> str:
        return (
            "Fetch all allergy and adverse reaction records including substance, "
            "reaction type, severity, and verification status."
        )

    async def execute(self, patient_id: str, **kwargs: Any) -> ToolResult:
        """
        Fetch AllergyIntolerance resources for the given patient.

        Fetches all statuses intentionally — the LLM prompt and verifier
        are responsible for highlighting active allergies.
        """
        try:
            bundle = await self._fhir_get(
                "/AllergyIntolerance",
                params={
                    "patient": patient_id,
                    "_sort": "-date",
                    "_count": "200",
                },
            )
            resources = extract_bundle_entries(bundle)
            allergies = [self._parse_allergy(r) for r in resources]
            return ToolResult(
                tool_name=self.tool_name,
                success=True,
                data=[a.model_dump(mode="json") for a in allergies],
                records_returned=len(allergies),
            )
        except NotImplementedError:
            raise
        except Exception as exc:
            return ToolResult(
                tool_name=self.tool_name,
                success=False,
                error_message=f"Failed to fetch allergies: {exc}",
            )

    def _parse_allergy(self, resource: dict[str, Any]) -> Allergy:
        """Map a FHIR AllergyIntolerance resource dict to an Allergy model."""
        substance_element = resource.get("code") or {}
        clinical_status_element = resource.get("clinicalStatus") or {}
        verification_status_element = resource.get("verificationStatus") or {}

        # Reaction: use the first reaction entry
        reactions = resource.get("reaction") or []
        reaction_text: str | None = None
        severity: str | None = None
        if reactions:
            first_reaction = reactions[0]
            manifestations = first_reaction.get("manifestation") or []
            if manifestations:
                reaction_text = get_coding_display(manifestations[0])
            severity = first_reaction.get("severity")

        return Allergy(
            allergy_id=resource.get("id", ""),
            substance=get_coding_display(substance_element, fallback="Unknown substance"),
            substance_code=get_coding_code(substance_element),
            reaction=reaction_text,
            severity=severity,
            clinical_status=get_coding_code(clinical_status_element) or "unknown",
            verification_status=get_coding_code(verification_status_element),
            recorded_date=parse_fhir_date(resource.get("recordedDate")),
        )
