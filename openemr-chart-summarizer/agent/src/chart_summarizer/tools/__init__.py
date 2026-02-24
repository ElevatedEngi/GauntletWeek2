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

from typing import Optional

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


class ToolRegistry:
    """
    Registry for FHIR data retrieval tools.

    Holds the set of active tool instances and provides lookup by name.
    Supports disabling individual tools at construction time (e.g. when a
    particular FHIR resource type is not available in the deployment).

    Usage
    -----
    # Real FHIR tools (require live OpenEMR + OAuth credentials)
    registry = ToolRegistry.from_real(disabled_tools=["get_procedures"])

    # Mock tools (development / CI)
    registry = ToolRegistry.from_mock()

    tool = registry.get_tool("get_medications")
    all_tools = registry.get_all_tools()
    """

    def __init__(self, tools: list[FHIRTool]) -> None:
        self._tools: dict[str, FHIRTool] = {t.tool_name: t for t in tools}

    # ------------------------------------------------------------------
    # Factory methods
    # ------------------------------------------------------------------

    @classmethod
    def from_real(
        cls,
        fhir_base_url: Optional[str] = None,
        disabled_tools: Optional[list[str]] = None,
    ) -> "ToolRegistry":
        """
        Build a registry of all 9 real FHIR tools.

        Args:
            fhir_base_url:    Override for the FHIR base URL.
            disabled_tools:   List of tool names to exclude
                              (e.g. ``["get_procedures"]``).
        """
        disabled = set(disabled_tools or [])
        tools = [
            instance
            for cls_ in ALL_FHIR_TOOLS
            for instance in [cls_(fhir_base_url=fhir_base_url)]
            if instance.tool_name not in disabled
        ]
        return cls(tools)

    @classmethod
    def from_mock(
        cls,
        disabled_tools: Optional[list[str]] = None,
    ) -> "ToolRegistry":
        """
        Build a registry backed by mock (in-memory) tools — no real HTTP calls.

        Args:
            disabled_tools: List of tool names to exclude.
        """
        disabled = set(disabled_tools or [])
        tools = [t for t in create_mock_tools() if t.tool_name not in disabled]
        return cls(tools)

    # ------------------------------------------------------------------
    # Query API
    # ------------------------------------------------------------------

    def get_all_tools(self) -> list[FHIRTool]:
        """Return all registered tool instances in registration order."""
        return list(self._tools.values())

    def get_tool(self, name: str) -> FHIRTool:
        """
        Return a specific tool by its ``tool_name``.

        Args:
            name: Tool identifier, e.g. ``"get_medications"``.

        Raises:
            KeyError: If no tool with that name is registered.
        """
        if name not in self._tools:
            raise KeyError(
                f"Tool '{name}' is not registered. "
                f"Available tools: {sorted(self._tools)}"
            )
        return self._tools[name]

    def is_registered(self, name: str) -> bool:
        """Return True if a tool with the given name is registered."""
        return name in self._tools

    def tool_names(self) -> list[str]:
        """Return the names of all registered tools."""
        return list(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)

    def __repr__(self) -> str:
        return f"ToolRegistry(tools={self.tool_names()})"


__all__ = [
    # Base abstractions
    "FHIRTool",
    "ToolResult",
    # Registry
    "ToolRegistry",
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