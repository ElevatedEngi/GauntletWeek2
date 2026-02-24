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

"""FHIR R4 data retrieval tools for patient chart data (read-only)."""

from chart_summarizer.tools.base import FHIRTool, ToolResult
from chart_summarizer.tools.fhir import (
    ALL_FHIR_TOOLS,
    GetAllergiesTool,
    GetEncounterNotesTool,
    GetImmunizationsTool,
    GetLabResultsTool,
    GetMedicationsTool,
    GetPatientDemographicsTool,
    GetProblemListTool,
    GetProceduresTool,
    GetVitalsHistoryTool,
    create_fhir_tools,
)
from chart_summarizer.tools.mock import (
    ALL_MOCK_TOOLS,
    MOCK_PATIENT_IDS,
    MockFHIRTool,
    MockGetAllergiesTool,
    MockGetEncounterNotesTool,
    MockGetImmunizationsTool,
    MockGetLabResultsTool,
    MockGetMedicationsTool,
    MockGetPatientDemographicsTool,
    MockGetProblemListTool,
    MockGetProceduresTool,
    MockGetVitalsHistoryTool,
    create_mock_tools,
)

__all__ = [
    # Base abstractions
    "FHIRTool",
    "ToolResult",
    # Real FHIR tools (require live OpenEMR + OAuth)
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
    # Mock tools (development / testing)
    "MockFHIRTool",
    "MockGetPatientDemographicsTool",
    "MockGetProblemListTool",
    "MockGetMedicationsTool",
    "MockGetAllergiesTool",
    "MockGetLabResultsTool",
    "MockGetVitalsHistoryTool",
    "MockGetEncounterNotesTool",
    "MockGetImmunizationsTool",
    "MockGetProceduresTool",
    "ALL_MOCK_TOOLS",
    "MOCK_PATIENT_IDS",
    "create_mock_tools",
]
