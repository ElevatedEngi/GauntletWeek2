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
Summary request and response models.

These models define the API contract for requesting a chart summary and
interpreting the result, including verification metadata.
"""

import re
from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

# Allowed specialty values — must match ConfigResponse.available_specialties in routes.py.
ALLOWED_SPECIALTIES: frozenset[str] = frozenset({
    "primary_care",
    "cardiology",
    "psychiatry",
    "pediatrics",
    "neurology",
    "oncology",
    "endocrinology",
    "nephrology",
    "internal_medicine",
    "emergency_medicine",
    "surgery",
    "obstetrics",
})


class DateRange(BaseModel):
    """Inclusive date range for filtering patient data."""

    start: date = Field(description="Start date (inclusive).")
    end: date = Field(description="End date (inclusive).")


class SummaryRequest(BaseModel):
    """
    Incoming request to generate a patient chart summary.

    The requesting provider specifies the patient, their specialty context,
    the data time window, and which sections to include.
    """

    patient_id: str = Field(
        description="OpenEMR internal patient ID (PID). Required — never use name-based lookup."
    )
    specialty: str = Field(
        default="primary_care",
        description=(
            "Requesting provider's specialty context used to tailor the summary. "
            "E.g. 'primary_care', 'cardiology', 'psychiatry', 'pediatrics'."
        ),
    )
    date_range_months: Optional[int] = Field(
        default=12,
        ge=1,
        le=120,
        description=(
            "Look-back window in months from today. "
            "Used when date_range is not explicitly specified. "
            "Clamped to [1, 120]."
        ),
    )
    date_range: Optional[DateRange] = Field(
        default=None,
        description=(
            "Explicit date range to restrict data retrieval. "
            "When provided, overrides date_range_months. "
            "Defaults to the last date_range_months months when omitted."
        ),
    )
    requested_sections: list[str] = Field(
        default_factory=lambda: [
            "demographics",
            "conditions",
            "medications",
            "allergies",
            "labs",
            "vitals",
            "encounters",
            "immunizations",
            "procedures",
        ],
        description="Which data sections to include in the summary.",
    )
    requesting_provider_id: Optional[str] = Field(
        default=None,
        description="Provider user ID — stored in the audit log.",
    )

    @field_validator("patient_id")
    @classmethod
    def validate_patient_id(cls, v: str) -> str:
        """Patient ID must be a numeric OpenEMR PID or a short alphanumeric ID."""
        v = v.strip()
        if not v:
            raise ValueError("patient_id cannot be empty")
        if not re.match(r"^[\w-]{1,64}$", v):
            raise ValueError(
                "patient_id must be 1–64 alphanumeric characters, underscores, or hyphens"
            )
        return v

    @field_validator("specialty")
    @classmethod
    def validate_specialty(cls, v: str) -> str:
        """Specialty must be from the allowed list to prevent prompt injection."""
        v = v.strip()
        if v not in ALLOWED_SPECIALTIES:
            raise ValueError(
                f"specialty must be one of: {sorted(ALLOWED_SPECIALTIES)}"
            )
        return v


class Citation(BaseModel):
    """A traceable link between a summary claim and its source record."""

    claim_text: str = Field(description="The exact statement made in the summary.")
    source_type: str = Field(
        description="Resource type: encounter | lab | medication | condition | allergy | etc."
    )
    source_id: str = Field(description="ID of the FHIR resource that supports this claim.")
    source_date: Optional[date] = Field(default=None)
    verified: bool = Field(default=False, description="True if the claim was verified post-generation.")


class VerificationResult(BaseModel):
    """
    Output of the post-generation fact-checking step.

    Every clinical claim in the summary is validated against the source FHIR data.
    """

    verified_claims: list[Citation] = Field(
        default_factory=list,
        description="Claims that were successfully matched to a source record.",
    )
    unverified_claims: list[Citation] = Field(
        default_factory=list,
        description="Claims that could not be traced to a source record — must be flagged.",
    )
    confidence_score: float = Field(
        ge=0.0,
        le=1.0,
        description="Fraction of claims that were verified (verified / total).",
    )
    confidence_level: Literal["GREEN", "YELLOW", "RED"] = Field(
        description=(
            "GREEN = ≥95% verified, YELLOW = 90–94%, RED = <90% or critical data missing."
        )
    )
    flags: list[str] = Field(
        default_factory=list,
        description="Human-readable warnings (e.g. 'Allergy data unavailable', 'Conflicting medication lists').",
    )
    verified_at: datetime = Field(default_factory=datetime.utcnow)


class SummaryMetadata(BaseModel):
    """Provenance and performance metadata attached to every summary response."""

    request_id: str = Field(description="Unique ID for this summary request (for audit log).")
    patient_id: str
    model_used: str
    provider: str = Field(description="LLM provider name (anthropic | openai | local).")
    input_tokens: int = Field(default=0)
    output_tokens: int = Field(default=0)
    latency_ms: int = Field(default=0, description="End-to-end generation time in milliseconds.")
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    data_sections_retrieved: list[str] = Field(default_factory=list)
    specialty_context: str = Field(default="primary_care")


class SummaryResponse(BaseModel):
    """
    The complete output of a chart summary generation request.

    The summary is always presented as a DRAFT for clinician review.
    It must never be auto-inserted into the medical record.
    """

    summary_text: str = Field(
        description="The AI-generated clinical summary in Markdown format."
    )
    html_summary: Optional[str] = Field(
        default=None,
        description=(
            "HTML-formatted version of the summary for embedding in the OpenEMR UI. "
            "Includes confidence badge, warning banners, and disclaimer."
        ),
    )
    citations: list[Citation] = Field(
        default_factory=list,
        description="All inline citations linking summary claims to source records.",
    )
    confidence_level: Literal["GREEN", "YELLOW", "RED"] = Field(
        description="Overall confidence indicator based on verification results."
    )
    metadata: SummaryMetadata
    verification_result: VerificationResult
    status: Literal["complete", "partial", "failed"] = Field(
        default="complete",
        description=(
            "complete = all sections retrieved; partial = some data unavailable; "
            "failed = unable to generate."
        ),
    )
    disclaimer: str = Field(
        default=(
            "⚠️ This summary was generated by AI and requires clinician review before "
            "clinical use. Do not act on this summary without verifying against the "
            "original patient record."
        )
    )
