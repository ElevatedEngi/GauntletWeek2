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
Smoke tests for all 9 mock FHIR tools.

These tests verify that:
  - Every tool returns success=True for all known patient IDs
  - Unknown patient IDs return success=False with a helpful error
  - Critical safety data (allergies, medications) is never empty for test patients
  - The ToolResult structure is correct
"""

import pytest

from chart_summarizer.tools.mock import (
    MOCK_PATIENT_IDS,
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


# ---------------------------------------------------------------------------
# Parametrized: every tool × every patient ID
# ---------------------------------------------------------------------------

ALL_TOOL_CLASSES = [
    MockGetPatientDemographicsTool,
    MockGetProblemListTool,
    MockGetMedicationsTool,
    MockGetAllergiesTool,
    MockGetLabResultsTool,
    MockGetVitalsHistoryTool,
    MockGetEncounterNotesTool,
    MockGetImmunizationsTool,
    MockGetProceduresTool,
]


@pytest.mark.parametrize("tool_cls", ALL_TOOL_CLASSES)
@pytest.mark.parametrize("patient_id", MOCK_PATIENT_IDS)
async def test_tool_succeeds_for_known_patient(tool_cls, patient_id):
    """Every tool must return success=True for all built-in patient IDs."""
    tool = tool_cls()
    result = await tool.execute(patient_id)

    assert result.success is True, (
        f"{tool_cls.__name__}.execute('{patient_id}') returned "
        f"success=False: {result.error_message}"
    )
    assert result.tool_name == tool.tool_name
    assert result.data is not None


@pytest.mark.parametrize("tool_cls", ALL_TOOL_CLASSES)
async def test_tool_fails_gracefully_for_unknown_patient(tool_cls):
    """Every tool must return success=False (not raise) for an unknown patient ID."""
    tool = tool_cls()
    result = await tool.execute("UNKNOWN-999")

    assert result.success is False
    assert result.error_message is not None
    assert "UNKNOWN-999" in result.error_message


# ---------------------------------------------------------------------------
# Safety-critical data checks
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("patient_id", MOCK_PATIENT_IDS)
async def test_allergies_always_present(patient_id):
    """Every test patient must have at least one allergy record."""
    tool = MockGetAllergiesTool()
    result = await tool.execute(patient_id)

    assert result.success is True
    assert isinstance(result.data, list)
    assert len(result.data) >= 1, (
        f"Patient {patient_id} has no allergy records — "
        "all test patients must have at least one allergy for safety testing."
    )


@pytest.mark.parametrize("patient_id", MOCK_PATIENT_IDS)
async def test_medications_always_present(patient_id):
    """Every test patient must have at least one medication record."""
    tool = MockGetMedicationsTool()
    result = await tool.execute(patient_id)

    assert result.success is True
    assert isinstance(result.data, list)
    assert len(result.data) >= 1, (
        f"Patient {patient_id} has no medication records — "
        "all test patients must have at least one medication for completeness testing."
    )


# ---------------------------------------------------------------------------
# Demographics structure check
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("patient_id", MOCK_PATIENT_IDS)
async def test_demographics_structure(patient_id):
    """Demographics must be a dict with required keys, not a list."""
    tool = MockGetPatientDemographicsTool()
    result = await tool.execute(patient_id)

    assert result.success is True
    assert isinstance(result.data, dict), "Demographics should be a single dict, not a list"
    assert result.records_returned == 1

    for required_key in ("patient_id", "first_name", "last_name", "date_of_birth", "sex"):
        assert required_key in result.data, f"Missing required key '{required_key}' in demographics"

    # Verify the returned patient_id matches what was requested
    assert result.data["patient_id"] == patient_id


# ---------------------------------------------------------------------------
# create_mock_tools factory
# ---------------------------------------------------------------------------

def test_create_mock_tools_returns_all_nine():
    """create_mock_tools() must return exactly 9 tool instances."""
    tools = create_mock_tools()
    assert len(tools) == 9


def test_create_mock_tools_unique_names():
    """Each tool must have a unique tool_name."""
    tools = create_mock_tools()
    names = [t.tool_name for t in tools]
    assert len(names) == len(set(names)), f"Duplicate tool names: {names}"


async def test_create_mock_tools_with_custom_fixtures():
    """create_mock_tools() should accept an override fixture dict."""
    custom_fixtures = {
        "CUSTOM-001": {
            "demographics": {
                "patient_id": "CUSTOM-001",
                "first_name": "Test",
                "last_name": "Patient",
                "date_of_birth": "2000-01-01",
                "sex": "Male",
            },
            "allergies": [
                {
                    "allergy_id": "ALG-C-01",
                    "substance": "Latex",
                    "reaction": "Contact dermatitis",
                    "severity": "mild",
                    "clinical_status": "active",
                    "verification_status": "confirmed",
                }
            ],
            "medications": [
                {
                    "medication_id": "MED-C-01",
                    "name": "Ibuprofen",
                    "dosage": "400 mg",
                    "frequency": "as needed",
                    "route": "oral",
                    "status": "active",
                }
            ],
            "conditions": [],
            "labs": [],
            "vitals": [],
            "encounters": [],
            "immunizations": [],
            "procedures": [],
        }
    }

    tools = create_mock_tools(patient_fixtures=custom_fixtures)
    demographics_tool = tools[0]  # MockGetPatientDemographicsTool is first

    result = await demographics_tool.execute("CUSTOM-001")
    assert result.success is True
    assert result.data["first_name"] == "Test"

    # Known IDs from the default fixtures should NOT be found
    result_unknown = await demographics_tool.execute("TEST-001")
    assert result_unknown.success is False
