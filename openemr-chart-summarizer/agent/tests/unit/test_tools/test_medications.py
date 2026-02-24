# Copyright (C) 2026 OpenEMR Community
# GPL v3 — see project root for full license text.

"""Unit tests for GetMedicationsTool."""

import json
import pathlib
from typing import Any
from unittest.mock import AsyncMock

import pytest

from chart_summarizer.tools.fhir.get_medications import GetMedicationsTool

FIXTURE_DIR = pathlib.Path(__file__).parent.parent.parent / "fixtures" / "fhir"


def load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / name).read_text())


BUNDLE = load("medication_bundle.json")
EMPTY = load("empty_bundle.json")


def make_tool() -> GetMedicationsTool:
    return GetMedicationsTool(fhir_base_url="http://test-fhir")


@pytest.mark.asyncio
class TestGetMedicationsTool:
    async def test_happy_path_two_meds(self) -> None:
        tool = make_tool()
        tool._fhir_get = AsyncMock(return_value=BUNDLE)

        result = await tool.execute("pid-001")

        assert result.success is True
        assert result.records_returned == 2
        names = [m["name"] for m in result.data]
        assert "Lisinopril 10 MG Oral Tablet" in names
        assert "Metformin 500 MG Oral Tablet" in names

    async def test_dosage_extracted(self) -> None:
        tool = make_tool()
        tool._fhir_get = AsyncMock(return_value=BUNDLE)
        result = await tool.execute("pid-001")
        lisinopril = next(m for m in result.data if "Lisinopril" in m["name"])
        assert lisinopril["dosage"] is not None
        assert "10" in str(lisinopril["dosage"])

    async def test_prescriber_extracted(self) -> None:
        tool = make_tool()
        tool._fhir_get = AsyncMock(return_value=BUNDLE)
        result = await tool.execute("pid-001")
        lisinopril = next(m for m in result.data if "Lisinopril" in m["name"])
        assert lisinopril["prescriber"] == "Dr. Sarah Smith"

    async def test_empty_bundle_returns_empty_list(self) -> None:
        tool = make_tool()
        tool._fhir_get = AsyncMock(return_value=EMPTY)
        result = await tool.execute("pid-001")
        assert result.success is True
        assert result.data == []

    async def test_401_returns_failure(self) -> None:
        import httpx
        tool = make_tool()
        tool._fhir_get = AsyncMock(
            side_effect=httpx.HTTPStatusError("401", request=None, response=httpx.Response(401))
        )
        result = await tool.execute("pid-001")
        assert result.success is False

    async def test_tool_name(self) -> None:
        assert make_tool().tool_name == "get_medications"

    async def test_rxnorm_code_extracted(self) -> None:
        tool = make_tool()
        tool._fhir_get = AsyncMock(return_value=BUNDLE)
        result = await tool.execute("pid-001")
        codes = [m.get("rxnorm_code") for m in result.data]
        assert "197361" in codes  # Lisinopril
