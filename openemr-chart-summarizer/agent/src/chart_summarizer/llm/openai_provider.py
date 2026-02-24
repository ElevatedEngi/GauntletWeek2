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
OpenAI provider implementation.

Fallback LLM provider used when Anthropic is unavailable (rate-limited or down).
OpenAI also offers a HIPAA BAA required for PHI processing.

Default model: gpt-4o-mini (comparable cost/quality to Claude Haiku).
"""

import asyncio
import json
import logging
from typing import Any, Optional, Type

import openai
from pydantic import BaseModel

from chart_summarizer.llm.base import LLMProvider, LLMResponse

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 1.0  # seconds; doubles on each retry


class OpenAIProvider(LLMProvider):
    """
    Concrete LLM provider backed by OpenAI's GPT models.

    Used as a fallback when the Anthropic provider is unavailable.
    Requires a valid OpenAI API key with HIPAA-eligible access.
    """

    def __init__(self, model: str, api_key: str) -> None:
        """
        Initialise the OpenAI provider.

        Args:
            model: OpenAI model identifier (e.g. 'gpt-4o-mini').
            api_key: OpenAI API key. Must not be logged or stored in plaintext.
        """
        self._model = model
        self._client = openai.AsyncOpenAI(api_key=api_key)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def model_name(self) -> str:
        """Return the configured OpenAI model identifier."""
        return self._model

    @property
    def supports_tool_calling(self) -> bool:
        """GPT-4o and GPT-4o-mini support native function/tool calling."""
        return True

    @property
    def max_context_window(self) -> int:
        """
        Return the approximate context window in tokens.

        gpt-4o-mini supports 128K tokens.
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
        Call the OpenAI Chat Completions API and return a normalised LLMResponse.

        Prepends the system_prompt as a system message. Retries up to
        _MAX_RETRIES times on rate-limit and server errors.

        Args:
            system_prompt: System-level instructions prepended as a system message.
            messages: Conversation history in [{"role": ..., "content": ...}] format.
            tools: Optional list of tool definitions in OpenAI function-call format.

        Returns:
            Normalised LLMResponse.

        Raises:
            openai.APIError: After exhausting retries.
        """
        full_messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            *messages,
        ]
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": full_messages,
        }
        if tools:
            kwargs["tools"] = tools

        last_exc: Exception = RuntimeError("No attempts made")
        delay = _RETRY_BASE_DELAY
        for attempt in range(_MAX_RETRIES):
            try:
                response = await self._client.chat.completions.create(**kwargs)
                return self._normalise_response(response)
            except openai.RateLimitError as exc:
                last_exc = exc
                logger.warning(
                    "OpenAI rate limit hit (attempt %d/%d); retrying in %.1fs",
                    attempt + 1,
                    _MAX_RETRIES,
                    delay,
                )
                await asyncio.sleep(delay)
                delay *= 2
            except openai.InternalServerError as exc:
                last_exc = exc
                logger.warning(
                    "OpenAI server error (attempt %d/%d); retrying in %.1fs",
                    attempt + 1,
                    _MAX_RETRIES,
                    delay,
                )
                await asyncio.sleep(delay)
                delay *= 2
            except openai.APIError:
                raise

        raise last_exc

    async def generate_structured(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        response_model: Type[BaseModel],
    ) -> BaseModel:
        """
        Use OpenAI's structured outputs (JSON schema mode) to generate a typed response.

        Passes ``response_model.model_json_schema()`` as ``response_format`` so the
        model is constrained to emit valid JSON matching the schema. The result is
        parsed back into the Pydantic model via ``model_validate_json()``.

        Args:
            system_prompt: System-level instructions.
            messages: Conversation history.
            response_model: Pydantic model class defining the expected output shape.

        Returns:
            An instance of response_model populated by the LLM's output.
        """
        full_messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            *messages,
        ]

        response = await self._client.chat.completions.create(
            model=self._model,
            messages=full_messages,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": response_model.__name__,
                    "strict": True,
                    "schema": response_model.model_json_schema(),
                },
            },
        )

        content = response.choices[0].message.content or ""
        return response_model.model_validate_json(content)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _normalise_response(self, response: Any) -> LLMResponse:
        """Convert a raw OpenAI ChatCompletion object to LLMResponse."""
        choice = response.choices[0]
        message = choice.message

        text_content = message.content or ""
        tool_calls: list[dict[str, Any]] = []

        if message.tool_calls:
            for tc in message.tool_calls:
                try:
                    arguments = json.loads(tc.function.arguments)
                except (json.JSONDecodeError, AttributeError):
                    arguments = {}
                tool_calls.append(
                    {
                        "id": tc.id,
                        "name": tc.function.name,
                        "input": arguments,
                    }
                )

        usage = response.usage
        return LLMResponse(
            content=text_content,
            tool_calls=tool_calls if tool_calls else None,
            finish_reason=choice.finish_reason,
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            model=response.model,
        )
