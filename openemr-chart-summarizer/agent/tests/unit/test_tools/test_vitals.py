# Copyright (C) 2026 OpenEMR Community
# GPL v3 — see project root for full license text.

"""Unit tests for GetVitalsHistoryTool."""

import json
import pathlib
from typing import Any
from unittest.mock import AsyncMock

import pytest

from chart_summarizer.tools.fhir.get_vitals import GetVitalsHistoryTool

FIXTURE_DIR = pathlib.Path(__file__).parent.parent.parent / "fixtures" / "fhir"


def load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / name).read_text())


BUNDLE = load("vital_bundle.json")
EMPTY = load("empty_bundle.json")


def make_tool() -> GetVitalsHistoryTool:
    return GetVitalsHistoryTool(fhir_base_url="http://test-fhir")


@pytest.mark.asyncio
class TestGetVitalsHistoryTool:
    async def test_happy_path_two_vitals(self) -> None:
        tool = make_tool()
        tool._fhir_get = AsyncMock(return_value=BUNDLE)

        result = await tool.execute("pid-001")

        assert result.success is True
        assert result.records_returned >= 1

    async def test_blood_pressure_parsed(self) -> None:
        tool = make_tool()
        tool._fhir_get = AsyncMock(return_value=BUNDLE)
        result = await tool.execute("pid-001")
        # BP may split into systolic/diastolic components or combined
        types = [v["type"] for v in result.data]
        # Tool uses hyphenated LOINC-mapped labels: "blood-pressure"
        bp_found = any(
            "blood" in t.lower() or "systolic" in t.lower() or "diastolic" in t.lower()
            for t in types
        )
        assert bp_found

    async def test_heart_rate_parsed(self) -> None:
        tool = make_tool()
        tool._fhir_get = AsyncMock(return_value=BUNDLE)
        result = await tool.execute("pid-001")
        types = [v["type"] for v in result.data]
        # Tool uses hyphenated LOINC-mapped label: "heart-rate"
        hr_found = any("heart" in t.lower() or "pulse" in t.lower() for t in types)
        assert hr_found

    async def test_empty_bundle_returns_empty_list(self) -> None:
        tool = make_tool()
        tool._fhir_get = AsyncMock(return_value=EMPTY)
        result = await tool.execute("pid-001")
        assert result.success is True
        assert result.data == []

    async def test_500_returns_failure(self) -> None:
        import httpx
        tool = make_tool()
        tool._fhir_get = AsyncMock(
            side_effect=httpx.HTTPStatusError("500", request=None, response=httpx.Response(500))
        )
        result = await tool.execute("pid-001")
        assert result.success is False

    async def test_tool_name(self) -> None:
        assert make_tool().tool_name == "get_vitals_history"
