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
FHIR-aligned patient data models.

These models are simplified representations of FHIR R4 resources, designed
to be easy to work with in Python while preserving the key clinical data
needed for chart summarization.
"""

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field


class PatientDemographics(BaseModel):
    """Core patient identification and demographic information."""

    patient_id: str = Field(description="OpenEMR internal patient ID (PID).")
    fhir_id: Optional[str] = Field(default=None, description="FHIR Patient resource ID.")
    first_name: str = Field(description="Patient first name.")
    last_name: str = Field(description="Patient last name.")
    date_of_birth: date = Field(description="Patient date of birth.")
    sex: str = Field(description="Biological sex as recorded in the EHR.")
    race: Optional[str] = Field(default=None)
    ethnicity: Optional[str] = Field(default=None)
    primary_language: Optional[str] = Field(default=None)
    insurance_name: Optional[str] = Field(default=None)
    primary_care_provider: Optional[str] = Field(default=None)


class Condition(BaseModel):
    """A diagnosis or problem list entry (FHIR Condition resource)."""

    condition_id: str = Field(description="Unique identifier for this condition record.")
    icd10_code: Optional[str] = Field(default=None, description="ICD-10 diagnosis code.")
    display_name: str = Field(description="Human-readable condition name.")
    clinical_status: str = Field(
        description="active | recurrence | relapse | inactive | remission | resolved"
    )
    onset_date: Optional[date] = Field(default=None)
    resolved_date: Optional[date] = Field(default=None)
    recorded_by: Optional[str] = Field(default=None, description="Provider who recorded this.")
    notes: Optional[str] = Field(default=None)


class Medication(BaseModel):
    """A medication order or active prescription (FHIR MedicationRequest resource)."""

    medication_id: str
    name: str = Field(description="Medication generic or brand name.")
    rxnorm_code: Optional[str] = Field(default=None, description="RxNorm code.")
    dosage: Optional[str] = Field(default=None, description="Dose amount and unit (e.g. '10 mg').")
    frequency: Optional[str] = Field(default=None, description="Frequency (e.g. 'once daily').")
    route: Optional[str] = Field(default=None, description="Route of administration.")
    status: str = Field(description="active | on-hold | cancelled | completed | stopped")
    start_date: Optional[date] = Field(default=None)
    end_date: Optional[date] = Field(default=None)
    prescriber: Optional[str] = Field(default=None)
    indication: Optional[str] = Field(default=None)


class Allergy(BaseModel):
    """An allergy or adverse reaction record (FHIR AllergyIntolerance resource)."""

    allergy_id: str
    substance: str = Field(description="Allergen substance name.")
    substance_code: Optional[str] = Field(default=None, description="SNOMED or RxNorm code.")
    reaction: Optional[str] = Field(default=None, description="Clinical manifestation of reaction.")
    severity: Optional[str] = Field(
        default=None, description="mild | moderate | severe"
    )
    clinical_status: str = Field(description="active | inactive | resolved")
    verification_status: Optional[str] = Field(
        default=None, description="confirmed | unconfirmed | presumed | refuted"
    )
    recorded_date: Optional[date] = Field(default=None)


class LabResult(BaseModel):
    """A laboratory observation result (FHIR Observation resource, category=laboratory)."""

    lab_id: str
    loinc_code: Optional[str] = Field(default=None, description="LOINC code for the test.")
    test_name: str
    value: Optional[str] = Field(default=None, description="Result value as a string.")
    unit: Optional[str] = Field(default=None)
    reference_range: Optional[str] = Field(default=None, description="Normal range (e.g. '3.5-5.0').")
    interpretation: Optional[str] = Field(
        default=None, description="H (high) | L (low) | N (normal) | A (abnormal)"
    )
    status: str = Field(description="registered | preliminary | final | amended | corrected")
    effective_date: Optional[datetime] = Field(default=None)
    ordering_provider: Optional[str] = Field(default=None)


class VitalSign(BaseModel):
    """A vital signs observation (FHIR Observation resource, category=vital-signs)."""

    vital_id: str
    type: str = Field(
        description="blood-pressure | heart-rate | body-weight | body-height | bmi | temperature | oxygen-saturation"
    )
    value: str = Field(description="Measured value (e.g. '120/80' for BP, '72' for HR).")
    unit: Optional[str] = Field(default=None)
    effective_date: datetime
    recorder: Optional[str] = Field(default=None)


class Encounter(BaseModel):
    """A clinical encounter (FHIR Encounter resource)."""

    encounter_id: str
    encounter_type: Optional[str] = Field(default=None, description="Office visit, telehealth, etc.")
    date: datetime
    provider: Optional[str] = Field(default=None)
    specialty: Optional[str] = Field(default=None)
    chief_complaint: Optional[str] = Field(default=None)
    soap_note: Optional[str] = Field(
        default=None,
        description="Full SOAP note text. May be long; pre-summarize before sending to LLM.",
    )
    diagnoses: list[str] = Field(
        default_factory=list,
        description="ICD-10 codes or descriptions documented at this encounter.",
    )
    discharge_disposition: Optional[str] = Field(default=None)


class Immunization(BaseModel):
    """A vaccination record (FHIR Immunization resource)."""

    immunization_id: str
    vaccine_name: str
    cvx_code: Optional[str] = Field(default=None, description="CVX vaccine code.")
    dose_number: Optional[str] = Field(default=None)
    occurrence_date: Optional[date] = Field(default=None)
    status: str = Field(description="completed | entered-in-error | not-done")
    administered_by: Optional[str] = Field(default=None)
    lot_number: Optional[str] = Field(default=None)


class Procedure(BaseModel):
    """A surgical or procedural history entry (FHIR Procedure resource)."""

    procedure_id: str
    cpt_code: Optional[str] = Field(default=None, description="CPT procedure code.")
    snomed_code: Optional[str] = Field(default=None, description="SNOMED-CT code.")
    name: str = Field(description="Human-readable procedure name.")
    performed_date: Optional[date] = Field(default=None)
    status: str = Field(
        description="preparation | in-progress | not-done | on-hold | stopped | completed | entered-in-error | unknown"
    )
    performer: Optional[str] = Field(default=None)
    body_site: Optional[str] = Field(default=None)
    notes: Optional[str] = Field(default=None)
