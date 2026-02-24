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
Unit tests for AnthropicProvider and OpenAIProvider.

All tests mock the underlying SDK clients so no real API calls are made.
"""

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel

from chart_summarizer.llm.anthropic_provider import AnthropicProvider
from chart_summarizer.llm.base import LLMResponse
from chart_summarizer.llm.openai_provider import OpenAIProvider


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


class _SimpleModel(BaseModel):
    name: str
    score: int


def _make_anthropic_response(
    text: str = "Hello",
    tool_name: str | None = None,
    tool_input: dict[str, Any] | None = None,
    stop_reason: str = "end_turn",
    input_tokens: int = 10,
    output_tokens: int = 20,
    model: str = "claude-haiku-4-5-20251001",
) -> MagicMock:
    """Build a mock Anthropic Message response."""
    resp = MagicMock()
    resp.stop_reason = stop_reason
    resp.model = model
    resp.usage = MagicMock(input_tokens=input_tokens, output_tokens=output_tokens)

    content_blocks = []
    if text:
        tb = MagicMock()
        tb.type = "text"
        tb.text = text
        content_blocks.append(tb)
    if tool_name:
        tub = MagicMock()
        tub.type = "tool_use"
        tub.id = "toolu_01"
        tub.name = tool_name
        tub.input = tool_input or {}
        content_blocks.append(tub)

    resp.content = content_blocks
    return resp


def _make_openai_response(
    text: str = "Hello",
    tool_name: str | None = None,
    tool_args: dict[str, Any] | None = None,
    finish_reason: str = "stop",
    prompt_tokens: int = 10,
    completion_tokens: int = 20,
    model: str = "gpt-4o-mini",
) -> MagicMock:
    """Build a mock OpenAI ChatCompletion response."""
    resp = MagicMock()
    resp.model = model
    resp.usage = MagicMock(
        prompt_tokens=prompt_tokens, completion_tokens=completion_tokens
    )

    message = MagicMock()
    message.content = text
    message.tool_calls = None

    if tool_name:
        tc = MagicMock()
        tc.id = "call_01"
        tc.function = MagicMock()
        tc.function.name = tool_name
        tc.function.arguments = json.dumps(tool_args or {})
        message.tool_calls = [tc]

    choice = MagicMock()
    choice.message = message
    choice.finish_reason = finish_reason
    resp.choices = [choice]
    return resp


# ---------------------------------------------------------------------------
# AnthropicProvider tests
# ---------------------------------------------------------------------------


class TestAnthropicProvider:
    """Tests for AnthropicProvider."""

    def _make_provider(self) -> AnthropicProvider:
        with patch("chart_summarizer.llm.anthropic_provider.anthropic.AsyncAnthropic"):
            return AnthropicProvider(model="claude-haiku-4-5-20251001", api_key="test")

    def test_properties(self) -> None:
        provider = self._make_provider()
        assert provider.model_name == "claude-haiku-4-5-20251001"
        assert provider.supports_tool_calling is True
        assert provider.max_context_window == 128_000

    async def test_generate_returns_normalised_response(self) -> None:
        provider = self._make_provider()
        raw = _make_anthropic_response(text="Summary text", output_tokens=42)
        provider._client.messages.create = AsyncMock(return_value=raw)

        result = await provider.generate(
            system_prompt="You are a clinician.",
            messages=[{"role": "user", "content": "Summarise"}],
        )

        assert isinstance(result, LLMResponse)
        assert result.content == "Summary text"
        assert result.output_tokens == 42
        assert result.finish_reason == "end_turn"
        assert result.tool_calls is None

    async def test_generate_passes_tools_kwarg(self) -> None:
        provider = self._make_provider()
        raw = _make_anthropic_response()
        provider._client.messages.create = AsyncMock(return_value=raw)
        tools = [{"name": "my_tool", "description": "does stuff", "input_schema": {}}]

        await provider.generate(
            system_prompt="sys",
            messages=[{"role": "user", "content": "hi"}],
            tools=tools,
        )

        call_kwargs = provider._client.messages.create.call_args.kwargs
        assert call_kwargs["tools"] == tools

    async def test_generate_maps_tool_use_blocks(self) -> None:
        provider = self._make_provider()
        raw = _make_anthropic_response(
            text="",
            tool_name="structured_output",
            tool_input={"name": "Alice", "score": 99},
        )
        provider._client.messages.create = AsyncMock(return_value=raw)

        result = await provider.generate(
            system_prompt="sys",
            messages=[{"role": "user", "content": "hi"}],
        )

        assert result.tool_calls is not None
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0]["name"] == "structured_output"
        assert result.tool_calls[0]["input"] == {"name": "Alice", "score": 99}

    async def test_generate_structured_returns_pydantic_model(self) -> None:
        provider = self._make_provider()
        raw = _make_anthropic_response(
            text="",
            tool_name="structured_output",
            tool_input={"name": "Bob", "score": 7},
        )
        provider._client.messages.create = AsyncMock(return_value=raw)

        result = await provider.generate_structured(
            system_prompt="sys",
            messages=[{"role": "user", "content": "go"}],
            response_model=_SimpleModel,
        )

        assert isinstance(result, _SimpleModel)
        assert result.name == "Bob"
        assert result.score == 7

    async def test_generate_structured_raises_if_no_tool_call(self) -> None:
        provider = self._make_provider()
        raw = _make_anthropic_response(text="oops no tool")
        provider._client.messages.create = AsyncMock(return_value=raw)

        with pytest.raises(ValueError, match="structured_output"):
            await provider.generate_structured(
                system_prompt="sys",
                messages=[{"role": "user", "content": "go"}],
                response_model=_SimpleModel,
            )

    async def test_generate_retries_on_rate_limit(self) -> None:
        import anthropic as anthropic_sdk

        provider = self._make_provider()
        raw = _make_anthropic_response(text="ok")

        call_count = 0

        async def _side_effect(**kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise anthropic_sdk.RateLimitError(
                    message="rate limit",
                    response=MagicMock(status_code=429),
                    body={},
                )
            return raw

        provider._client.messages.create = _side_effect

        with patch("chart_summarizer.llm.anthropic_provider.asyncio.sleep", new=AsyncMock()):
            result = await provider.generate(
                system_prompt="sys",
                messages=[{"role": "user", "content": "hi"}],
            )

        assert result.content == "ok"
        assert call_count == 3


# ---------------------------------------------------------------------------
# OpenAIProvider tests
# ---------------------------------------------------------------------------


class TestOpenAIProvider:
    """Tests for OpenAIProvider."""

    def _make_provider(self) -> OpenAIProvider:
        with patch("chart_summarizer.llm.openai_provider.openai.AsyncOpenAI"):
            return OpenAIProvider(model="gpt-4o-mini", api_key="test")

    def test_properties(self) -> None:
        provider = self._make_provider()
        assert provider.model_name == "gpt-4o-mini"
        assert provider.supports_tool_calling is True
        assert provider.max_context_window == 128_000

    async def test_generate_returns_normalised_response(self) -> None:
        provider = self._make_provider()
        raw = _make_openai_response(text="OpenAI answer", completion_tokens=55)
        provider._client.chat.completions.create = AsyncMock(return_value=raw)

        result = await provider.generate(
            system_prompt="You are a clinician.",
            messages=[{"role": "user", "content": "Summarise"}],
        )

        assert isinstance(result, LLMResponse)
        assert result.content == "OpenAI answer"
        assert result.output_tokens == 55
        assert result.finish_reason == "stop"
        assert result.tool_calls is None

    async def test_generate_prepends_system_message(self) -> None:
        provider = self._make_provider()
        raw = _make_openai_response()
        provider._client.chat.completions.create = AsyncMock(return_value=raw)

        await provider.generate(
            system_prompt="Be helpful",
            messages=[{"role": "user", "content": "hi"}],
        )

        call_kwargs = provider._client.chat.completions.create.call_args.kwargs
        messages = call_kwargs["messages"]
        assert messages[0] == {"role": "system", "content": "Be helpful"}
        assert messages[1] == {"role": "user", "content": "hi"}

    async def test_generate_maps_tool_calls(self) -> None:
        provider = self._make_provider()
        raw = _make_openai_response(
            text="",
            tool_name="get_data",
            tool_args={"patient_id": "42"},
        )
        provider._client.chat.completions.create = AsyncMock(return_value=raw)

        result = await provider.generate(
            system_prompt="sys",
            messages=[{"role": "user", "content": "hi"}],
        )

        assert result.tool_calls is not None
        assert result.tool_calls[0]["name"] == "get_data"
        assert result.tool_calls[0]["input"] == {"patient_id": "42"}

    async def test_generate_structured_returns_pydantic_model(self) -> None:
        provider = self._make_provider()
        json_str = json.dumps({"name": "Carol", "score": 5})
        raw = _make_openai_response(text=json_str)
        provider._client.chat.completions.create = AsyncMock(return_value=raw)

        result = await provider.generate_structured(
            system_prompt="sys",
            messages=[{"role": "user", "content": "go"}],
            response_model=_SimpleModel,
        )

        assert isinstance(result, _SimpleModel)
        assert result.name == "Carol"
        assert result.score == 5

    async def test_generate_retries_on_rate_limit(self) -> None:
        import openai as openai_sdk

        provider = self._make_provider()
        raw = _make_openai_response(text="ok")

        call_count = 0

        async def _side_effect(**kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise openai_sdk.RateLimitError(
                    message="rate limit",
                    response=MagicMock(status_code=429, headers={}),
                    body={},
                )
            return raw

        provider._client.chat.completions.create = _side_effect

        with patch("chart_summarizer.llm.openai_provider.asyncio.sleep", new=AsyncMock()):
            result = await provider.generate(
                system_prompt="sys",
                messages=[{"role": "user", "content": "hi"}],
            )

        assert result.content == "ok"
        assert call_count == 3
