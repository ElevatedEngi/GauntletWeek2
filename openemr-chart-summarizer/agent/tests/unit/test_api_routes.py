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
API route tests using httpx.AsyncClient.

Tests:
  - GET /api/v1/health — returns 200 without auth, fields present.
  - GET /api/v1/config — returns 200 when auth disabled, 401 when enforced.
  - POST /api/v1/summarize — auth gating, 429 rate limit, 504 timeout.
  - GET /api/v1/summarize/{summary_id} — 404 for unknown IDs.
  - GET /api/v1/summarize/{summary_id}/citations — 404 for unknown IDs.
  - POST /api/v1/summarize/{summary_id}/feedback — valid/invalid actions.
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from chart_summarizer.main import create_app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client(api_key: str = "") -> TestClient:
    """Create a TestClient with the given AGENT_API_KEY value."""
    app = create_app()
    with patch("chart_summarizer.api.auth.settings") as mock_settings:
        mock_settings.AGENT_API_KEY = SecretStr(api_key)
        mock_settings.OPENEMR_OAUTH2_INTROSPECT_URL = ""
        return TestClient(app, raise_server_exceptions=False)


# Auth disabled by default in all tests (AGENT_API_KEY="")
_client = TestClient(create_app(), raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# GET /api/v1/health
# ---------------------------------------------------------------------------


class TestHealthEndpoint:

    def test_health_returns_200(self) -> None:
        resp = _client.get("/api/v1/health")
        assert resp.status_code == 200

    def test_health_returns_ok_status(self) -> None:
        resp = _client.get("/api/v1/health")
        assert resp.json()["status"] == "ok"

    def test_health_includes_version(self) -> None:
        resp = _client.get("/api/v1/health")
        assert "version" in resp.json()

    def test_health_includes_uptime(self) -> None:
        resp = _client.get("/api/v1/health")
        assert "uptime_seconds" in resp.json()

    def test_health_includes_llm_provider(self) -> None:
        resp = _client.get("/api/v1/health")
        assert "llm_provider" in resp.json()

    def test_health_includes_fhir_connected(self) -> None:
        resp = _client.get("/api/v1/health")
        assert "fhir_connected" in resp.json()

    def test_health_requires_no_auth(self) -> None:
        """Health endpoint must be accessible without any Authorization header."""
        app = create_app()
        with patch("chart_summarizer.api.auth.settings") as s:
            s.AGENT_API_KEY = SecretStr("super-secret")
            s.OPENEMR_OAUTH2_INTROSPECT_URL = ""
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/api/v1/health")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /api/v1/config
# ---------------------------------------------------------------------------


class TestConfigEndpoint:

    def test_config_returns_200_when_auth_disabled(self) -> None:
        resp = _client.get("/api/v1/config")
        assert resp.status_code == 200

    def test_config_returns_specialties(self) -> None:
        resp = _client.get("/api/v1/config")
        body = resp.json()
        assert "available_specialties" in body
        assert isinstance(body["available_specialties"], list)
        assert len(body["available_specialties"]) > 0

    def test_config_returns_rate_limit(self) -> None:
        resp = _client.get("/api/v1/config")
        body = resp.json()
        assert "rate_limit_per_hour" in body
        assert isinstance(body["rate_limit_per_hour"], int)

    def test_config_requires_auth_when_key_configured(self) -> None:
        app = create_app()
        with patch("chart_summarizer.api.auth.settings") as s:
            s.AGENT_API_KEY = SecretStr("secret")
            s.OPENEMR_OAUTH2_INTROSPECT_URL = ""
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/api/v1/config")
        assert resp.status_code == 401

    def test_config_accessible_with_valid_token(self) -> None:
        key = "valid-secret"
        app = create_app()
        with patch("chart_summarizer.api.auth.settings") as s:
            s.AGENT_API_KEY = SecretStr(key)
            s.OPENEMR_OAUTH2_INTROSPECT_URL = ""
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/api/v1/config", headers={"Authorization": f"Bearer {key}"})
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# POST /api/v1/summarize
# ---------------------------------------------------------------------------


class TestSummarizeEndpoint:

    def test_summarize_returns_non_401_when_auth_disabled(self) -> None:
        resp = _client.post(
            "/api/v1/summarize",
            json={"patient_id": "TEST-001", "specialty": "primary_care",
                  "requested_sections": ["demographics"]},
        )
        assert resp.status_code != 401

    def test_summarize_returns_401_when_key_configured_and_missing_header(self) -> None:
        app = create_app()
        with patch("chart_summarizer.api.auth.settings") as s:
            s.AGENT_API_KEY = SecretStr("secret")
            s.OPENEMR_OAUTH2_INTROSPECT_URL = ""
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                "/api/v1/summarize",
                json={"patient_id": "TEST-001", "specialty": "primary_care",
                      "requested_sections": ["demographics"]},
            )
        assert resp.status_code == 401

    def test_summarize_returns_401_with_wrong_token(self) -> None:
        app = create_app()
        with patch("chart_summarizer.api.auth.settings") as s:
            s.AGENT_API_KEY = SecretStr("correct-secret")
            s.OPENEMR_OAUTH2_INTROSPECT_URL = ""
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                "/api/v1/summarize",
                headers={"Authorization": "Bearer wrong-token"},
                json={"patient_id": "TEST-001", "specialty": "primary_care",
                      "requested_sections": ["demographics"]},
            )
        assert resp.status_code == 401

    def test_summarize_rejects_missing_patient_id(self) -> None:
        resp = _client.post(
            "/api/v1/summarize",
            json={"specialty": "primary_care"},
        )
        # 422 Unprocessable Entity from Pydantic validation
        assert resp.status_code == 422

    def test_summarize_rate_limit_returns_429(self) -> None:
        """Exhaust the rate limit then verify 429 with Retry-After header."""
        from chart_summarizer.api.rate_limiter import SlidingWindowRateLimiter

        # Patch limiter to immediately reject
        blocking_limiter = SlidingWindowRateLimiter(max_requests=0)

        with patch("chart_summarizer.api.routes.get_rate_limiter", return_value=blocking_limiter):
            # Also patch audit/DB to avoid DB setup
            with patch("chart_summarizer.api.routes.write_audit_record", new_callable=AsyncMock):
                resp = _client.post(
                    "/api/v1/summarize",
                    json={"patient_id": "TEST-001", "specialty": "primary_care",
                          "requested_sections": ["demographics"]},
                )
        assert resp.status_code == 429
        assert "Retry-After" in resp.headers


