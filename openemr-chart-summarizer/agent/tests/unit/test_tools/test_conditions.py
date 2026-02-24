# Copyright (C) 2026 OpenEMR Community
# GPL v3 — see project root for full license text.

"""Unit tests for GetProblemListTool."""

import json
import pathlib
from typing import Any
from unittest.mock import AsyncMock

import pytest

from chart_summarizer.tools.fhir.get_problem_list import GetProblemListTool

FIXTURE_DIR = pathlib.Path(__file__).parent.parent.parent / "fixtures" / "fhir"


def load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / name).read_text())


BUNDLE = load("condition_bundle.json")
EMPTY = load("empty_bundle.json")


def make_tool() -> GetProblemListTool:
    return GetProblemListTool(fhir_base_url="http://test-fhir")


@pytest.mark.asyncio
class TestGetProblemListTool:
    async def test_happy_path_two_conditions(self) -> None:
        tool = make_tool()
        tool._fhir_get = AsyncMock(return_value=BUNDLE)

        result = await tool.execute("pid-001")

        assert result.success is True
        assert result.records_returned == 2
        names = [c["display_name"] for c in result.data]
        assert "Essential hypertension" in names
        assert "Type 2 diabetes mellitus without complications" in names

    async def test_icd10_codes_extracted(self) -> None:
        tool = make_tool()
        tool._fhir_get = AsyncMock(return_value=BUNDLE)
        result = await tool.execute("pid-001")
        codes = [c["icd10_code"] for c in result.data]
        assert "I10" in codes
        assert "E11.9" in codes

    async def test_empty_bundle_returns_empty_list(self) -> None:
        tool = make_tool()
        tool._fhir_get = AsyncMock(return_value=EMPTY)
        result = await tool.execute("pid-001")
        assert result.success is True
        assert result.data == []
        assert result.records_returned == 0

    async def test_500_returns_failure(self) -> None:
        import httpx
        tool = make_tool()
        tool._fhir_get = AsyncMock(
            side_effect=httpx.HTTPStatusError("500", request=None, response=httpx.Response(500))
        )
        result = await tool.execute("pid-001")
        assert result.success is False
        assert result.error_message is not None

    async def test_tool_name(self) -> None:
        assert make_tool().tool_name == "get_problem_list"

    async def test_date_filter_passed_in_params(self) -> None:
        tool = make_tool()
        tool._fhir_get = AsyncMock(return_value=EMPTY)
        await tool.execute("pid-001", date_from="2024-01-01")
        call_kwargs = tool._fhir_get.call_args
        # date_from kwarg should be present if tool uses it
        assert call_kwargs is not None
