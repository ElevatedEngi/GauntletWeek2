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
LangGraph state definition for the Chart Summarizer pipeline.

SummarizerState is the single shared data structure that flows through every
node.  Each node reads what it needs and writes back only its outputs.
"""

from typing import Optional

from typing import TypedDict

from chart_summarizer.models.patient import (
    Allergy,
    Condition,
    Encounter,
    Immunization,
    LabResult,
    Medication,
    PatientDemographics,
    Procedure,
    VitalSign,
)
from chart_summarizer.models.summary import SummaryResponse, VerificationResult


class SummarizerState(TypedDict):
    # ── Input ────────────────────────────────────────────────────────────────
    patient_id: str
    specialty: str              # "primary_care", "cardiology", "psychiatry", etc.
    date_range_months: int      # How far back to look
    requested_sections: Optional[list[str]]  # None = all sections

    # ── Data retrieval results (populated by retrieve_data_node) ─────────────
    demographics: Optional[PatientDemographics]
    conditions: list[Condition]
    medications: list[Medication]
    allergies: list[Allergy]
    lab_results: list[LabResult]
    vitals: list[VitalSign]
    encounters: list[Encounter]
    immunizations: list[Immunization]
    procedures: list[Procedure]

    # ── Processing ───────────────────────────────────────────────────────────
    structured_context: str     # Organised patient data text for the LLM
    raw_summary: str            # LLM-generated markdown summary
    verification_result: Optional[VerificationResult]

    # ── Retry control ────────────────────────────────────────────────────────
    retry_count: int            # Number of regeneration attempts so far

    # ── Conversation context (optional — populated by SummaryService) ────────
    session_id: Optional[str]           # Active conversation session UUID, or None
    conversation_history: list[dict]    # Prior turns as LLM messages [{role, content}]

    # ── Output ───────────────────────────────────────────────────────────────
    final_summary: Optional[SummaryResponse]
    errors: list[str]           # Accumulated errors from any node
    metadata: dict              # Timing, token usage, model used, etc.
