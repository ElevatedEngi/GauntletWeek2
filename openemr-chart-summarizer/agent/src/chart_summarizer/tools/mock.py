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
Mock FHIR tools for development and testing.

Provides a complete set of drop-in replacements for the real FHIR tools
that return deterministic fixture data — no OpenEMR instance required.

Three synthetic patients are included:

  TEST-001  Jane Doe          35F  Simple: HTN, one medication, one allergy
  TEST-002  Robert Smith      82M  Complex: CAD, T2DM, HTN, CKD, HFpEF, polypharmacy
  TEST-003  Emma Wilson        7F  Pediatric: asthma, healthy otherwise

To swap in the mock tools, pass ``use_mock=True`` to the retrieve node,
or replace the tool list in the pipeline directly during development.
"""

from abc import abstractmethod
from typing import Any, Optional

from chart_summarizer.tools.base import FHIRTool, ToolResult

# ---------------------------------------------------------------------------
# Synthetic patient fixture data
# Keys match Pydantic model fields (models/patient.py) so the structure_node
# can validate them directly with model_validate().
# ---------------------------------------------------------------------------

_PATIENT_FIXTURES: dict[str, dict[str, Any]] = {
    # ------------------------------------------------------------------
    # TEST-001 — Jane Doe, 35F, simple healthy adult
    # ------------------------------------------------------------------
    "TEST-001": {
        "demographics": {
            "patient_id": "TEST-001",
            "fhir_id": "fhir-001",
            "first_name": "Jane",
            "last_name": "Doe",
            "date_of_birth": "1990-05-15",
            "sex": "Female",
            "race": "White",
            "ethnicity": "Non-Hispanic",
            "primary_language": "English",
            "insurance_name": "Blue Cross Blue Shield",
            "primary_care_provider": "Dr. Sarah Chen",
        },
        "conditions": [
            {
                "condition_id": "COND-001-01",
                "icd10_code": "I10",
                "display_name": "Essential (primary) hypertension",
                "clinical_status": "active",
                "onset_date": "2022-03-10",
                "resolved_date": None,
                "recorded_by": "Dr. Sarah Chen",
                "notes": "Controlled on current medication.",
            },
            {
                "condition_id": "COND-001-02",
                "icd10_code": "K21.0",
                "display_name": "Gastro-esophageal reflux disease with oesophagitis",
                "clinical_status": "resolved",
                "onset_date": "2021-06-01",
                "resolved_date": "2022-01-15",
                "recorded_by": "Dr. Sarah Chen",
                "notes": None,
            },
        ],
        "medications": [
            {
                "medication_id": "MED-001-01",
                "name": "Lisinopril",
                "rxnorm_code": "29046",
                "dosage": "10 mg",
                "frequency": "once daily",
                "route": "oral",
                "status": "active",
                "start_date": "2022-03-15",
                "end_date": None,
                "prescriber": "Dr. Sarah Chen",
                "indication": "Hypertension",
            },
        ],
        "allergies": [
            {
                "allergy_id": "ALG-001-01",
                "substance": "Penicillin",
                "substance_code": "7980",
                "reaction": "Anaphylaxis",
                "severity": "severe",
                "clinical_status": "active",
                "verification_status": "confirmed",
                "recorded_date": "2005-08-20",
            },
        ],
        "labs": [
            {
                "lab_id": "LAB-001-01",
                "loinc_code": "2160-0",
                "test_name": "Creatinine [Mass/volume] in Serum or Plasma",
                "value": "0.85",
                "unit": "mg/dL",
                "reference_range": "0.57–1.00",
                "interpretation": "N",
                "status": "final",
                "effective_date": "2025-11-10T09:30:00",
                "ordering_provider": "Dr. Sarah Chen",
            },
            {
                "lab_id": "LAB-001-02",
                "loinc_code": "718-7",
                "test_name": "Hemoglobin [Mass/volume] in Blood",
                "value": "13.8",
                "unit": "g/dL",
                "reference_range": "12.0–16.0",
                "interpretation": "N",
                "status": "final",
                "effective_date": "2025-11-10T09:30:00",
                "ordering_provider": "Dr. Sarah Chen",
            },
        ],
        "vitals": [
            {
                "vital_id": "VIT-001-01",
                "type": "blood-pressure",
                "value": "132/84",
                "unit": "mmHg",
                "effective_date": "2025-11-15T10:00:00",
                "recorder": "Nursing Staff",
            },
            {
                "vital_id": "VIT-001-02",
                "type": "heart-rate",
                "value": "72",
                "unit": "bpm",
                "effective_date": "2025-11-15T10:00:00",
                "recorder": "Nursing Staff",
            },
            {
                "vital_id": "VIT-001-03",
                "type": "body-weight",
                "value": "145",
                "unit": "lbs",
                "effective_date": "2025-11-15T10:00:00",
                "recorder": "Nursing Staff",
            },
        ],
        "encounters": [
            {
                "encounter_id": "ENC-001-01",
                "encounter_type": "Annual wellness visit",
                "date": "2025-11-15T10:00:00",
                "provider": "Dr. Sarah Chen",
                "specialty": "Internal Medicine",
                "chief_complaint": "Annual physical exam",
                "soap_note": (
                    "S: Patient presents for annual wellness visit. No acute complaints. "
                    "Reports good medication adherence. BP readings at home averaging 130-135/82-86. "
                    "O: BP 132/84, HR 72, Wt 145 lbs. Lungs CTA. Heart RRR, no murmurs. "
                    "A: Hypertension — well controlled on Lisinopril 10 mg daily. "
                    "P: Continue current medications. Repeat labs in 12 months. RTC 1 year."
                ),
                "diagnoses": ["I10"],
                "discharge_disposition": None,
            },
        ],
        "immunizations": [
            {
                "immunization_id": "IMM-001-01",
                "vaccine_name": "Influenza, seasonal, injectable",
                "cvx_code": "141",
                "dose_number": "1",
                "occurrence_date": "2025-10-05",
                "status": "completed",
                "administered_by": "Nursing Staff",
                "lot_number": "FLU25-001",
            },
            {
                "immunization_id": "IMM-001-02",
                "vaccine_name": "COVID-19, mRNA",
                "cvx_code": "208",
                "dose_number": "4",
                "occurrence_date": "2025-09-15",
                "status": "completed",
                "administered_by": "Nursing Staff",
                "lot_number": "COV25-XYZ",
            },
        ],
        "procedures": [],
    },

    # ------------------------------------------------------------------
    # TEST-002 — Robert Smith, 82M, complex multi-morbidity / polypharmacy
    # ------------------------------------------------------------------
    "TEST-002": {
        "demographics": {
            "patient_id": "TEST-002",
            "fhir_id": "fhir-002",
            "first_name": "Robert",
            "last_name": "Smith",
            "date_of_birth": "1942-03-20",
            "sex": "Male",
            "race": "Black or African American",
            "ethnicity": "Non-Hispanic",
            "primary_language": "English",
            "insurance_name": "Medicare Part B",
            "primary_care_provider": "Dr. Marcus Johnson",
        },
        "conditions": [
            {
                "condition_id": "COND-002-01",
                "icd10_code": "I25.10",
                "display_name": "Atherosclerotic heart disease of native coronary artery without angina pectoris",
                "clinical_status": "active",
                "onset_date": "2018-04-12",
                "resolved_date": None,
                "recorded_by": "Dr. Lisa Park (Cardiology)",
                "notes": "Stent placed LAD 2023. On DAPT.",
            },
            {
                "condition_id": "COND-002-02",
                "icd10_code": "E11.9",
                "display_name": "Type 2 diabetes mellitus without complications",
                "clinical_status": "active",
                "onset_date": "2010-07-01",
                "resolved_date": None,
                "recorded_by": "Dr. Marcus Johnson",
                "notes": "A1C 7.8% as of Nov 2025. Target <8.0% given age.",
            },
            {
                "condition_id": "COND-002-03",
                "icd10_code": "I10",
                "display_name": "Essential (primary) hypertension",
                "clinical_status": "active",
                "onset_date": "2005-01-15",
                "resolved_date": None,
                "recorded_by": "Dr. Marcus Johnson",
                "notes": "BP suboptimally controlled. Consider uptitrating Carvedilol.",
            },
            {
                "condition_id": "COND-002-04",
                "icd10_code": "N18.3",
                "display_name": "Chronic kidney disease, stage 3a",
                "clinical_status": "active",
                "onset_date": "2020-09-30",
                "resolved_date": None,
                "recorded_by": "Dr. Marcus Johnson",
                "notes": "eGFR 42 (Nov 2025). Dose-adjust renally cleared meds.",
            },
            {
                "condition_id": "COND-002-05",
                "icd10_code": "I50.32",
                "display_name": "Chronic diastolic (congestive) heart failure",
                "clinical_status": "active",
                "onset_date": "2022-11-08",
                "resolved_date": None,
                "recorded_by": "Dr. Lisa Park (Cardiology)",
                "notes": "EF 55% (HFpEF). On Furosemide for volume management.",
            },
            {
                "condition_id": "COND-002-06",
                "icd10_code": "E03.9",
                "display_name": "Hypothyroidism, unspecified",
                "clinical_status": "active",
                "onset_date": "2015-03-22",
                "resolved_date": None,
                "recorded_by": "Dr. Marcus Johnson",
                "notes": "TSH 2.1 (Nov 2025) — well controlled.",
            },
        ],
        "medications": [
            {
                "medication_id": "MED-002-01",
                "name": "Metformin",
                "rxnorm_code": "6809",
                "dosage": "500 mg",
                "frequency": "twice daily with meals",
                "route": "oral",
                "status": "active",
                "start_date": "2010-07-05",
                "end_date": None,
                "prescriber": "Dr. Marcus Johnson",
                "indication": "Type 2 diabetes",
            },
            {
                "medication_id": "MED-002-02",
                "name": "Lisinopril",
                "rxnorm_code": "29046",
                "dosage": "20 mg",
                "frequency": "once daily",
                "route": "oral",
                "status": "active",
                "start_date": "2005-02-01",
                "end_date": None,
                "prescriber": "Dr. Marcus Johnson",
                "indication": "Hypertension, CKD protection",
            },
            {
                "medication_id": "MED-002-03",
                "name": "Carvedilol",
                "rxnorm_code": "20352",
                "dosage": "12.5 mg",
                "frequency": "twice daily",
                "route": "oral",
                "status": "active",
                "start_date": "2022-11-20",
                "end_date": None,
                "prescriber": "Dr. Lisa Park (Cardiology)",
                "indication": "HFpEF, CAD",
            },
            {
                "medication_id": "MED-002-04",
                "name": "Furosemide",
                "rxnorm_code": "4603",
                "dosage": "40 mg",
                "frequency": "once daily in the morning",
                "route": "oral",
                "status": "active",
                "start_date": "2022-11-20",
                "end_date": None,
                "prescriber": "Dr. Lisa Park (Cardiology)",
                "indication": "Heart failure — volume management",
            },
            {
                "medication_id": "MED-002-05",
                "name": "Levothyroxine",
                "rxnorm_code": "10582",
                "dosage": "75 mcg",
                "frequency": "once daily on empty stomach",
                "route": "oral",
                "status": "active",
                "start_date": "2015-04-01",
                "end_date": None,
                "prescriber": "Dr. Marcus Johnson",
                "indication": "Hypothyroidism",
            },
            {
                "medication_id": "MED-002-06",
                "name": "Atorvastatin",
                "rxnorm_code": "83367",
                "dosage": "40 mg",
                "frequency": "once daily at bedtime",
                "route": "oral",
                "status": "active",
                "start_date": "2018-04-20",
                "end_date": None,
                "prescriber": "Dr. Lisa Park (Cardiology)",
                "indication": "CAD — high-intensity statin therapy",
            },
            {
                "medication_id": "MED-002-07",
                "name": "Aspirin",
                "rxnorm_code": "1191",
                "dosage": "81 mg",
                "frequency": "once daily",
                "route": "oral",
                "status": "active",
                "start_date": "2018-04-12",
                "end_date": None,
                "prescriber": "Dr. Lisa Park (Cardiology)",
                "indication": "CAD — antiplatelet therapy",
            },
            {
                "medication_id": "MED-002-08",
                "name": "Clopidogrel",
                "rxnorm_code": "174742",
                "dosage": "75 mg",
                "frequency": "once daily",
                "route": "oral",
                "status": "active",
                "start_date": "2023-06-15",
                "end_date": None,
                "prescriber": "Dr. Lisa Park (Cardiology)",
                "indication": "Post-stent DAPT (dual antiplatelet therapy)",
            },
        ],
        "allergies": [
            {
                "allergy_id": "ALG-002-01",
                "substance": "Sulfonamides",
                "substance_code": "387406002",
                "reaction": "Skin rash, urticaria",
                "severity": "moderate",
                "clinical_status": "active",
                "verification_status": "confirmed",
                "recorded_date": "1998-05-10",
            },
            {
                "allergy_id": "ALG-002-02",
                "substance": "Codeine",
                "substance_code": "2670",
                "reaction": "Nausea and vomiting",
                "severity": "mild",
                "clinical_status": "active",
                "verification_status": "confirmed",
                "recorded_date": "2012-11-03",
            },
        ],
        "labs": [
            {
                "lab_id": "LAB-002-01",
                "loinc_code": "4548-4",
                "test_name": "Hemoglobin A1c/Hemoglobin.total in Blood",
                "value": "7.8",
                "unit": "%",
                "reference_range": "<7.0",
                "interpretation": "H",
                "status": "final",
                "effective_date": "2025-11-05T08:00:00",
                "ordering_provider": "Dr. Marcus Johnson",
            },
            {
                "lab_id": "LAB-002-02",
                "loinc_code": "2160-0",
                "test_name": "Creatinine [Mass/volume] in Serum or Plasma",
                "value": "1.9",
                "unit": "mg/dL",
                "reference_range": "0.70–1.20",
                "interpretation": "H",
                "status": "final",
                "effective_date": "2025-11-05T08:00:00",
                "ordering_provider": "Dr. Marcus Johnson",
            },
            {
                "lab_id": "LAB-002-03",
                "loinc_code": "98979-8",
                "test_name": "Glomerular filtration rate (eGFR)",
                "value": "42",
                "unit": "mL/min/1.73m2",
                "reference_range": ">60",
                "interpretation": "L",
                "status": "final",
                "effective_date": "2025-11-05T08:00:00",
                "ordering_provider": "Dr. Marcus Johnson",
            },
            {
                "lab_id": "LAB-002-04",
                "loinc_code": "42637-9",
                "test_name": "BNP [Mass/volume] in Serum or Plasma",
                "value": "420",
                "unit": "pg/mL",
                "reference_range": "<100",
                "interpretation": "H",
                "status": "final",
                "effective_date": "2025-11-05T08:00:00",
                "ordering_provider": "Dr. Lisa Park (Cardiology)",
            },
            {
                "lab_id": "LAB-002-05",
                "loinc_code": "3016-3",
                "test_name": "Thyrotropin [Units/volume] in Serum or Plasma",
                "value": "2.1",
                "unit": "mIU/L",
                "reference_range": "0.45–4.50",
                "interpretation": "N",
                "status": "final",
                "effective_date": "2025-11-05T08:00:00",
                "ordering_provider": "Dr. Marcus Johnson",
            },
        ],
        "vitals": [
            {
                "vital_id": "VIT-002-01",
                "type": "blood-pressure",
                "value": "148/90",
                "unit": "mmHg",
                "effective_date": "2025-11-10T09:00:00",
                "recorder": "Nursing Staff",
            },
            {
                "vital_id": "VIT-002-02",
                "type": "heart-rate",
                "value": "62",
                "unit": "bpm",
                "effective_date": "2025-11-10T09:00:00",
                "recorder": "Nursing Staff",
            },
            {
                "vital_id": "VIT-002-03",
                "type": "body-weight",
                "value": "198",
                "unit": "lbs",
                "effective_date": "2025-11-10T09:00:00",
                "recorder": "Nursing Staff",
            },
            {
                "vital_id": "VIT-002-04",
                "type": "oxygen-saturation",
                "value": "96",
                "unit": "%",
                "effective_date": "2025-11-10T09:00:00",
                "recorder": "Nursing Staff",
            },
        ],
        "encounters": [
            {
                "encounter_id": "ENC-002-01",
                "encounter_type": "Office visit",
                "date": "2025-11-10T09:00:00",
                "provider": "Dr. Marcus Johnson",
                "specialty": "Internal Medicine",
                "chief_complaint": "Quarterly diabetes and cardiac follow-up",
                "soap_note": (
                    "S: 82M with CAD, T2DM, HTN, CKD3, HFpEF presents for routine follow-up. "
                    "Reports mild ankle swelling over past 2 weeks, improved with Furosemide uptitration. "
                    "No chest pain, no syncope. Denies polyuria. "
                    "O: BP 148/90, HR 62, O2 96% RA, Wt 198 lbs (down 4 lbs since last visit). "
                    "JVP mildly elevated. 1+ pitting edema bilateral ankles. Lungs: fine bibasilar crackles. "
                    "A: (1) HFpEF — partially decompensated, improving with diuresis. "
                    "(2) T2DM — A1C 7.8%, at goal for age. Continue Metformin. "
                    "(3) HTN — suboptimally controlled. Consider uptitrating Carvedilol. "
                    "(4) CKD3 — eGFR 42, stable. Monitor Metformin use per CKD guidelines. "
                    "P: Continue current medications. Uptitrate Carvedilol to 25 mg BID at next visit. "
                    "Repeat BMP, BNP in 4 weeks. Cardiology follow-up scheduled."
                ),
                "diagnoses": ["I50.32", "E11.9", "I10", "N18.3", "I25.10"],
                "discharge_disposition": None,
            },
            {
                "encounter_id": "ENC-002-02",
                "encounter_type": "Cardiology office visit",
                "date": "2025-09-22T14:00:00",
                "provider": "Dr. Lisa Park",
                "specialty": "Cardiology",
                "chief_complaint": "Cardiac follow-up post-stent",
                "soap_note": (
                    "S: Patient doing well post-LAD stenting (June 2023). No anginal symptoms. "
                    "Compliant with DAPT (Aspirin 81mg + Clopidogrel 75mg). "
                    "O: BP 150/92. HR 64. Echo EF 55%. No wall motion abnormalities. "
                    "A: CAD post-PCI — stable. Continue DAPT through June 2025 (12 months post-stent). "
                    "P: Stress test in 6 months. Continue Atorvastatin 40mg. LDL-C target <70 mg/dL."
                ),
                "diagnoses": ["I25.10", "I50.32"],
                "discharge_disposition": None,
            },
        ],
        "immunizations": [
            {
                "immunization_id": "IMM-002-01",
                "vaccine_name": "Influenza, seasonal, injectable",
                "cvx_code": "141",
                "dose_number": "1",
                "occurrence_date": "2025-10-01",
                "status": "completed",
                "administered_by": "Nursing Staff",
                "lot_number": "FLU25-002",
            },
            {
                "immunization_id": "IMM-002-02",
                "vaccine_name": "Pneumococcal polysaccharide vaccine, 23 valent",
                "cvx_code": "33",
                "dose_number": "1",
                "occurrence_date": "2022-04-15",
                "status": "completed",
                "administered_by": "Nursing Staff",
                "lot_number": "PNEU22-001",
            },
            {
                "immunization_id": "IMM-002-03",
                "vaccine_name": "Zoster vaccine, live",
                "cvx_code": "121",
                "dose_number": "2",
                "occurrence_date": "2023-02-20",
                "status": "completed",
                "administered_by": "Nursing Staff",
                "lot_number": "ZOS23-002",
            },
        ],
        "procedures": [
            {
                "procedure_id": "PROC-002-01",
                "cpt_code": "92928",
                "snomed_code": "415070008",
                "name": "Percutaneous coronary intervention (PCI) with stent placement — LAD",
                "performed_date": "2023-06-15",
                "status": "completed",
                "performer": "Dr. James Rivera (Interventional Cardiology)",
                "body_site": "Left anterior descending artery",
                "notes": "Drug-eluting stent placed. TIMI 3 flow achieved.",
            },
            {
                "procedure_id": "PROC-002-02",
                "cpt_code": "33208",
                "snomed_code": "17219003",
                "name": "Insertion of permanent pacemaker with transvenous electrodes",
                "performed_date": "2024-01-08",
                "status": "completed",
                "performer": "Dr. Lisa Park (Cardiology)",
                "body_site": "Right ventricle",
                "notes": "Dual-chamber pacemaker for sick sinus syndrome. Pacing threshold adequate.",
            },
        ],
    },

    # ------------------------------------------------------------------
    # TEST-003 — Emma Wilson, 7F, pediatric patient with asthma
    # ------------------------------------------------------------------
    "TEST-003": {
        "demographics": {
            "patient_id": "TEST-003",
            "fhir_id": "fhir-003",
            "first_name": "Emma",
            "last_name": "Wilson",
            "date_of_birth": "2018-08-10",
            "sex": "Female",
            "race": "White",
            "ethnicity": "Non-Hispanic",
            "primary_language": "English",
            "insurance_name": "Medicaid",
            "primary_care_provider": "Dr. Amy Torres (Pediatrics)",
        },
        "conditions": [
            {
                "condition_id": "COND-003-01",
                "icd10_code": "J45.20",
                "display_name": "Mild intermittent asthma, uncomplicated",
                "clinical_status": "active",
                "onset_date": "2021-09-15",
                "resolved_date": None,
                "recorded_by": "Dr. Amy Torres",
                "notes": "Well-controlled. SABA use <2 days/week. No nocturnal symptoms.",
            },
        ],
        "medications": [
            {
                "medication_id": "MED-003-01",
                "name": "Albuterol sulfate",
                "rxnorm_code": "435",
                "dosage": "90 mcg/actuation",
                "frequency": "2 puffs every 4-6 hours as needed for wheeze/shortness of breath",
                "route": "inhalation",
                "status": "active",
                "start_date": "2021-09-20",
                "end_date": None,
                "prescriber": "Dr. Amy Torres",
                "indication": "Asthma — rescue inhaler",
            },
        ],
        "allergies": [
            {
                "allergy_id": "ALG-003-01",
                "substance": "Amoxicillin",
                "substance_code": "723",
                "reaction": "Rash",
                "severity": "mild",
                "clinical_status": "active",
                "verification_status": "confirmed",
                "recorded_date": "2022-03-01",
            },
        ],
        "labs": [
            {
                "lab_id": "LAB-003-01",
                "loinc_code": "718-7",
                "test_name": "Hemoglobin [Mass/volume] in Blood",
                "value": "12.5",
                "unit": "g/dL",
                "reference_range": "11.5–15.5",
                "interpretation": "N",
                "status": "final",
                "effective_date": "2025-08-10T09:00:00",
                "ordering_provider": "Dr. Amy Torres",
            },
        ],
        "vitals": [
            {
                "vital_id": "VIT-003-01",
                "type": "body-weight",
                "value": "22",
                "unit": "kg",
                "effective_date": "2025-08-12T10:00:00",
                "recorder": "Nursing Staff",
            },
            {
                "vital_id": "VIT-003-02",
                "type": "body-height",
                "value": "119",
                "unit": "cm",
                "effective_date": "2025-08-12T10:00:00",
                "recorder": "Nursing Staff",
            },
            {
                "vital_id": "VIT-003-03",
                "type": "blood-pressure",
                "value": "96/60",
                "unit": "mmHg",
                "effective_date": "2025-08-12T10:00:00",
                "recorder": "Nursing Staff",
            },
        ],
        "encounters": [
            {
                "encounter_id": "ENC-003-01",
                "encounter_type": "Well-child visit (7-year-old)",
                "date": "2025-08-12T10:00:00",
                "provider": "Dr. Amy Torres",
                "specialty": "Pediatrics",
                "chief_complaint": "Annual well-child visit",
                "soap_note": (
                    "S: 7-year-old female presenting for annual well-child visit. "
                    "Parents report asthma well-controlled. Albuterol used once in past 3 months. "
                    "No ER visits or hospitalizations for asthma this year. Doing well in school. "
                    "O: Wt 22 kg (50th%), Ht 119 cm (55th%), BP 96/60, O2 98% RA. "
                    "Lungs: clear bilaterally. No wheezing. Good air movement. "
                    "A: (1) Mild intermittent asthma — well controlled on PRN albuterol. "
                    "(2) Healthy growth and development. "
                    "P: Continue PRN albuterol. Asthma action plan reviewed with parents. "
                    "Recommend annual flu vaccine. Next visit in 1 year."
                ),
                "diagnoses": ["J45.20", "Z00.121"],
                "discharge_disposition": None,
            },
        ],
        "immunizations": [
            {
                "immunization_id": "IMM-003-01",
                "vaccine_name": "Influenza, seasonal, injectable",
                "cvx_code": "141",
                "dose_number": "1",
                "occurrence_date": "2025-10-10",
                "status": "completed",
                "administered_by": "Nursing Staff",
                "lot_number": "FLU25-003",
            },
            {
                "immunization_id": "IMM-003-02",
                "vaccine_name": "DTaP",
                "cvx_code": "20",
                "dose_number": "5",
                "occurrence_date": "2023-08-12",
                "status": "completed",
                "administered_by": "Nursing Staff",
                "lot_number": "DTAP23-001",
            },
            {
                "immunization_id": "IMM-003-03",
                "vaccine_name": "MMR",
                "cvx_code": "03",
                "dose_number": "2",
                "occurrence_date": "2023-08-12",
                "status": "completed",
                "administered_by": "Nursing Staff",
                "lot_number": "MMR23-001",
            },
        ],
        "procedures": [],
    },
}

# ---------------------------------------------------------------------------
# MockFHIRTool — abstract base for all mock implementations
# ---------------------------------------------------------------------------


class MockFHIRTool(FHIRTool):
    """
    Abstract base for mock FHIR tools.

    Subclasses only need to declare ``tool_name``, ``description``, and
    ``_section_key``. The ``execute()`` method looks up fixture data from
    ``_PATIENT_FIXTURES`` using the provided ``patient_id``.

    Override ``_patient_fixtures`` in tests to inject custom data.
    """

    def __init__(self, patient_fixtures: Optional[dict[str, Any]] = None) -> None:
        """
        Initialise the mock tool.

        Args:
            patient_fixtures: Optional override for the module-level fixture dict.
                              Useful for injecting per-test data without modifying globals.
        """
        # Do NOT call super().__init__() — that would try to set up the real HTTP client
        self._fhir_base_url = "mock://fhir"
        self._fixtures = patient_fixtures if patient_fixtures is not None else _PATIENT_FIXTURES

    @property
    @abstractmethod
    def _section_key(self) -> str:
        """The key within a patient's fixture dict that this tool returns."""
        raise NotImplementedError

    async def execute(self, patient_id: str, **kwargs: Any) -> ToolResult:
        """
        Return fixture data for the given patient_id and section_key.

        Returns a failure ToolResult if the patient_id is not in the fixture dict.
        Returns an empty list (not a failure) if the patient has no data for this section.
        """
        patient = self._fixtures.get(patient_id)
        if patient is None:
            return ToolResult(
                tool_name=self.tool_name,
                success=False,
                error_message=(
                    f"No mock fixture data found for patient_id='{patient_id}'. "
                    f"Available IDs: {list(self._fixtures.keys())}"
                ),
            )

        data = patient.get(self._section_key)

        # Demographics is a single dict, not a list
        if isinstance(data, dict):
            return ToolResult(
                tool_name=self.tool_name,
                success=True,
                data=data,
                records_returned=1,
            )

        records = data or []
        return ToolResult(
            tool_name=self.tool_name,
            success=True,
            data=records,
            records_returned=len(records),
        )

    # The real OAuth / HTTP methods are not needed for mock tools
    async def _get_oauth_token(self) -> str:
        return "mock-bearer-token"

    async def _fhir_get(self, path: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        raise RuntimeError(
            "MockFHIRTool._fhir_get() should never be called. "
            "execute() returns fixture data directly."
        )


# ---------------------------------------------------------------------------
# 9 concrete mock tool classes
# ---------------------------------------------------------------------------


class MockGetPatientDemographicsTool(MockFHIRTool):
    """Mock tool: returns patient demographics (name, DOB, sex, insurance)."""

    @property
    def tool_name(self) -> str:
        return "get_patient_demographics"

    @property
    def description(self) -> str:
        return "Mock: patient demographics from fixture data."

    @property
    def _section_key(self) -> str:
        return "demographics"


class MockGetProblemListTool(MockFHIRTool):
    """Mock tool: returns active and resolved conditions (ICD-10)."""

    @property
    def tool_name(self) -> str:
        return "get_problem_list"

    @property
    def description(self) -> str:
        return "Mock: problem list (conditions) from fixture data."

    @property
    def _section_key(self) -> str:
        return "conditions"


class MockGetMedicationsTool(MockFHIRTool):
    """Mock tool: returns current medications, dosages, and prescribers."""

    @property
    def tool_name(self) -> str:
        return "get_medications"

    @property
    def description(self) -> str:
        return "Mock: medication list from fixture data."

    @property
    def _section_key(self) -> str:
        return "medications"


class MockGetAllergiesTool(MockFHIRTool):
    """Mock tool: returns allergy and adverse reaction records."""

    @property
    def tool_name(self) -> str:
        return "get_allergies"

    @property
    def description(self) -> str:
        return "Mock: allergy records from fixture data."

    @property
    def _section_key(self) -> str:
        return "allergies"


class MockGetLabResultsTool(MockFHIRTool):
    """Mock tool: returns recent lab results with reference ranges."""

    @property
    def tool_name(self) -> str:
        return "get_lab_results"

    @property
    def description(self) -> str:
        return "Mock: laboratory results from fixture data."

    @property
    def _section_key(self) -> str:
        return "labs"


class MockGetVitalsHistoryTool(MockFHIRTool):
    """Mock tool: returns BP, weight, BMI, and other vital signs."""

    @property
    def tool_name(self) -> str:
        return "get_vitals_history"

    @property
    def description(self) -> str:
        return "Mock: vital signs history from fixture data."

    @property
    def _section_key(self) -> str:
        return "vitals"


class MockGetEncounterNotesTool(MockFHIRTool):
    """Mock tool: returns SOAP notes from recent encounters."""

    @property
    def tool_name(self) -> str:
        return "get_encounter_notes"

    @property
    def description(self) -> str:
        return "Mock: encounter notes from fixture data."

    @property
    def _section_key(self) -> str:
        return "encounters"


class MockGetImmunizationsTool(MockFHIRTool):
    """Mock tool: returns vaccination history."""

    @property
    def tool_name(self) -> str:
        return "get_immunizations"

    @property
    def description(self) -> str:
        return "Mock: immunization records from fixture data."

    @property
    def _section_key(self) -> str:
        return "immunizations"


class MockGetProceduresTool(MockFHIRTool):
    """Mock tool: returns surgical and procedural history."""

    @property
    def tool_name(self) -> str:
        return "get_procedures"

    @property
    def description(self) -> str:
        return "Mock: procedure history from fixture data."

    @property
    def _section_key(self) -> str:
        return "procedures"


# ---------------------------------------------------------------------------
# Convenience factory
# ---------------------------------------------------------------------------

#: The ordered list of all 9 mock tools, matching the real tool set.
ALL_MOCK_TOOLS: list[type[MockFHIRTool]] = [
    MockGetPatientDemographicsTool,
    MockGetProblemListTool,
    MockGetMedicationsTool,
    MockGetAllergiesTool,
    MockGetLabResultsTool,
    MockGetVitalsHistoryTool,
    MockGetEncounterNotesTool,
    MockGetImmunizationsTool,
    MockGetProceduresTool,
]


def create_mock_tools(
    patient_fixtures: Optional[dict[str, Any]] = None,
) -> list[MockFHIRTool]:
    """
    Instantiate and return all 9 mock FHIR tools.

    Args:
        patient_fixtures: Optional fixture override dict. Pass a custom dict
                          in tests to inject specific patient scenarios.

    Returns:
        List of 9 MockFHIRTool instances, one per FHIR resource type.
    """
    return [cls(patient_fixtures=patient_fixtures) for cls in ALL_MOCK_TOOLS]


#: Convenience set of available mock patient IDs for test assertions.
MOCK_PATIENT_IDS: list[str] = list(_PATIENT_FIXTURES.keys())
