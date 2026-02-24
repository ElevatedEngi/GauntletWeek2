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
Shared pytest fixtures for the entire test suite.

Provides:
  - Synthetic patient fixtures (simple, complex, pediatric, elderly, mental health)
  - Mock FHIR tool responses
  - FastAPI test client
  - Overridden settings for testing (HIPAA_MODE, mock API keys)
"""

from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chart_summarizer.config import Settings
from chart_summarizer.main import create_app
from chart_summarizer.models.patient import (
    Allergy,
    Condition,
    Encounter,
    LabResult,
    Medication,
    PatientDemographics,
    Procedure,
    VitalSign,
)
from chart_summarizer.models.summary import SummaryRequest


# ---------------------------------------------------------------------------
# Test settings override
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def test_settings() -> Settings:
    """
    Return a Settings instance configured for testing.

    Overrides LLM API key, FHIR URL, and disables audit logging.
    """
    return Settings(
        LLM_PROVIDER="anthropic",
        LLM_MODEL="claude-haiku-4-5-20251001",
        LLM_API_KEY="test-api-key-not-real",  # type: ignore[arg-type]
        OPENEMR_FHIR_BASE_URL="http://mock-fhir:8080/fhir",
        OPENEMR_CLIENT_ID="test-client",
        OPENEMR_CLIENT_SECRET="test-secret",  # type: ignore[arg-type]
        AUDIT_LOG_ENABLED=False,
        HIPAA_MODE=True,
    )


# ---------------------------------------------------------------------------
# FastAPI test client
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def client() -> TestClient:
    """Return a synchronous FastAPI TestClient for route testing."""
    app = create_app()
    return TestClient(app)


# ---------------------------------------------------------------------------
# Synthetic patient fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def simple_patient() -> PatientDemographics:
    """Healthy adult with minimal history — no chronic conditions."""
    return PatientDemographics(
        patient_id="TEST-001",
        first_name="Jane",
        last_name="Doe",
        date_of_birth=date(1990, 5, 15),
        sex="Female",
    )


@pytest.fixture
def complex_patient() -> PatientDemographics:
    """Elderly patient with multi-morbidity and polypharmacy."""
    return PatientDemographics(
        patient_id="TEST-002",
        first_name="Robert",
        last_name="Smith",
        date_of_birth=date(1942, 3, 20),
        sex="Male",
        primary_care_provider="Dr. Johnson",
    )


@pytest.fixture
def pediatric_patient() -> PatientDemographics:
    """Pediatric patient (child) — tests age-appropriate handling."""
    return PatientDemographics(
        patient_id="TEST-003",
        first_name="Emma",
        last_name="Wilson",
        date_of_birth=date(2018, 8, 10),
        sex="Female",
    )


# ---------------------------------------------------------------------------
# Mock FHIR tool response data
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_medications() -> list[Medication]:
    """Sample active medication list for unit testing."""
    return [
        Medication(
            medication_id="MED-001",
            name="Metformin",
            dosage="500 mg",
            frequency="twice daily",
            route="oral",
            status="active",
        ),
        Medication(
            medication_id="MED-002",
            name="Lisinopril",
            dosage="10 mg",
            frequency="once daily",
            route="oral",
            status="active",
        ),
    ]


@pytest.fixture
def mock_allergies() -> list[Allergy]:
    """Sample allergy records — MUST always appear in any generated summary."""
    return [
        Allergy(
            allergy_id="ALG-001",
            substance="Penicillin",
            reaction="Anaphylaxis",
            severity="severe",
            clinical_status="active",
            verification_status="confirmed",
        ),
    ]


@pytest.fixture
def mock_summary_request(simple_patient: PatientDemographics) -> SummaryRequest:
    """A basic SummaryRequest for the simple patient."""
    return SummaryRequest(
        patient_id=simple_patient.patient_id,
        specialty="primary_care",
        requesting_provider_id="PROV-001",
    )


@pytest.fixture
def mock_patient_data(
    mock_medications: list[Medication],
    mock_allergies: list[Allergy],
) -> dict[str, Any]:
    """
    A minimal mock patient_data dict as would be produced by the retrieve node.

    TODO: Expand with conditions, labs, vitals, encounters as tests are added.
    """
    return {
        "medications": [m.model_dump() for m in mock_medications],
        "allergies": [a.model_dump() for a in mock_allergies],
        "conditions": [],
        "labs": [],
        "vitals": [],
        "encounters": [],
        "immunizations": [],
        "procedures": [],
    }
