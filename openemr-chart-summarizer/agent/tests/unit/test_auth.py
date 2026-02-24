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
Unit tests for the verify_api_key / verify_token FastAPI dependencies.

Test strategy:
  - Patch settings to be fully isolated from .env files.
  - Use FastAPI's TestClient for end-to-end route-level auth tests.
  - Directly call verify_api_key() for unit tests of the dependency itself.

All route paths use the /api/v1 prefix (configured in main.py).
"""

from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import SecretStr

from chart_summarizer.api.auth import _cache, verify_api_key
from chart_summarizer.main import create_app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(headers: dict) -> object:
    """Build a minimal mock Request with the given headers."""
    from starlette.datastructures import Headers
    from starlette.requests import Request
    from starlette.types import Scope

    scope: Scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/summarize",
        "query_string": b"",
        "headers": Headers(headers=headers).raw,
    }
    return Request(scope)


def _patch_key(value: str):
    """Context manager: patch auth settings for a single test."""
    return patch(
        "chart_summarizer.api.auth.settings",
        AGENT_API_KEY=SecretStr(value),
        OPENEMR_OAUTH2_INTROSPECT_URL="",
    )


# ---------------------------------------------------------------------------
# Unit tests for verify_api_key dependency
# ---------------------------------------------------------------------------


class TestVerifyApiKeyDependency:
    def setup_method(self):
        # Clear the token cache before each test for isolation.
        _cache.clear()

    async def test_no_key_configured_skips_auth(self) -> None:
        """When AGENT_API_KEY is empty and no introspect URL, any request passes."""
        with _patch_key(""):
            request = _make_request({})
            await verify_api_key(request)  # must not raise

    async def test_no_key_configured_ignores_wrong_bearer(self) -> None:
        """Auth disabled means even a wrong token is accepted."""
        with _patch_key(""):
            request = _make_request({"authorization": "Bearer wrong"})
            await verify_api_key(request)  # must not raise

    async def test_valid_key_accepted(self) -> None:
        secret = "correct-secret-abc123"
        with _patch_key(secret):
            request = _make_request({"authorization": f"Bearer {secret}"})
            await verify_api_key(request)  # must not raise

    async def test_wrong_key_raises_401(self) -> None:
        with _patch_key("correct-secret"):
            request = _make_request({"authorization": "Bearer wrong-secret"})
            with pytest.raises(HTTPException) as exc_info:
                await verify_api_key(request)
            assert exc_info.value.status_code == 401

    async def test_missing_header_raises_401(self) -> None:
        with _patch_key("some-secret"):
            request = _make_request({})
            with pytest.raises(HTTPException) as exc_info:
                await verify_api_key(request)
            assert exc_info.value.status_code == 401

    async def test_malformed_header_not_bearer_raises_401(self) -> None:
        with _patch_key("some-secret"):
            request = _make_request({"authorization": "Basic dXNlcjpwYXNz"})
            with pytest.raises(HTTPException) as exc_info:
                await verify_api_key(request)
            assert exc_info.value.status_code == 401

    async def test_bearer_prefix_only_no_token_raises_401(self) -> None:
        with _patch_key("some-secret"):
            request = _make_request({"authorization": "Bearer "})
            with pytest.raises(HTTPException) as exc_info:
                await verify_api_key(request)
            assert exc_info.value.status_code == 401

    async def test_401_response_includes_www_authenticate(self) -> None:
        with _patch_key("some-secret"):
            request = _make_request({})
            with pytest.raises(HTTPException) as exc_info:
                await verify_api_key(request)
            assert "WWW-Authenticate" in exc_info.value.headers  # type: ignore[operator]

    async def test_case_sensitive_key(self) -> None:
        """Keys are case-sensitive."""
        with _patch_key("SecretKey"):
            request = _make_request({"authorization": "Bearer secretkey"})
            with pytest.raises(HTTPException):
                await verify_api_key(request)


# ---------------------------------------------------------------------------
# Integration tests — TestClient through the full FastAPI app
# ---------------------------------------------------------------------------


class TestSummarizeRouteAuth:
    """
    Tests that /api/v1/summarize returns 401 when auth is enforced, and
    200/500 (not 401) when a valid key is provided or auth is disabled.
    """

    def test_no_configured_key_allows_request(self) -> None:
        """With AGENT_API_KEY empty, /api/v1/summarize is reachable without a header."""
        app = create_app()
        with patch(
            "chart_summarizer.api.auth.settings",
            AGENT_API_KEY=SecretStr(""),
            OPENEMR_OAUTH2_INTROSPECT_URL="",
        ):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                "/api/v1/summarize",
                json={
                    "patient_id": "TEST-001",
                    "specialty": "primary_care",
                    "requested_sections": ["demographics"],
                },
            )
        # 200 (or 500 from pipeline) — either way, NOT 401
        assert resp.status_code != 401

    def test_configured_key_missing_header_returns_401(self) -> None:
        app = create_app()
        with patch(
            "chart_summarizer.api.auth.settings",
            AGENT_API_KEY=SecretStr("super-secret"),
            OPENEMR_OAUTH2_INTROSPECT_URL="",
        ):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                "/api/v1/summarize",
                json={"patient_id": "TEST-001", "specialty": "primary_care",
                      "requested_sections": ["demographics"]},
            )
        assert resp.status_code == 401

    def test_configured_key_wrong_token_returns_401(self) -> None:
        app = create_app()
        with patch(
            "chart_summarizer.api.auth.settings",
            AGENT_API_KEY=SecretStr("super-secret"),
            OPENEMR_OAUTH2_INTROSPECT_URL="",
        ):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                "/api/v1/summarize",
                headers={"Authorization": "Bearer wrong-token"},
                json={"patient_id": "TEST-001", "specialty": "primary_care",
                      "requested_sections": ["demographics"]},
            )
        assert resp.status_code == 401

    def test_configured_key_correct_token_passes_auth(self) -> None:
        app = create_app()
        secret = "super-secret"
        with patch(
            "chart_summarizer.api.auth.settings",
            AGENT_API_KEY=SecretStr(secret),
            OPENEMR_OAUTH2_INTROSPECT_URL="",
        ):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                "/api/v1/summarize",
                headers={"Authorization": f"Bearer {secret}"},
                json={"patient_id": "TEST-001", "specialty": "primary_care",
                      "requested_sections": ["demographics"]},
            )
        # Auth passed — not a 401
        assert resp.status_code != 401

    def test_health_endpoint_never_requires_auth(self) -> None:
        """GET /api/v1/health must be reachable without any Authorization header."""
        app = create_app()
        with patch(
            "chart_summarizer.api.auth.settings",
            AGENT_API_KEY=SecretStr("super-secret"),
            OPENEMR_OAUTH2_INTROSPECT_URL="",
        ):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/api/v1/health")
        assert resp.status_code == 200

    def test_config_endpoint_requires_auth_when_key_configured(self) -> None:
        """GET /api/v1/config requires auth (unlike the old unauthenticated /config)."""
        app = create_app()
        with patch(
            "chart_summarizer.api.auth.settings",
            AGENT_API_KEY=SecretStr("super-secret"),
            OPENEMR_OAUTH2_INTROSPECT_URL="",
        ):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/api/v1/config")
        assert resp.status_code == 401

    def test_config_endpoint_accessible_with_valid_token(self) -> None:
        """GET /api/v1/config succeeds with a correct Bearer token."""
        secret = "super-secret"
        app = create_app()
        with patch(
            "chart_summarizer.api.auth.settings",
            AGENT_API_KEY=SecretStr(secret),
            OPENEMR_OAUTH2_INTROSPECT_URL="",
        ):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/api/v1/config", headers={"Authorization": f"Bearer {secret}"})
        assert resp.status_code == 200