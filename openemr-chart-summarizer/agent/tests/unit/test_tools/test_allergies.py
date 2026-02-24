# Copyright (C) 2026 OpenEMR Community
# GPL v3 — see project root for full license text.

"""Unit tests for GetAllergiesTool."""

import json
import pathlib
from typing import Any
from unittest.mock import AsyncMock

import pytest

from chart_summarizer.tools.fhir.get_allergies import GetAllergiesTool

FIXTURE_DIR = pathlib.Path(__file__).parent.parent.parent / "fixtures" / "fhir"


def load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / name).read_text())


BUNDLE = load("allergy_bundle.json")
EMPTY = load("empty_bundle.json")
PAGE1 = load("pagination_page1.json")
PAGE2 = load("pagination_page2.json")


def make_tool() -> GetAllergiesTool:
    return GetAllergiesTool(fhir_base_url="http://test-fhir")


@pytest.mark.asyncio
class TestGetAllergiesTool:
    async def test_happy_path_penicillin(self) -> None:
        tool = make_tool()
        tool._fhir_get = AsyncMock(return_value=BUNDLE)

        result = await tool.execute("pid-001")

        assert result.success is True
        assert result.records_returned == 1
        allergy = result.data[0]
        assert allergy["substance"] == "Penicillin"
        assert allergy["severity"] == "moderate"
        assert allergy["clinical_status"] == "active"

    async def test_reaction_extracted(self) -> None:
        tool = make_tool()
        tool._fhir_get = AsyncMock(return_value=BUNDLE)
        result = await tool.execute("pid-001")
        assert result.data[0]["reaction"] == "Skin rash"

    async def test_verification_status_extracted(self) -> None:
        tool = make_tool()
        tool._fhir_get = AsyncMock(return_value=BUNDLE)
        result = await tool.execute("pid-001")
        assert result.data[0]["verification_status"] == "confirmed"

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
        assert make_tool().tool_name == "get_allergies"

    async def test_pagination_all_entries_returned(self) -> None:
        """When _fhir_get returns a merged bundle (pagination already handled by base),
        all entries are returned."""
        # Create a merged bundle with 4 entries (simulating what _fhir_get returns
        # after auto-following pagination)
        merged_bundle = {
            "resourceType": "Bundle",
            "type": "searchset",
            "total": 4,
            "entry": PAGE1["entry"] + PAGE2["entry"],
        }
        tool = make_tool()
        tool._fhir_get = AsyncMock(return_value=merged_bundle)
        result = await tool.execute("pid-001")
        assert result.success is True
        assert result.records_returned == 4
