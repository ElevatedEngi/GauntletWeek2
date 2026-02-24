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
Abstract base class for LLM provider implementations.

All concrete providers (Anthropic, OpenAI, local) must implement this interface.
This abstraction allows the rest of the application to be provider-agnostic.
"""

from abc import ABC, abstractmethod
from typing import Any, Optional, Type

from pydantic import BaseModel


class LLMResponse(BaseModel):
    """
    Normalised response returned by every LLM provider implementation.

    Wraps the raw provider response into a consistent structure so that
    the graph pipeline does not need to handle provider-specific formats.
    """

    content: str = ""
    tool_calls: Optional[list[dict[str, Any]]] = None
    finish_reason: Optional[str] = None
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""


class LLMProvider(ABC):
    """
    Abstract interface for LLM providers.

    Concrete implementations must override all abstract methods and properties.
    The factory (llm/factory.py) selects the appropriate implementation based
    on the LLM_PROVIDER configuration value.
    """

    # ------------------------------------------------------------------
    # Abstract properties — describe the model's capabilities
    # ------------------------------------------------------------------

    @property
    @abstractmethod
    def model_name(self) -> str:
        """The model identifier string (e.g. 'claude-haiku-4-5-20251001')."""
        raise NotImplementedError

    @property
    @abstractmethod
    def supports_tool_calling(self) -> bool:
        """True if the model natively supports tool / function calling."""
        raise NotImplementedError

    @property
    @abstractmethod
    def max_context_window(self) -> int:
        """Maximum context window in tokens for this model."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Abstract methods — generation interface
    # ------------------------------------------------------------------

    @abstractmethod
    async def generate(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
    ) -> LLMResponse:
        """
        Generate a response from the LLM.

        Args:
            system_prompt: System-level instructions for the LLM.
            messages: Conversation history in OpenAI-compatible format
                      [{"role": "user"|"assistant", "content": "..."}].
            tools: Optional list of tool definitions in the provider's schema.

        Returns:
            Normalised LLMResponse.
        """
        raise NotImplementedError

    @abstractmethod
    async def generate_structured(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        response_model: Type[BaseModel],
    ) -> BaseModel:
        """
        Generate a structured response conforming to a Pydantic model.

        Uses the provider's structured output / JSON mode feature where available,
        or falls back to prompt-based extraction.

        Args:
            system_prompt: System-level instructions.
            messages: Conversation history.
            response_model: Pydantic model class that defines the expected output shape.

        Returns:
            An instance of response_model populated with the LLM's output.
        """
        raise NotImplementedError
