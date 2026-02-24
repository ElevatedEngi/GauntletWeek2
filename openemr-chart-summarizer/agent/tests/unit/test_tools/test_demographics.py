# Copyright (C) 2026 OpenEMR Community
# GPL v3 — see project root for full license text.

"""Unit tests for GetPatientDemographicsTool."""

import json
import pathlib
from typing import Any
from unittest.mock import AsyncMock

import pytest

from chart_summarizer.tools.fhir.get_demographics import GetPatientDemographicsTool

FIXTURE_DIR = pathlib.Path(__file__).parent.parent.parent / "fixtures" / "fhir"


def load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / name).read_text())


PATIENT = load("patient.json")
EMPTY = load("empty_bundle.json")


def make_tool() -> GetPatientDemographicsTool:
    tool = GetPatientDemographicsTool(fhir_base_url="http://test-fhir")
    return tool


@pytest.mark.asyncio
class TestGetPatientDemographicsTool:
    async def test_happy_path_returns_demographics(self) -> None:
        tool = make_tool()
        tool._fhir_get = AsyncMock(return_value=PATIENT)

        result = await tool.execute("pid-001")

        assert result.success is True
        assert result.records_returned == 1
        data = result.data
        assert data["first_name"] == "Jane Marie"
        assert data["last_name"] == "Doe"
        assert data["sex"] == "Female"
        assert data["date_of_birth"] == "1989-03-15"
        assert data["race"] == "White"
        assert data["ethnicity"] == "Not Hispanic or Latino"
        assert data["primary_language"] == "English"

    async def test_tool_name(self) -> None:
        assert make_tool().tool_name == "get_patient_demographics"

    async def test_description_not_empty(self) -> None:
        assert len(make_tool().description) > 10

    async def test_fhir_path_includes_patient_id(self) -> None:
        tool = make_tool()
        tool._fhir_get = AsyncMock(return_value=PATIENT)
        await tool.execute("pid-999")
        tool._fhir_get.assert_called_once_with("/Patient/pid-999")

    async def test_patient_not_found_returns_failure(self) -> None:
        import httpx
        tool = make_tool()
        tool._fhir_get = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "404", request=None, response=httpx.Response(404)
            )
        )
        result = await tool.execute("pid-missing")
        assert result.success is False
        assert result.error_message is not None

    async def test_minimal_patient_no_extensions(self) -> None:
        minimal = {
            "resourceType": "Patient",
            "id": "pid-min",
            "name": [{"family": "Smith", "given": ["Bob"]}],
            "birthDate": "1970-01-01",
            "gender": "male",
        }
        tool = make_tool()
        tool._fhir_get = AsyncMock(return_value=minimal)
        result = await tool.execute("pid-min")

        assert result.success is True
        data = result.data
        assert data["first_name"] == "Bob"
        assert data["last_name"] == "Smith"
        assert data["race"] is None
        assert data["ethnicity"] is None
        assert data["primary_language"] is None