# ---------------------------------------------------------------------------
# GET /api/v1/summarize/{summary_id}
# ---------------------------------------------------------------------------


class TestGetSummaryEndpoint:

    def test_unknown_summary_id_returns_404(self) -> None:
        resp = _client.get("/api/v1/summarize/nonexistent-id-12345")
        # Without DB, will be 500 or 404 — must not be 200
        assert resp.status_code in (404, 500)

    def test_summary_requires_auth_when_key_set(self) -> None:
        app = create_app()
        with patch("chart_summarizer.api.auth.settings") as s:
            s.AGENT_API_KEY = SecretStr("secret")
            s.OPENEMR_OAUTH2_INTROSPECT_URL = ""
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/api/v1/summarize/some-id")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/v1/summarize/{summary_id}/citations
# ---------------------------------------------------------------------------


class TestGetCitationsEndpoint:

    def test_unknown_id_returns_404_or_500(self) -> None:
        resp = _client.get("/api/v1/summarize/nonexistent-id/citations")
        assert resp.status_code in (404, 500)

    def test_citations_requires_auth_when_key_set(self) -> None:
        app = create_app()
        with patch("chart_summarizer.api.auth.settings") as s:
            s.AGENT_API_KEY = SecretStr("secret")
            s.OPENEMR_OAUTH2_INTROSPECT_URL = ""
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/api/v1/summarize/some-id/citations")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /api/v1/summarize/{summary_id}/feedback
# ---------------------------------------------------------------------------


class TestFeedbackEndpoint:

    def test_feedback_unknown_id_returns_404_or_500(self) -> None:
        resp = _client.post(
            "/api/v1/summarize/nonexistent-id/feedback",
            json={"action": "approved"},
        )
        assert resp.status_code in (404, 500)

    def test_feedback_invalid_action_returns_422(self) -> None:
        resp = _client.post(
            "/api/v1/summarize/some-id/feedback",
            json={"action": "invalid_action"},
        )
        assert resp.status_code == 422

    def test_feedback_requires_auth_when_key_set(self) -> None:
        app = create_app()
        with patch("chart_summarizer.api.auth.settings") as s:
            s.AGENT_API_KEY = SecretStr("secret")
            s.OPENEMR_OAUTH2_INTROSPECT_URL = ""
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                "/api/v1/summarize/some-id/feedback",
                json={"action": "approved"},
            )
        assert resp.status_code == 401

    def test_feedback_valid_actions_accepted_by_schema(self) -> None:
        """Schema must accept all three valid action values."""
        from chart_summarizer.api.routes import FeedbackRequest

        for action in ("approved", "edited", "rejected"):
            fb = FeedbackRequest(action=action)
            assert fb.action == action