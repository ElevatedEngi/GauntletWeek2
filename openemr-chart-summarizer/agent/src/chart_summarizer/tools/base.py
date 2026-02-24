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
Abstract base class for FHIR R4 data retrieval tools.

All 9 concrete tools (demographics, conditions, medications, allergies,
labs, vitals, encounters, immunizations, procedures) must extend FHIRTool.

All tools are READ-ONLY — no write operations are permitted.
"""

from abc import ABC, abstractmethod
from typing import Any, Optional

from pydantic import BaseModel

from chart_summarizer.config import settings


class ToolResult(BaseModel):
    """
    Standard result wrapper returned by every FHIR tool.

    Wraps the payload with success/failure status so that the graph pipeline
    can handle partial failures gracefully (e.g. labs unavailable → note in summary).
    """

    tool_name: str
    success: bool
    data: Optional[Any] = None
    error_message: Optional[str] = None
    http_status: Optional[int] = None
    records_returned: int = 0


class FHIRTool(ABC):
    """
    Abstract base class for all FHIR R4 data retrieval tools.

    Subclasses implement one specific FHIR query (e.g. get_medications).
    The base class handles shared concerns: OAuth token refresh, retry logic,
    and error normalisation.
    """

    def __init__(self, fhir_base_url: Optional[str] = None) -> None:
        """
        Initialise the tool with the FHIR base URL.

        Args:
            fhir_base_url: Override the default FHIR base URL from config.
                           Useful for injecting mock URLs in tests.
        """
        self._fhir_base_url = fhir_base_url or settings.OPENEMR_FHIR_BASE_URL
        # TODO: Initialise httpx.AsyncClient with OAuth2 bearer token refresh here

    # ------------------------------------------------------------------
    # Abstract interface
    # ------------------------------------------------------------------

    @property
    @abstractmethod
    def tool_name(self) -> str:
        """Short identifier for this tool (e.g. 'get_medications')."""
        raise NotImplementedError

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description of what this tool retrieves."""
        raise NotImplementedError

    @abstractmethod
    async def execute(self, patient_id: str, **kwargs: Any) -> ToolResult:
        """
        Fetch data for the given patient from the FHIR API.

        Args:
            patient_id: OpenEMR patient ID (PID).
            **kwargs: Tool-specific parameters (e.g. date_range, category).

        Returns:
            ToolResult with the retrieved data or a structured error.
        """
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Shared helpers (to be implemented)
    # ------------------------------------------------------------------

    async def _get_oauth_token(self) -> str:
        """
        Obtain or refresh the OAuth2 bearer token for the FHIR API.

        TODO: Implement client-credentials flow against OpenEMR's OAuth endpoint.
              Cache the token and refresh before expiry.
        """
        raise NotImplementedError("OAuth token retrieval is not yet implemented.")

    async def _fhir_get(self, path: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """
        Perform an authenticated GET request against the FHIR API.

        Handles 401 token refresh, 429 rate limiting (exponential backoff),
        and 500 server errors (retry ×2).

        TODO: Implement using httpx.AsyncClient with Authorization header.
        """
        raise NotImplementedError("FHIR HTTP client is not yet implemented.")
