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
Real FHIR R4 tool implementations.

All tools require _fhir_get() to be implemented in FHIRTool base class
(i.e. real OAuth is wired up) before they will function. Use the mock
tools from chart_summarizer.tools.mock during development.
"""

from typing import Optional

# Import shared utilities first (no circular dependency risk).
# Tool submodules import from ._utils directly to avoid a circular import
# with this __init__.py.
from chart_summarizer.tools.fhir._utils import (
    extract_bundle_entries,
    get_coding_code,
    get_coding_display,
    parse_fhir_date,
    parse_fhir_datetime,
)

# Import base class, then tool classes.
from chart_summarizer.tools.base import FHIRTool
from chart_summarizer.tools.fhir.get_allergies import GetAllergiesTool
from chart_summarizer.tools.fhir.get_demographics import GetPatientDemographicsTool
from chart_summarizer.tools.fhir.get_encounters import GetEncounterNotesTool
from chart_summarizer.tools.fhir.get_immunizations import GetImmunizationsTool
from chart_summarizer.tools.fhir.get_lab_results import GetLabResultsTool
from chart_summarizer.tools.fhir.get_medications import GetMedicationsTool
from chart_summarizer.tools.fhir.get_problem_list import GetProblemListTool
from chart_summarizer.tools.fhir.get_procedures import GetProceduresTool
from chart_summarizer.tools.fhir.get_vitals import GetVitalsHistoryTool

__all__ = [
    "GetPatientDemographicsTool",
    "GetProblemListTool",
    "GetMedicationsTool",
    "GetAllergiesTool",
    "GetLabResultsTool",
    "GetVitalsHistoryTool",
    "GetEncounterNotesTool",
    "GetImmunizationsTool",
    "GetProceduresTool",
    "ALL_FHIR_TOOLS",
    "create_fhir_tools",
    # Shared FHIR parsing utilities (re-exported for convenience)
    "extract_bundle_entries",
    "parse_fhir_date",
    "parse_fhir_datetime",
    "get_coding_display",
    "get_coding_code",
]

#: Ordered list of all 9 real FHIR tool classes.
ALL_FHIR_TOOLS: list[type[FHIRTool]] = [
    GetPatientDemographicsTool,
    GetProblemListTool,
    GetMedicationsTool,
    GetAllergiesTool,
    GetLabResultsTool,
    GetVitalsHistoryTool,
    GetEncounterNotesTool,
    GetImmunizationsTool,
    GetProceduresTool,
]


def create_fhir_tools(fhir_base_url: Optional[str] = None) -> list[FHIRTool]:
    """
    Instantiate and return all 9 real FHIR tools.

    Args:
        fhir_base_url: Optional override for the FHIR base URL.
                       Defaults to settings.OPENEMR_FHIR_BASE_URL.

    Returns:
        List of 9 FHIRTool instances ready to call the FHIR API.
        NOTE: _fhir_get() must be implemented before these will work.
    """
    return [cls(fhir_base_url=fhir_base_url) for cls in ALL_FHIR_TOOLS]
