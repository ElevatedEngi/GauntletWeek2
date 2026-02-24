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
Unit tests for FHIRClient.

Uses httpx.MockTransport to intercept HTTP calls — no real network required.
"""

import json
import pathlib
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from chart_summarizer.tools.fhir.client import FHIRClient, _TokenCache

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FIXTURE_DIR = pathlib.Path(__file__).parent.parent.parent / "fixtures" / "fhir"


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / name).read_text())


TOKEN = load_fixture("token_response.json")
PATIENT = load_fixture("patient.json")
EMPTY = load_fixture("empty_bundle.json")
ALLERGY_P1 = load_fixture("pagination_page1.json")
ALLERGY_P2 = load_fixture("pagination_page2.json")
ALLERGY_BUNDLE = load_fixture("allergy_bundle.json")


def make_response(status: int, body: Any) -> httpx.Response:
    content = json.dumps(body).encode()
    return httpx.Response(status, content=content, headers={"Content-Type": "application/json"})


def make_client(transport: httpx.MockTransport) -> FHIRClient:
    client = FHIRClient(
        fhir_base_url="http://fhir-server/fhir",
        client_id="client-id",
        client_secret="client-secret",
    )
    # No base_url: FHIRClient always builds absolute URLs, so we don't want
    # httpx to do any additional URL merging that could strip query params.
    client._http = httpx.AsyncClient(transport=transport)
    return client


# ---------------------------------------------------------------------------
# _TokenCache
# ---------------------------------------------------------------------------


class TestTokenCache:
    def test_initially_invalid(self) -> None:
        cache = _TokenCache()
        assert not cache.is_valid
        assert cache.get() is None

    def test_valid_after_store(self) -> None:
        cache = _TokenCache()
        cache.store("tok-123", expires_in=3600)
        assert cache.is_valid
        assert cache.get() == "tok-123"

    def test_invalidate_clears_token(self) -> None:
        cache = _TokenCache()
        cache.store("tok-123", expires_in=3600)
        cache.invalidate()
        assert not cache.is_valid
        assert cache.get() is None

    def test_expired_token_is_invalid(self) -> None:
        cache = _TokenCache()
        # expires_in=0 means it expired immediately (< buffer)
        cache.store("tok-old", expires_in=0)
        assert not cache.is_valid


# ---------------------------------------------------------------------------
# FHIRClient.get — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestFHIRClientGet:
    async def test_happy_path_patient(self) -> None:
        """GET /Patient/{id} returns patient resource."""
        call_count = 0

        def handler(req: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if "token" in str(req.url):
                return make_response(200, TOKEN)
            return make_response(200, PATIENT)

        client = make_client(httpx.MockTransport(handler))
        result = await client.get("/Patient/pid-001")
        await client.close()

        assert result["resourceType"] == "Patient"
        assert result["id"] == "pid-001"
        assert call_count == 2  # token + patient

    async def test_token_cached_between_requests(self) -> None:
        """Token is only fetched once even across multiple requests."""
        call_count = {"token": 0, "fhir": 0}

        def handler(req: httpx.Request) -> httpx.Response:
            if "token" in str(req.url):
                call_count["token"] += 1
                return make_response(200, TOKEN)
            call_count["fhir"] += 1
            return make_response(200, PATIENT)

        client = make_client(httpx.MockTransport(handler))
        await client.get("/Patient/pid-001")
        await client.get("/Patient/pid-001")
        await client.close()

        assert call_count["token"] == 1
        assert call_count["fhir"] == 2

    async def test_401_triggers_token_refresh(self) -> None:
        """A 401 response causes the token to be refreshed and request retried."""
        calls: list[str] = []

        def handler(req: httpx.Request) -> httpx.Response:
            url = str(req.url)
            calls.append(url)
            if "token" in url:
                return make_response(200, TOKEN)
            if len([c for c in calls if "Patient" in c]) == 1:
                # First patient request returns 401
                return make_response(401, {"error": "unauthorized"})
            return make_response(200, PATIENT)

        client = make_client(httpx.MockTransport(handler))
        result = await client.get("/Patient/pid-001")
        await client.close()

        assert result["resourceType"] == "Patient"
        patient_calls = [c for c in calls if "Patient" in c]
        assert len(patient_calls) == 2  # first failed, second succeeded

    async def test_500_retries_with_backoff(self) -> None:
        """Transient 500 errors are retried up to 3 times."""
        attempt = {"n": 0}

        def handler(req: httpx.Request) -> httpx.Response:
            if "token" in str(req.url):
                return make_response(200, TOKEN)
            attempt["n"] += 1
            if attempt["n"] < 3:
                return make_response(500, {"error": "internal"})
            return make_response(200, PATIENT)

        client = make_client(httpx.MockTransport(handler))
        # Patch asyncio.sleep so tests don't actually wait
        with patch("chart_summarizer.tools.fhir.client.asyncio.sleep", AsyncMock()):
            result = await client.get("/Patient/pid-001")
        await client.close()

        assert result["resourceType"] == "Patient"
        assert attempt["n"] == 3

    async def test_non_retryable_404_raises_immediately(self) -> None:
        """A 404 is not retried — raises HTTPStatusError immediately."""
        call_count = {"n": 0}

        def handler(req: httpx.Request) -> httpx.Response:
            if "token" in str(req.url):
                return make_response(200, TOKEN)
            call_count["n"] += 1
            return make_response(404, {"resourceType": "OperationOutcome"})

        client = make_client(httpx.MockTransport(handler))
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await client.get("/Patient/nonexistent")
        await client.close()

        assert exc_info.value.response.status_code == 404
        assert call_count["n"] == 1  # no retries

    async def test_empty_bundle_returned(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            if "token" in str(req.url):
                return make_response(200, TOKEN)
            return make_response(200, EMPTY)

        client = make_client(httpx.MockTransport(handler))
        result = await client.get("/AllergyIntolerance", {"patient": "pid-001"})
        await client.close()

        assert result["resourceType"] == "Bundle"
        assert result.get("entry") == []


# ---------------------------------------------------------------------------
# FHIRClient.paginate — multi-page
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestFHIRClientPaginate:
    async def test_single_page_no_next_link(self) -> None:
        """Single-page Bundle returns all resources without following any link."""

        def handler(req: httpx.Request) -> httpx.Response:
            if "token" in str(req.url):
                return make_response(200, TOKEN)
            return make_response(200, ALLERGY_BUNDLE)

        client = make_client(httpx.MockTransport(handler))
        resources = await client.paginate("/AllergyIntolerance", {"patient": "pid-001"})
        await client.close()

        assert len(resources) == 1
        assert resources[0]["resourceType"] == "AllergyIntolerance"
        assert resources[0]["id"] == "allergy-001"

    async def test_two_page_bundle_fully_aggregated(self) -> None:
        """Pagination follows 'next' link and aggregates all entries."""
        # Use a counter: first FHIR request → PAGE1, subsequent → PAGE2
        # (PAGE2 has no 'next' link so pagination stops after 2 pages)
        fhir_call: list[int] = [0]

        def handler(req: httpx.Request) -> httpx.Response:
            if "token" in str(req.url):
                return make_response(200, TOKEN)
            n = fhir_call[0]
            fhir_call[0] += 1
            return make_response(200, ALLERGY_P1 if n == 0 else ALLERGY_P2)

        client = make_client(httpx.MockTransport(handler))
        resources = await client.paginate("/AllergyIntolerance", {"patient": "pid-001"})
        await client.close()

        assert len(resources) == 4  # 2 from page1 + 2 from page2
        ids = [r["id"] for r in resources]
        assert "allergy-page1-001" in ids
        assert "allergy-page2-001" in ids

    async def test_empty_bundle_returns_empty_list(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            if "token" in str(req.url):
                return make_response(200, TOKEN)
            return make_response(200, EMPTY)

        client = make_client(httpx.MockTransport(handler))
        resources = await client.paginate("/MedicationRequest", {"patient": "none"})
        await client.close()

        assert resources == []


# ---------------------------------------------------------------------------
# FHIRClient._next_url
# ---------------------------------------------------------------------------


class TestNextUrl:
    def test_finds_next_link(self) -> None:
        bundle = {
            "link": [
                {"relation": "self", "url": "http://example.com/page1"},
                {"relation": "next", "url": "http://example.com/page2"},
            ]
        }
        assert FHIRClient._next_url(bundle) == "http://example.com/page2"

    def test_returns_none_when_no_next(self) -> None:
        bundle = {
            "link": [
                {"relation": "self", "url": "http://example.com/page1"},
            ]
        }
        assert FHIRClient._next_url(bundle) is None

    def test_returns_none_for_empty_link(self) -> None:
        assert FHIRClient._next_url({}) is None
        assert FHIRClient._next_url({"link": []}) is None
