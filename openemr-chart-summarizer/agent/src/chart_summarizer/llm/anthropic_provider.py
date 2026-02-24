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
Anthropic Claude provider implementation.

Primary LLM provider for the Chart Summarizer. Anthropic offers a HIPAA BAA
and zero-data-retention option required for PHI processing.

Default model: claude-haiku-4-5-20251001 (best cost/quality for structured summaries).
Upgrade to claude-sonnet-4-6 or claude-opus-4-6 for complex cases.
"""

import asyncio
import json
import logging
from typing import Any, Optional, Type

import anthropic
from pydantic import BaseModel

from chart_summarizer.llm.base import LLMProvider, LLMResponse

logger = logging.getLogger(__name__)

# Maximum number of attempts for transient errors (rate limits, server errors).
_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 1.0  # seconds; doubles on each retry


class AnthropicProvider(LLMProvider):
    """
    Concrete LLM provider backed by Anthropic's Claude models.

    Requires a valid Anthropic API key with HIPAA-eligible access enabled.
    """

    def __init__(self, model: str, api_key: str) -> None:
        """
        Initialise the Anthropic provider.

        Args:
            model: Anthropic model identifier (e.g. 'claude-haiku-4-5-20251001').
            api_key: Anthropic API key. Must not be logged or stored in plaintext.
        """
        self._model = model
        self._client = anthropic.AsyncAnthropic(api_key=api_key)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def model_name(self) -> str:
        """Return the configured Anthropic model identifier."""
        return self._model

    @property
    def supports_tool_calling(self) -> bool:
        """Claude models support native tool use."""
        return True

    @property
    def max_context_window(self) -> int:
        """
        Return the approximate context window in tokens.

        claude-haiku-4-5 supports 200K tokens; use 128K as a conservative limit.
        """
        return 128_000

    # ------------------------------------------------------------------
    # Generation methods
    # ------------------------------------------------------------------

    async def generate(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
    ) -> LLMResponse:
        """
        Call the Anthropic Messages API and return a normalised LLMResponse.

        Retries up to _MAX_RETRIES times on rate-limit and server errors
        using exponential back-off.

        Args:
            system_prompt: System-level instructions for Claude.
            messages: Conversation history in [{"role": ..., "content": ...}] format.
            tools: Optional list of tool definitions in Anthropic tool schema format.

        Returns:
            Normalised LLMResponse.

        Raises:
            anthropic.APIError: After exhausting retries.
        """
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": 4096,
            "system": system_prompt,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools

        last_exc: Exception = RuntimeError("No attempts made")
        delay = _RETRY_BASE_DELAY
        for attempt in range(_MAX_RETRIES):
            try:
                response = await self._client.messages.create(**kwargs)
                return self._normalise_response(response)
            except anthropic.RateLimitError as exc:
                last_exc = exc
                logger.warning(
                    "Anthropic rate limit hit (attempt %d/%d); retrying in %.1fs",
                    attempt + 1,
                    _MAX_RETRIES,
                    delay,
                )
                await asyncio.sleep(delay)
                delay *= 2
            except anthropic.InternalServerError as exc:
                last_exc = exc
                logger.warning(
                    "Anthropic server error (attempt %d/%d); retrying in %.1fs",
                    attempt + 1,
                    _MAX_RETRIES,
                    delay,
                )
                await asyncio.sleep(delay)
                delay *= 2
            except anthropic.APIError:
                raise  # Non-retryable API errors bubble up immediately

        raise last_exc

    async def generate_structured(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        response_model: Type[BaseModel],
    ) -> BaseModel:
        """
        Use Claude's tool-use feature to generate a structured response.

        Defines a synthetic tool whose JSON schema mirrors ``response_model``,
        forces the model to call that tool, then parses the arguments back
        into the Pydantic model.

        Args:
            system_prompt: System-level instructions.
            messages: Conversation history.
            response_model: Pydantic model class defining the expected output shape.

        Returns:
            An instance of response_model populated by the LLM's output.
        """
        tool_name = "structured_output"
        tool_def: dict[str, Any] = {
            "name": tool_name,
            "description": (
                f"Return a structured {response_model.__name__} object "
                "with fields exactly as specified."
            ),
            "input_schema": response_model.model_json_schema(),
        }

        llm_resp = await self.generate(
            system_prompt=system_prompt,
            messages=messages,
            tools=[tool_def],
        )

        # Extract the first tool_use block's input
        if not llm_resp.tool_calls:
            raise ValueError(
                f"Claude did not call the '{tool_name}' tool; "
                f"raw response: {llm_resp.content!r}"
            )

        tool_input = llm_resp.tool_calls[0].get("input", {})
        return response_model.model_validate(tool_input)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _normalise_response(self, response: Any) -> LLMResponse:
        """Convert a raw Anthropic Message object to LLMResponse."""
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []

        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                # Preserve the raw tool_use block as a dict
                tool_calls.append(
                    {
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    }
                )

        return LLMResponse(
            content="\n".join(text_parts),
            tool_calls=tool_calls if tool_calls else None,
            finish_reason=response.stop_reason,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            model=response.model,
        )
