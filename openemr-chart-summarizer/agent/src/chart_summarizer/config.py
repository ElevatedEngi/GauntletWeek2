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
Application configuration via Pydantic BaseSettings.

All settings are read from environment variables or a .env file.
No secrets should ever be hard-coded here.
"""

from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """
    Central configuration object for the Chart Summarizer Agent.

    Values are populated from environment variables (case-insensitive).
    Sensitive values use SecretStr to prevent accidental logging.
    """

    # --- LLM Configuration ---
    LLM_PROVIDER: Literal["anthropic", "openai", "local"] = Field(
        default="anthropic",
        description="LLM provider backend to use.",
    )
    LLM_MODEL: str = Field(
        default="claude-haiku-4-5-20251001",
        description="Model identifier for the selected LLM provider.",
    )
    LLM_API_KEY: SecretStr = Field(
        default=SecretStr(""),
        description="API key for the LLM provider. Never log this value.",
    )

    # --- OpenEMR FHIR Configuration ---
    OPENEMR_FHIR_BASE_URL: str = Field(
        default="http://localhost:8080/fhir",
        description="Base URL for the OpenEMR FHIR R4 API.",
    )
    OPENEMR_CLIENT_ID: str = Field(
        default="",
        description="OAuth2 client ID for OpenEMR API access.",
    )
    OPENEMR_CLIENT_SECRET: SecretStr = Field(
        default=SecretStr(""),
        description="OAuth2 client secret for OpenEMR API access.",
    )

    # --- Application Settings ---
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO",
        description="Logging verbosity level.",
    )
    AUDIT_LOG_ENABLED: bool = Field(
        default=True,
        description="Enable HIPAA-compliant audit logging for all summary requests.",
    )
    MAX_CONCURRENT_REQUESTS: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Maximum number of simultaneous summary requests.",
    )

    # --- Summary Behaviour ---
    SUMMARY_DEFAULT_MONTHS: int = Field(
        default=12,
        ge=1,
        le=120,
        description="Default look-back window in months when generating summaries.",
    )
    MAX_ENCOUNTERS_PER_SUMMARY: int = Field(
        default=50,
        ge=1,
        le=500,
        description="Cap on the number of encounters included in a single summary.",
    )

    # --- Agent API Authentication ---
    AGENT_API_KEY: SecretStr = Field(
        default=SecretStr(""),
        description=(
            "Shared secret for authenticating requests from the OpenEMR PHP module. "
            "When empty, authentication is disabled (development / CI only). "
            "In production, set to a cryptographically random string of 32+ characters."
        ),
    )

    # --- CORS ---
    CORS_ORIGINS: str = Field(
        default="*",
        description=(
            "Comma-separated list of allowed CORS origins. "
            "Use '*' (the default) for public demo deployments on Railway/Fly. "
            "In production with a known OpenEMR host, set to e.g. "
            "'https://openemr.example.com,http://localhost:8080'."
        ),
    )

    # --- HIPAA Mode ---
    HIPAA_MODE: bool = Field(
        default=True,
        description=(
            "When True, all PHI (names, DOBs, SSNs) is redacted from logs. "
            "Should always be True in production."
        ),
    )

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
    }


# Module-level singleton — import this throughout the application
settings = Settings()
