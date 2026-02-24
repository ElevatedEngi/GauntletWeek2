# Copyright (C) 2026 OpenEMR Community
# GPL v3 — see project root for full license text.

"""Unit tests for ToolRegistry."""

import pytest

from chart_summarizer.tools import ToolRegistry


class TestToolRegistry:
    def test_from_mock_returns_9_tools(self) -> None:
        registry = ToolRegistry.from_mock()
        assert len(registry) == 9

    def test_get_all_tools_returns_list(self) -> None:
        registry = ToolRegistry.from_mock()
        tools = registry.get_all_tools()
        assert isinstance(tools, list)
        assert len(tools) == 9

    def test_get_tool_by_name(self) -> None:
        registry = ToolRegistry.from_mock()
        tool = registry.get_tool("get_medications")
        assert tool.tool_name == "get_medications"

    def test_get_unknown_tool_raises_key_error(self) -> None:
        registry = ToolRegistry.from_mock()
        with pytest.raises(KeyError, match="not registered"):
            registry.get_tool("nonexistent_tool")

    def test_is_registered_true(self) -> None:
        registry = ToolRegistry.from_mock()
        assert registry.is_registered("get_allergies") is True

    def test_is_registered_false(self) -> None:
        registry = ToolRegistry.from_mock()
        assert registry.is_registered("fly_to_moon") is False

    def test_tool_names_returns_9_names(self) -> None:
        registry = ToolRegistry.from_mock()
        names = registry.tool_names()
        assert len(names) == 9
        assert "get_medications" in names
        assert "get_allergies" in names
        assert "get_patient_demographics" in names

    def test_disabled_tools_excluded(self) -> None:
        registry = ToolRegistry.from_mock(disabled_tools=["get_procedures", "get_immunizations"])
        assert len(registry) == 7
        assert not registry.is_registered("get_procedures")
        assert not registry.is_registered("get_immunizations")
        assert registry.is_registered("get_medications")

    def test_repr_contains_tool_names(self) -> None:
        registry = ToolRegistry.from_mock()
        r = repr(registry)
        assert "ToolRegistry" in r
        assert "get_medications" in r

    def test_all_tools_have_descriptions(self) -> None:
        registry = ToolRegistry.from_mock()
        for tool in registry.get_all_tools():
            assert len(tool.description) > 5, f"Tool {tool.tool_name} has no description"
