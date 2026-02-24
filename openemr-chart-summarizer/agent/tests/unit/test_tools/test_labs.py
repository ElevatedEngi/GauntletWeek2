# Copyright (C) 2026 OpenEMR Community
# GPL v3 — see project root for full license text.

"""Unit tests for GetLabResultsTool."""

import json
import pathlib
from typing import Any
from unittest.mock import AsyncMock

import pytest

from chart_summarizer.tools.fhir.get_lab_results import GetLabResultsTool

FIXTURE_DIR = pathlib.Path(__file__).parent.parent.parent / "fixtures" / "fhir"


def load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / name).read_text())


BUNDLE = load("lab_bundle.json")
EMPTY = load("empty_bundle.json")


def make_tool() -> GetLabResultsTool:
    return GetLabResultsTool(fhir_base_url="http://test-fhir")


@pytest.mark.asyncio
class TestGetLabResultsTool:
    async def test_happy_path_two_labs(self) -> None:
        tool = make_tool()
        tool._fhir_get = AsyncMock(return_value=BUNDLE)

        result = await tool.execute("pid-001")

        assert result.success is True
        assert result.records_returned == 2

    async def test_hba1c_parsed_correctly(self) -> None:
        tool = make_tool()
        tool._fhir_get = AsyncMock(return_value=BUNDLE)
        result = await tool.execute("pid-001")
        labs = {lab["test_name"]: lab for lab in result.data}
        hba1c_name = next((k for k in labs if "A1c" in k or "HbA1c" in k or "Hemoglobin" in k), None)
        assert hba1c_name is not None
        lab = labs[hba1c_name]
        assert float(lab["value"]) == pytest.approx(7.2)
        assert lab["unit"] == "%"

    async def test_abnormal_flag_set(self) -> None:
        tool = make_tool()
        tool._fhir_get = AsyncMock(return_value=BUNDLE)
        result = await tool.execute("pid-001")
        # HbA1c is flagged as High
        hba1c = next(
            (lab for lab in result.data if "interpretation" in lab and lab["interpretation"]),
            None
        )
        assert hba1c is not None

    async def test_reference_range_extracted(self) -> None:
        tool = make_tool()
        tool._fhir_get = AsyncMock(return_value=BUNDLE)
        result = await tool.execute("pid-001")
        ranges = [lab.get("reference_range") for lab in result.data]
        assert any(r is not None for r in ranges)

    async def test_empty_bundle_returns_empty_list(self) -> None:
        tool = make_tool()
        tool._fhir_get = AsyncMock(return_value=EMPTY)
        result = await tool.execute("pid-001")
        assert result.success is True
        assert result.data == []

    async def test_404_returns_failure(self) -> None:
        import httpx
        tool = make_tool()
        tool._fhir_get = AsyncMock(
            side_effect=httpx.HTTPStatusError("404", request=None, response=httpx.Response(404))
        )
        result = await tool.execute("pid-001")
        assert result.success is False

    async def test_tool_name(self) -> None:
        assert make_tool().tool_name == "get_lab_results"
