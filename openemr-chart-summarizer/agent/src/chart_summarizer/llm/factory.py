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
LLM provider factory.

Reads LLM_PROVIDER from application config and returns the appropriate
concrete LLMProvider implementation. Callers never need to import a
specific provider directly.
"""

from chart_summarizer.config import settings
from chart_summarizer.llm.base import LLMProvider


def create_llm_provider() -> LLMProvider:
    """
    Instantiate and return the configured LLM provider.

    Reads LLM_PROVIDER from settings and constructs the matching implementation.
    Raises ValueError for unknown provider values.

    Returns:
        A concrete LLMProvider instance ready for use.

    Raises:
        ValueError: If LLM_PROVIDER is not one of 'anthropic', 'openai', 'local'.
    """
    provider = settings.LLM_PROVIDER.lower()

    if provider == "anthropic":
        from chart_summarizer.llm.anthropic_provider import AnthropicProvider

        return AnthropicProvider(
            model=settings.LLM_MODEL,
            api_key=settings.LLM_API_KEY.get_secret_value(),
        )

    if provider == "openai":
        from chart_summarizer.llm.openai_provider import OpenAIProvider

        return OpenAIProvider(
            model=settings.LLM_MODEL,
            api_key=settings.LLM_API_KEY.get_secret_value(),
        )

    if provider == "local":
        # Placeholder for future local model support (e.g. Ollama / llama.cpp)
        raise NotImplementedError(
            "Local LLM provider is not yet implemented. "
            "Set LLM_PROVIDER to 'anthropic' or 'openai'."
        )

    raise ValueError(
        f"Unknown LLM_PROVIDER '{provider}'. "
        "Valid options are: 'anthropic', 'openai', 'local'."
    )
