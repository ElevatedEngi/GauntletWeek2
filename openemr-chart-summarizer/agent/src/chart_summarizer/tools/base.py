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
    The base class owns a lazily-initialised FHIRClient and exposes
    ``_fhir_get()`` and ``_fhir_paginate()`` helpers to subclasses.
    """

    def __init__(self, fhir_base_url: Optional[str] = None) -> None:
        """
        Initialise the tool with the FHIR base URL.

        The FHIRClient is created lazily on the first request so that
        import-time instantiation (e.g. during test collection) does not
        require OpenEMR credentials to be present in the environment.

        Args:
            fhir_base_url: Override the default FHIR base URL from config.
                           Useful for injecting test URLs.
        """
        self._fhir_base_url = fhir_base_url or settings.OPENEMR_FHIR_BASE_URL
        self._client: Any = None  # Lazily initialised FHIRClient

    # ------------------------------------------------------------------
    # Internal: shared client
    # ------------------------------------------------------------------

    def _get_client(self) -> Any:
        """Return (or lazily create) the shared FHIRClient for this tool."""
        if self._client is None:
            from chart_summarizer.tools.fhir.client import FHIRClient

            self._client = FHIRClient(
                fhir_base_url=self._fhir_base_url,
                client_id=settings.OPENEMR_CLIENT_ID,
                client_secret=settings.OPENEMR_CLIENT_SECRET.get_secret_value(),
            )
        return self._client

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
    # Shared FHIR helpers
    # ------------------------------------------------------------------

    async def _fhir_get(
        self,
        path: str,
        params: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """
        Authenticated GET request to the FHIR API.

        For Bundle responses the method transparently follows all
        ``link[rel=next]`` pagination links (up to 10 pages) and returns a
        synthetic merged Bundle so that callers never see a truncated result.

        For non-Bundle responses (e.g. ``/Patient/{id}``), returns the raw
        resource dict unchanged.

        Args:
            path:   URL path relative to the FHIR base (e.g. ``/Patient/123``).
            params: Optional FHIR search parameters.

        Returns:
            Parsed JSON dict (merged Bundle or individual resource).
        """
        client = self._get_client()
        first_page = await client.get(path, params)

        if first_page.get("resourceType") != "Bundle":
            # Individual resource (e.g. Patient) — return as-is
            return first_page

        # Bundle: check if there are more pages
        if not client._next_url(first_page):
            # Single-page Bundle — no pagination needed
            return first_page

        # Multi-page: collect first page entries then use paginate() for rest
        all_entries: list[dict[str, Any]] = list(first_page.get("entry") or [])

        # paginate() returns flat resource list starting from page 1;
        # we already have page 1, so re-paginate from the next link directly.
        # Build a merged entry list: wrap resources in entry dicts for compatibility
        # with extract_bundle_entries().
        next_url = client._next_url(first_page)
        page_count = 1
        from chart_summarizer.tools.fhir.client import _MAX_PAGES

        while next_url and page_count < _MAX_PAGES:
            headers = await client._auth_headers()
            import asyncio
            async with client._semaphore:
                page = await client._get_with_retry(next_url, headers, {})
            for entry in page.get("entry") or []:
                if "resource" in entry:
                    all_entries.append(entry)
            next_url = client._next_url(page)
            page_count += 1

        return {**first_page, "entry": all_entries}

    async def _fhir_paginate(
        self,
        path: str,
        params: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        """
        Follow FHIR Bundle pagination and return a flat list of resource dicts.

        Convenience wrapper around FHIRClient.paginate() for tools that prefer
        working directly with resource lists rather than Bundle dicts.

        Args:
            path:   Search path, e.g. ``/MedicationRequest``.
            params: FHIR search parameters.

        Returns:
            Flat list of FHIR resource dicts from all pages.
        """
        return await self._get_client().paginate(path, params)
