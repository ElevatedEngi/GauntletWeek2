# Copyright (C) 2026 OpenEMR Community
# GPL v3 — see project root for full license text.

"""Unit tests for GetImmunizationsTool."""

import json
import pathlib
from typing import Any
from unittest.mock import AsyncMock

import pytest

from chart_summarizer.tools.fhir.get_immunizations import GetImmunizationsTool

FIXTURE_DIR = pathlib.Path(__file__).parent.parent.parent / "fixtures" / "fhir"


def load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / name).read_text())


BUNDLE = load("immunization_bundle.json")
EMPTY = load("empty_bundle.json")


def make_tool() -> GetImmunizationsTool:
    return GetImmunizationsTool(fhir_base_url="http://test-fhir")


@pytest.mark.asyncio
class TestGetImmunizationsTool:
    async def test_happy_path_two_immunizations(self) -> None:
        tool = make_tool()
        tool._fhir_get = AsyncMock(return_value=BUNDLE)

        result = await tool.execute("pid-001")

        assert result.success is True
        assert result.records_returned == 2

    async def test_vaccine_names_extracted(self) -> None:
        tool = make_tool()
        tool._fhir_get = AsyncMock(return_value=BUNDLE)
        result = await tool.execute("pid-001")
        names = [imm["vaccine_name"] for imm in result.data]
        covid = any("COVID" in n for n in names)
        flu = any("Influenza" in n for n in names)
        assert covid or flu  # at least one matched

    async def test_dose_number_extracted(self) -> None:
        tool = make_tool()
        tool._fhir_get = AsyncMock(return_value=BUNDLE)
        result = await tool.execute("pid-001")
        # COVID-19 has dose_number=1
        covid = next(
            (i for i in result.data if "COVID" in (i.get("vaccine_name") or "")), None
        )
        if covid:
            assert str(covid.get("dose_number")) == "1"

    async def test_occurrence_date_extracted(self) -> None:
        tool = make_tool()
        tool._fhir_get = AsyncMock(return_value=BUNDLE)
        result = await tool.execute("pid-001")
        dates = [i.get("occurrence_date") for i in result.data]
        assert any(d is not None for d in dates)

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
        assert make_tool().tool_name == "get_immunizations"
