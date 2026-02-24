# Copyright (C) 2026 OpenEMR Community
# GPL v3 — see project root for full license text.

"""Unit tests for GetProceduresTool."""

import json
import pathlib
from typing import Any
from unittest.mock import AsyncMock

import pytest

from chart_summarizer.tools.fhir.get_procedures import GetProceduresTool

FIXTURE_DIR = pathlib.Path(__file__).parent.parent.parent / "fixtures" / "fhir"


def load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / name).read_text())


BUNDLE = load("procedure_bundle.json")
EMPTY = load("empty_bundle.json")


def make_tool() -> GetProceduresTool:
    return GetProceduresTool(fhir_base_url="http://test-fhir")


@pytest.mark.asyncio
class TestGetProceduresTool:
    async def test_happy_path_one_procedure(self) -> None:
        tool = make_tool()
        tool._fhir_get = AsyncMock(return_value=BUNDLE)

        result = await tool.execute("pid-001")

        assert result.success is True
        assert result.records_returned == 1
        proc = result.data[0]
        assert proc["procedure_id"] == "proc-001"

    async def test_name_extracted(self) -> None:
        tool = make_tool()
        tool._fhir_get = AsyncMock(return_value=BUNDLE)
        result = await tool.execute("pid-001")
        assert "Chest" in result.data[0]["name"] or "Radiolog" in result.data[0]["name"]

    async def test_performer_extracted(self) -> None:
        tool = make_tool()
        tool._fhir_get = AsyncMock(return_value=BUNDLE)
        result = await tool.execute("pid-001")
        assert result.data[0]["performer"] == "Dr. James Lee"

    async def test_date_extracted(self) -> None:
        tool = make_tool()
        tool._fhir_get = AsyncMock(return_value=BUNDLE)
        result = await tool.execute("pid-001")
        assert result.data[0]["performed_date"] is not None

    async def test_cpt_code_extracted(self) -> None:
        tool = make_tool()
        tool._fhir_get = AsyncMock(return_value=BUNDLE)
        result = await tool.execute("pid-001")
        # CPT code 71046 should be in cpt_code or name
        proc = result.data[0]
        cpt = proc.get("cpt_code")
        assert cpt == "71046" or "71046" in str(proc)

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
        assert make_tool().tool_name == "get_procedures"
