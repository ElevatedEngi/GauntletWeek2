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
LangGraph node implementations for the Chart Summarizer pipeline.

Each public symbol is either:
  - A standalone async node function (structure_data_node, verify_summary_node,
    format_output_node) that takes SummarizerState and returns a partial state dict.
  - A factory function (make_retrieve_data_node, make_generate_summary_node) that
    injects external dependencies (tools, LLM provider) via closure and returns an
    async node function with the same signature.

All nodes are importable independently for unit testing.
"""

from __future__ import annotations

import asyncio
import html as html_stdlib
import logging
import re
import time
import uuid
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Optional

from chart_summarizer.graph.state import SummarizerState
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
from chart_summarizer.models.summary import (
    Citation,
    SummaryMetadata,
    SummaryResponse,
    VerificationResult,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PROMPTS_DIR = Path(__file__).parent / "prompts"

# Tools that are NOT filtered by date (always return full history)
_NO_DATE_FILTER_TOOLS = frozenset({"get_patient_demographics", "get_allergies"})

# Maps tool_name → state field key
_TOOL_TO_STATE_FIELD: dict[str, str] = {
    "get_patient_demographics": "demographics",
    "get_problem_list": "conditions",
    "get_medications": "medications",
    "get_allergies": "allergies",
    "get_lab_results": "lab_results",
    "get_vitals_history": "vitals",
    "get_encounter_notes": "encounters",
    "get_immunizations": "immunizations",
    "get_procedures": "procedures",
}

# Model class per state field (for validation)
_FIELD_MODEL_MAP: dict[str, Any] = {
    "demographics": PatientDemographics,
    "conditions": Condition,
    "medications": Medication,
    "allergies": Allergy,
    "lab_results": LabResult,
    "vitals": VitalSign,
    "encounters": Encounter,
    "immunizations": Immunization,
    "procedures": Procedure,
}

# State list fields (everything except demographics)
_LIST_FIELDS = [
    "conditions", "medications", "allergies", "lab_results",
    "vitals", "encounters", "immunizations", "procedures",
]

# Regex to find [Source: <id>] citations
_SOURCE_RE = re.compile(r"\[Source:\s*([^\]]+)\]")

# Max context window tokens (80K — conservative for large models)
_MAX_CONTEXT_TOKENS = 80_000

# Recent encounters to keep in full detail; older ones are summarised
_RECENT_ENCOUNTER_FULL = 5


# ---------------------------------------------------------------------------
# Token counting (optional tiktoken, fallback to char estimate)
# ---------------------------------------------------------------------------

try:
    import tiktoken as _tiktoken
    _ENC = _tiktoken.get_encoding("cl100k_base")

    def _count_tokens(text: str) -> int:
        return len(_ENC.encode(text))

except ImportError:
    def _count_tokens(text: str) -> int:
        # Rough estimate: 1 token ≈ 4 chars
        return len(text) // 4


# ---------------------------------------------------------------------------
# Markdown → HTML (optional markdown lib, fallback to pre-wrap)
# ---------------------------------------------------------------------------

try:
    import markdown as _md_lib

    def _md_to_html(text: str) -> str:
        return _md_lib.markdown(text, extensions=["extra", "sane_lists"])

except ImportError:
    def _md_to_html(text: str) -> str:
        escaped = html_stdlib.escape(text)
        return f"<pre style='white-space:pre-wrap;font-family:sans-serif'>{escaped}</pre>"


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _load_specialty_prompt(specialty: str) -> str:
    """Load the specialty-specific system prompt from the prompts directory."""
    path = _PROMPTS_DIR / f"{specialty}.txt"
    if not path.exists():
        path = _PROMPTS_DIR / "general.txt"
    try:
        return path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.warning("Could not load specialty prompt for '%s': %s", specialty, exc)
        return ""


def _model_to_dict(obj: Any) -> dict[str, Any]:
    """Serialise a Pydantic model or plain dict to a plain dict."""
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    return dict(obj)


def _state_to_patient_data(state: SummarizerState) -> dict[str, Any]:
    """
    Convert the typed state fields back to a flat dict[section → list[dict]]
    format that the SummaryVerifier expects.

    Note: the verifier uses "labs" (not "lab_results") as the section key.
    """
    result: dict[str, Any] = {}

    if demo := state.get("demographics"):
        result["demographics"] = _model_to_dict(demo)

    for field in _LIST_FIELDS:
        items = state.get(field) or []
        if items:
            # lab_results → "labs" for verifier compatibility
            key = "labs" if field == "lab_results" else field
            result[key] = [_model_to_dict(item) for item in items]

    return result


def _validate_tool_data(field: str, raw_data: Any) -> Any:
    """
    Convert raw tool output (dict or Pydantic model) to the appropriate type.

    Demographics → PatientDemographics instance.
    List fields  → list of the appropriate model instances.
    Passes through already-validated instances unchanged.
    """
    model_cls = _FIELD_MODEL_MAP.get(field)
    if model_cls is None:
        return raw_data

    if field == "demographics":
        if isinstance(raw_data, PatientDemographics):
            return raw_data
        if isinstance(raw_data, dict):
            try:
                return PatientDemographics.model_validate(raw_data)
            except Exception as exc:
                logger.warning("Demographics validation error: %s", exc)
                return None
        return None
    else:
        # List field
        if not isinstance(raw_data, list):
            return []
        validated = []
        for item in raw_data:
            if isinstance(item, model_cls):
                validated.append(item)
            elif isinstance(item, dict):
                try:
                    validated.append(model_cls.model_validate(item))
                except Exception as exc:
                    logger.debug("Skipping invalid %s record: %s", field, exc)
        return validated


# ---------------------------------------------------------------------------
# Text formatting helpers (used by structure_data_node)
# ---------------------------------------------------------------------------


def _fmt(title: str, lines: list[str]) -> str:
    if not lines:
        return ""
    return f"## {title}\n" + "\n".join(lines)


def _fmt_demographics(demo: Optional[PatientDemographics]) -> str:
    if not demo:
        return ""
    d = _model_to_dict(demo)
    lines = [
        f"**Name:** {d.get('first_name', '')} {d.get('last_name', '')}".strip(),
        f"**DOB:** {d.get('date_of_birth', 'Unknown')} | **Sex:** {d.get('sex', 'Unknown')}",
    ]
    for label, field in [
        ("Race", "race"), ("Ethnicity", "ethnicity"),
        ("Language", "primary_language"), ("Insurance", "insurance_name"),
        ("PCP", "primary_care_provider"),
    ]:
        if d.get(field):
            lines.append(f"**{label}:** {d[field]}")
    rid = d.get("fhir_id") or d.get("patient_id", "N/A")
    lines.append(f"*[{rid}]*")
    return _fmt("Patient Demographics", lines)


def _fmt_allergies(allergies: list[Allergy]) -> str:
    if not allergies:
        return "## Allergies & Adverse Reactions\nNo known allergies documented."
    lines = []
    for a in allergies:
        d = _model_to_dict(a)
        sev = f" — severity: **{d['severity']}**" if d.get("severity") else ""
        rxn = f" — reaction: {d['reaction']}" if d.get("reaction") else ""
        status = d.get("clinical_status", "active")
        lines.append(
            f"- [{d.get('allergy_id', '?')}] **{d.get('substance', 'Unknown')}**"
            f"{rxn}{sev} | {status}"
        )
    return _fmt("Allergies & Adverse Reactions", lines)


def _fmt_conditions(conditions: list[Condition], specialty: str = "") -> str:
    if not conditions:
        return ""
    # Cardiology: sort cardiac conditions first
    cardiac_keywords = {"cardiac", "coronary", "heart", "hypertension", "htn",
                        "arrhythmia", "atrial", "ventricular", "aortic", "valve",
                        "cholesterol", "lipid", "atherosclerosis", "cad", "mi",
                        "angina", "hf", "ckd"}
    if specialty == "cardiology":
        def _cardiac_key(c: Condition) -> int:
            name_lower = (c.display_name or "").lower()
            return 0 if any(kw in name_lower for kw in cardiac_keywords) else 1
        conditions = sorted(conditions, key=_cardiac_key)

    lines = []
    for c in conditions:
        d = _model_to_dict(c)
        icd = f" (ICD-10: {d['icd10_code']})" if d.get("icd10_code") else ""
        onset = f" | onset: {d['onset_date']}" if d.get("onset_date") else ""
        resolved = f" | resolved: {d['resolved_date']}" if d.get("resolved_date") else ""
        status = d.get("clinical_status", "unknown")
        lines.append(
            f"- [{d.get('condition_id', '?')}] **{d.get('display_name', 'Unknown')}**"
            f"{icd} | {status}{onset}{resolved}"
        )
    return _fmt("Conditions / Problem List", lines)


def _fmt_medications(medications: list[Medication], specialty: str = "") -> str:
    if not medications:
        return ""
    # Psychiatry: sort psych meds first
    psych_keywords = {"antidepressant", "ssri", "snri", "antipsychotic", "mood stabilizer",
                      "anxiolytic", "benzodiazepine", "stimulant", "sertraline", "fluoxetine",
                      "escitalopram", "bupropion", "quetiapine", "risperidone", "aripiprazole",
                      "lithium", "valproate", "lamotrigine", "clonazepam", "lorazepam",
                      "alprazolam", "methylphenidate", "amphetamine", "venlafaxine", "duloxetine"}
    cardiac_keywords = {"metoprolol", "lisinopril", "losartan", "atorvastatin", "rosuvastatin",
                        "amlodipine", "furosemide", "carvedilol", "digoxin", "warfarin",
                        "aspirin", "clopidogrel", "apixaban", "rivaroxaban", "spironolactone",
                        "eplerenone", "hydralazine", "isosorbide", "nitrate"}
    if specialty == "psychiatry":
        def _psych_key(m: Medication) -> int:
            name_lower = (m.name or "").lower()
            return 0 if any(kw in name_lower for kw in psych_keywords) else 1
        medications = sorted(medications, key=_psych_key)
    elif specialty == "cardiology":
        def _cardiac_key(m: Medication) -> int:
            name_lower = (m.name or "").lower()
            return 0 if any(kw in name_lower for kw in cardiac_keywords) else 1
        medications = sorted(medications, key=_cardiac_key)

    lines = []
    for m in medications:
        d = _model_to_dict(m)
        dose = f" {d['dosage']}" if d.get("dosage") else ""
        freq = f" — {d['frequency']}" if d.get("frequency") else ""
        route = f" — {d['route']}" if d.get("route") else ""
        status = d.get("status", "unknown")
        lines.append(
            f"- [{d.get('medication_id', '?')}] **{d.get('name', 'Unknown')}**"
            f"{dose}{freq}{route} | {status}"
        )
    return _fmt("Medications", lines)


def _fmt_labs(labs: list[LabResult], specialty: str = "") -> str:
    if not labs:
        return ""
    # Cardiology: BNP, troponin, lipids first; Psychiatry: TSH, lithium, CBC first
    priority_keywords: dict[str, set[str]] = {
        "cardiology": {"bnp", "pro-bnp", "troponin", "ldl", "hdl", "triglyceride",
                       "cholesterol", "creatinine", "potassium", "inr", "hba1c"},
        "psychiatry": {"tsh", "lithium", "valproate", "prolactin", "glucose",
                       "cbc", "cmp", "bmp", "hba1c"},
    }
    kws = priority_keywords.get(specialty, set())
    if kws:
        def _lab_key(lab: LabResult) -> int:
            name_lower = (lab.test_name or "").lower()
            return 0 if any(kw in name_lower for kw in kws) else 1
        labs = sorted(labs, key=_lab_key)

    lines = []
    for lab in labs:
        d = _model_to_dict(lab)
        val = f": {d['value']}" if d.get("value") else ""
        unit = f" {d['unit']}" if d.get("unit") else ""
        ref = f" [ref: {d['reference_range']}]" if d.get("reference_range") else ""
        interp = d.get("interpretation", "")
        flag = ""
        if interp in ("H", "A"):
            flag = " ⬆ HIGH"
        elif interp == "L":
            flag = " ⬇ LOW"
        dt = f" | {str(d.get('effective_date', ''))[:10]}" if d.get("effective_date") else ""
        lines.append(
            f"- [{d.get('lab_id', '?')}] **{d.get('test_name', 'Unknown')}**"
            f"{val}{unit}{ref}{flag}{dt}"
        )
    return _fmt("Laboratory Results", lines)


def _fmt_vitals(vitals: list[VitalSign], specialty: str = "") -> str:
    if not vitals:
        return ""
    # Cardiology: sort BP first
    if specialty == "cardiology":
        def _vital_key(v: VitalSign) -> int:
            return 0 if "blood-pressure" in (v.type or "").lower() else 1
        vitals = sorted(vitals, key=_vital_key)
    # Pediatrics: sort growth metrics first
    elif specialty == "pediatrics":
        growth_types = {"body-weight", "body-height", "bmi", "head-circumference"}
        def _growth_key(v: VitalSign) -> int:
            return 0 if (v.type or "").lower() in growth_types else 1
        vitals = sorted(vitals, key=_growth_key)

    lines = []
    for v in vitals:
        d = _model_to_dict(v)
        unit = f" {d['unit']}" if d.get("unit") else ""
        dt = f" | {str(d.get('effective_date', ''))[:10]}" if d.get("effective_date") else ""
        lines.append(
            f"- [{d.get('vital_id', '?')}] **{d.get('type', 'unknown')}**: "
            f"{d.get('value', '?')}{unit}{dt}"
        )
    return _fmt("Vital Signs", lines)


def _fmt_encounters(
    encounters: list[Encounter],
    specialty: str = "",
    recent_n: int = _RECENT_ENCOUNTER_FULL,
) -> str:
    if not encounters:
        return ""
    # Sort most-recent first
    def _enc_key(e: Encounter) -> str:
        d = _model_to_dict(e)
        return str(d.get("date", "")) or ""
    encounters = sorted(encounters, key=_enc_key, reverse=True)

    lines: list[str] = []
    for i, enc in enumerate(encounters):
        d = _model_to_dict(enc)
        eid = d.get("encounter_id", "?")
        dt = str(d.get("date", ""))[:10] if d.get("date") else "?"
        prov = f" | {d['provider']}" if d.get("provider") else ""
        etype = d.get("encounter_type", "Encounter")

        if i < recent_n:
            # Full detail
            cc = f"\n  Chief complaint: {d['chief_complaint']}" if d.get("chief_complaint") else ""
            dx = (
                f"\n  Diagnoses: {', '.join(d['diagnoses'])}"
                if d.get("diagnoses")
                else ""
            )
            soap = ""
            if d.get("soap_note"):
                # Truncate long SOAP notes to keep context manageable
                note_text = d["soap_note"][:500]
                if len(d["soap_note"]) > 500:
                    note_text += "..."
                soap = f"\n  Note (excerpt): {note_text}"
            lines.append(f"- [{eid}] **{etype}** | {dt}{prov}{cc}{dx}{soap}")
        else:
            # Summarised (1-2 sentences)
            cc_part = d.get("chief_complaint") or etype
            dx_part = (", ".join(d["diagnoses"][:2]) if d.get("diagnoses") else "")
            summary = f"{cc_part}" + (f": {dx_part}" if dx_part else "")
            lines.append(f"- [{eid}] {dt}: {summary} (summary)")

    return _fmt("Clinical Encounters", lines)


def _fmt_immunizations(immunizations: list[Immunization], specialty: str = "") -> str:
    if not immunizations:
        return ""
    # Pediatrics: sort by status (overdue first)
    lines = []
    for imm in immunizations:
        d = _model_to_dict(imm)
        dt = f" | {d['occurrence_date']}" if d.get("occurrence_date") else ""
        dose = f" | dose {d['dose_number']}" if d.get("dose_number") else ""
        status = d.get("status", "unknown")
        lines.append(
            f"- [{d.get('immunization_id', '?')}] **{d.get('vaccine_name', 'Unknown')}**"
            f"{dose}{dt} | {status}"
        )
    return _fmt("Immunizations", lines)


def _fmt_procedures(procedures: list[Procedure]) -> str:
    if not procedures:
        return ""
    lines = []
    for proc in procedures:
        d = _model_to_dict(proc)
        dt = f" | {d['performed_date']}" if d.get("performed_date") else ""
        perf = f" | by: {d['performer']}" if d.get("performer") else ""
        lines.append(
            f"- [{d.get('procedure_id', '?')}] **{d.get('name', 'Unknown')}**"
            f"{dt}{perf} | {d.get('status', 'unknown')}"
        )
    return _fmt("Procedures", lines)


def _build_structured_context(state: SummarizerState) -> str:
    """
    Assemble a formatted text block from state fields, ordered and emphasised
    per the requesting specialty.  Allergies always appear second after demographics.
    """
    specialty = state.get("specialty", "primary_care")

    sections: list[str] = []

    # 1. Demographics (always first)
    if demo_text := _fmt_demographics(state.get("demographics")):
        sections.append(demo_text)

    # 2. Allergies (always second — patient safety)
    allergy_text = _fmt_allergies(state.get("allergies") or [])
    sections.append(allergy_text)

    # 3–9. Remaining sections ordered by specialty
    if specialty == "pediatrics":
        order = ["vitals", "immunizations", "conditions", "medications",
                 "lab_results", "encounters", "procedures"]
    elif specialty == "cardiology":
        order = ["conditions", "medications", "lab_results", "vitals",
                 "encounters", "procedures", "immunizations"]
    elif specialty == "psychiatry":
        order = ["medications", "encounters", "lab_results", "conditions",
                 "vitals", "procedures", "immunizations"]
    else:  # primary_care and general
        order = ["conditions", "medications", "lab_results", "vitals",
                 "encounters", "immunizations", "procedures"]

    for field in order:
        if field == "conditions":
            text = _fmt_conditions(state.get("conditions") or [], specialty)
        elif field == "medications":
            text = _fmt_medications(state.get("medications") or [], specialty)
        elif field == "lab_results":
            text = _fmt_labs(state.get("lab_results") or [], specialty)
        elif field == "vitals":
            text = _fmt_vitals(state.get("vitals") or [], specialty)
        elif field == "encounters":
            text = _fmt_encounters(state.get("encounters") or [], specialty)
        elif field == "immunizations":
            text = _fmt_immunizations(state.get("immunizations") or [], specialty)
        elif field == "procedures":
            text = _fmt_procedures(state.get("procedures") or [])
        else:
            text = ""
        if text:
            sections.append(text)

    return "\n\n".join(s for s in sections if s)


def _truncate_context(context: str, max_tokens: int = _MAX_CONTEXT_TOKENS) -> str:
    """
    Truncate the structured context to fit within max_tokens.
    Removes encounters section first (largest and most truncatable).
    """
    if _count_tokens(context) <= max_tokens:
        return context

    # Find and shorten the encounters section
    enc_start = context.find("## Clinical Encounters")
    if enc_start == -1:
        # No encounters section — hard truncate with notice
        approx_chars = max_tokens * 4
        return context[:approx_chars] + "\n\n[... context truncated to fit context window ...]"

    # Find the end of encounters section (next ## heading)
    enc_end_match = re.search(r"\n## ", context[enc_start + 1:])
    enc_end = (enc_start + 1 + enc_end_match.start()) if enc_end_match else len(context)
    enc_section = context[enc_start:enc_end]

    # Keep only the most recent encounters in full, summarise the rest
    enc_lines = enc_section.split("\n")
    bullet_indices = [i for i, ln in enumerate(enc_lines) if ln.startswith("- [")]
    if len(bullet_indices) > _RECENT_ENCOUNTER_FULL:
        keep_until = bullet_indices[_RECENT_ENCOUNTER_FULL]
        enc_section = "\n".join(enc_lines[:keep_until])
        enc_section += "\n- [older encounters omitted to fit context window]"

    truncated = context[:enc_start] + enc_section
    if enc_end < len(context):
        truncated += context[enc_end:]

    if _count_tokens(truncated) <= max_tokens:
        return truncated

    # Still too large — hard truncate with notice
    approx_chars = max_tokens * 4
    return truncated[:approx_chars] + "\n\n[... context truncated to fit context window ...]"


def _build_full_system_prompt(specialty_prompt: str, specialty: str, is_retry: bool) -> str:
    """Combine specialty prompt with universal citation/safety instructions."""
    specialty_display = specialty.replace("_", " ").title()
    base = (
        f"You are a clinical chart summarizer for a {specialty_display} provider.\n\n"
        f"{specialty_prompt}\n\n"
        "────────────────────────────────────────\n"
        "UNIVERSAL RULES (these override everything above):\n"
        "1. Begin every summary with the line:\n"
        "   ## ⚠️ DRAFT — AI-GENERATED — REQUIRES CLINICIAN REVIEW\n"
        "2. Cite EVERY clinical claim with [Source: <record-id>] using the exact "
        "bracketed ID shown in the data (e.g., [Source: med-001]). "
        "Uncited claims will be flagged as unverified.\n"
        "3. Use ONLY information present in the provided patient data. "
        "NEVER infer, extrapolate, fabricate, or add detail not in the source.\n"
        "4. If data is missing or sparse for a section, state "
        "'Limited data available' — do NOT fill in with assumptions.\n"
        "5. Use medical terminology appropriate for the specialty above.\n"
    )
    if is_retry:
        base += (
            "\n⚠️ STRICTER MODE (retry due to low verification confidence):\n"
            "- Re-read every claim and confirm it appears verbatim in the source data.\n"
            "- Remove any claim you cannot directly cite. Fewer, well-cited claims are "
            "better than many uncited ones.\n"
            "- Do not add any information beyond what the source data explicitly states.\n"
        )
    return base


# ---------------------------------------------------------------------------
# Node 1: retrieve_data_node (factory — injects tools via closure)
# ---------------------------------------------------------------------------


def make_retrieve_data_node(tools: list[Any]) -> Callable:
    """
    Factory that returns an async LangGraph node function pre-loaded with tools.

    Args:
        tools: List of FHIRTool instances.

    Returns:
        async node function: SummarizerState → dict
    """
    async def retrieve_data_node(state: SummarizerState) -> dict[str, Any]:
        from chart_summarizer.config import settings

        start_time = time.time()
        date_range = state.get("date_range_months") or settings.SUMMARY_DEFAULT_MONTHS
        date_from = (date.today() - timedelta(days=30 * date_range)).isoformat()

        async def _run_tool(tool: Any) -> tuple[Any, Any]:
            kwargs: dict[str, Any] = (
                {} if tool.tool_name in _NO_DATE_FILTER_TOOLS
                else {"date_from": date_from}
            )
            return tool, await tool.execute(state["patient_id"], **kwargs)

        raw = await asyncio.gather(
            *[_run_tool(t) for t in tools],
            return_exceptions=True,
        )

        errors: list[str] = list(state.get("errors") or [])
        updates: dict[str, Any] = {}

        for item in raw:
            if isinstance(item, Exception):
                logger.error("Tool raised exception: %s", item)
                errors.append(f"Tool error: {item}")
                continue
            tool, result = item
            field = _TOOL_TO_STATE_FIELD.get(tool.tool_name, tool.tool_name)
            if result.success:
                updates[field] = _validate_tool_data(field, result.data)
            else:
                err = f"{tool.tool_name}: {result.error_message}"
                logger.warning("Tool returned error: %s", err)
                errors.append(err)

        elapsed_ms = int((time.time() - start_time) * 1000)
        metadata = dict(state.get("metadata") or {})
        metadata["retrieval_time_ms"] = elapsed_ms

        # Ensure all list fields exist (empty list if tool failed)
        for field in _LIST_FIELDS:
            if field not in updates:
                updates[field] = []
        if "demographics" not in updates:
            updates["demographics"] = None

        updates["errors"] = errors
        updates["metadata"] = metadata
        return updates

    retrieve_data_node.__name__ = "retrieve_data_node"
    return retrieve_data_node


# ---------------------------------------------------------------------------
# Node 2: structure_data_node (standalone)
# ---------------------------------------------------------------------------


async def structure_data_node(state: SummarizerState) -> dict[str, Any]:
    """
    Organise raw FHIR data from state into a single formatted text block
    optimised for the LLM.

    Applies specialty-specific section ordering, highlights clinically important
    values, and truncates to fit within the context window using tiktoken.
    """
    requested = set(state.get("requested_sections") or [
        "demographics", "conditions", "medications", "allergies",
        "lab_results", "vitals", "encounters", "immunizations", "procedures",
    ])

    # Filter state to only requested sections
    filtered_state: dict[str, Any] = dict(state)
    all_fields = ["demographics"] + _LIST_FIELDS
    for field in all_fields:
        section_key = "lab_results" if field == "lab_results" else field
        if section_key not in requested and field != "demographics":
            filtered_state[field] = []

    context = _build_structured_context(filtered_state)  # type: ignore[arg-type]
    context = _truncate_context(context)

    return {"structured_context": context}


# ---------------------------------------------------------------------------
# Node 3: generate_summary_node (factory — injects LLM provider via closure)
# ---------------------------------------------------------------------------


def make_generate_summary_node(llm_provider: Optional[Any] = None) -> Callable:
    """
    Factory that returns an async LangGraph node function pre-loaded with an
    LLM provider.

    Args:
        llm_provider: LLMProvider instance, or None for lazy init from settings.

    Returns:
        async node function: SummarizerState → dict
    """
    _llm: list[Any] = [llm_provider]

    async def generate_summary_node(state: SummarizerState) -> dict[str, Any]:
        # Lazy initialise LLM provider
        if _llm[0] is None:
            from chart_summarizer.llm.factory import create_llm_provider
            _llm[0] = create_llm_provider()
        llm = _llm[0]

        specialty = state.get("specialty", "primary_care")
        retry_count = state.get("retry_count", 0)
        is_retry = retry_count > 0

        specialty_prompt = _load_specialty_prompt(specialty)
        system_prompt = _build_full_system_prompt(specialty_prompt, specialty, is_retry)
        user_content = state.get("structured_context", "")

        errors: list[str] = list(state.get("errors") or [])
        metadata: dict[str, Any] = dict(state.get("metadata") or {})

        gen_start = time.time()
        try:
            llm_response = await llm.generate(
                system_prompt=system_prompt,
                messages=[{"role": "user", "content": user_content}],
            )
        except Exception as exc:
            logger.error("LLM generation failed: %s", exc)
            errors.append(f"generate_summary_node: {exc}")
            metadata["model_used"] = ""
            return {
                "raw_summary": f"[ERROR] Summary generation failed: {exc}",
                "errors": errors,
                "metadata": metadata,
                "retry_count": retry_count + 1,
            }

        gen_ms = int((time.time() - gen_start) * 1000)
        metadata["model_used"] = llm_response.model
        metadata["input_tokens"] = (metadata.get("input_tokens", 0) + llm_response.input_tokens)
        metadata["output_tokens"] = (metadata.get("output_tokens", 0) + llm_response.output_tokens)
        metadata["generation_time_ms"] = gen_ms

        return {
            "raw_summary": llm_response.content,
            "errors": errors,
            "metadata": metadata,
            "retry_count": retry_count + 1,
        }

    generate_summary_node.__name__ = "generate_summary_node"
    return generate_summary_node


# ---------------------------------------------------------------------------
# Node 4: verify_summary_node (standalone)
# ---------------------------------------------------------------------------

# Claim categories for enhanced verification
_HALLUCINATION_PREFIXES = frozenset({"allerg", "med", "drug", "rx"})


async def verify_summary_node(state: SummarizerState) -> dict[str, Any]:
    """
    Parse the generated summary, cross-reference each [Source:] citation against
    the source data, and produce a VerificationResult.

    Enhancements over the base verifier:
    - Detects HALLUCINATED allergy/medication source IDs (cited but not in data)
      and flags them as severity=critical.
    - Adds a ⚠️ warning banner to raw_summary if confidence < 0.90.
    """
    from chart_summarizer.verification.verifier import SummaryVerifier

    raw_summary = state.get("raw_summary", "")
    patient_data = _state_to_patient_data(state)

    verifier = SummaryVerifier()
    result = await verifier.verify(
        summary_text=raw_summary,
        patient_data=patient_data,
    )

    # --- HALLUCINATION detection ---
    # Unverified claims whose source ID looks like an allergy or medication reference
    # but doesn't exist in source data → HALLUCINATION (severity: critical)
    hallucination_flags: list[str] = []
    for claim in result.unverified_claims:
        sid_lower = claim.source_id.lower()
        if any(sid_lower.startswith(pfx) or pfx in sid_lower
               for pfx in _HALLUCINATION_PREFIXES):
            hallucination_flags.append(
                f"⚠️ HALLUCINATION (severity=critical): Summary references "
                f"'{claim.source_id}' (allergy/medication) which does NOT exist "
                f"in the source data. Claim: \"{claim.claim_text}\""
            )

    if hallucination_flags:
        result = VerificationResult(
            verified_claims=result.verified_claims,
            unverified_claims=result.unverified_claims,
            confidence_score=result.confidence_score,
            confidence_level="RED",
            flags=result.flags + hallucination_flags,
        )

    # --- Warning banner if confidence < 0.90 ---
    updated_summary = raw_summary
    if result.confidence_score < 0.90:
        banner = (
            "\n> ⚠️ **LOW CONFIDENCE WARNING**: Verification score "
            f"{result.confidence_score:.0%}. Multiple claims could not be traced "
            "to source records. Clinician review is especially critical for this summary.\n\n"
        )
        # Insert banner after the DRAFT header line
        draft_line_end = raw_summary.find("\n", raw_summary.find("DRAFT"))
        if draft_line_end != -1:
            updated_summary = (
                raw_summary[: draft_line_end + 1]
                + banner
                + raw_summary[draft_line_end + 1:]
            )
        else:
            updated_summary = banner + raw_summary

    return {
        "verification_result": result,
        "raw_summary": updated_summary,
    }


# ---------------------------------------------------------------------------
# Node 5: format_output_node (standalone)
# ---------------------------------------------------------------------------

_BADGE_CSS: dict[str, str] = {
    "GREEN": "background:#22c55e;color:#fff;",
    "YELLOW": "background:#eab308;color:#fff;",
    "RED": "background:#ef4444;color:#fff;",
}

_DISCLAIMER = (
    "⚠️ This summary was generated by AI and requires clinician review before "
    "clinical use. Do not act on this summary without verifying against the "
    "original patient record."
)


async def format_output_node(state: SummarizerState) -> dict[str, Any]:
    """
    Assemble the final SummaryResponse with:
    - HTML-converted summary body
    - Confidence badge (GREEN / YELLOW / RED)
    - Citation list with source metadata
    - AI-generated disclaimer
    - Metadata (timing, model, token counts)
    - Any accumulated errors / warnings
    """
    from chart_summarizer.config import settings

    raw_summary = state.get("raw_summary", "")
    vr: Optional[VerificationResult] = state.get("verification_result")
    errors: list[str] = list(state.get("errors") or [])
    metadata: dict[str, Any] = dict(state.get("metadata") or {})

    # Determine confidence level
    if vr:
        confidence_level = vr.confidence_level
        confidence_score = vr.confidence_score
        verified_claims = vr.verified_claims
        unverified_claims = vr.unverified_claims
        flags = vr.flags
    else:
        confidence_level = "RED"
        confidence_score = 0.0
        verified_claims = []
        unverified_claims = []
        flags = ["Verification step did not complete."]

    # Build citation list from verified claims (with source metadata)
    citations: list[Citation] = list(verified_claims) + list(unverified_claims)

    # Convert markdown to HTML
    html_body = _md_to_html(raw_summary)

    # Build confidence badge HTML
    badge_css = _BADGE_CSS.get(confidence_level, _BADGE_CSS["RED"])
    badge_html = (
        f'<span style="{badge_css}padding:4px 10px;border-radius:4px;'
        f'font-weight:bold;font-size:0.9em;">AI Confidence: '
        f'{confidence_level} ({confidence_score:.0%})</span>'
    )

    # Build warnings section
    warning_html = ""
    if flags:
        warning_items = "".join(f"<li>{html_stdlib.escape(f)}</li>" for f in flags)
        warning_html = (
            f'<div style="background:#fef3c7;border:1px solid #f59e0b;'
            f'padding:8px;margin:8px 0;border-radius:4px;">'
            f"<strong>⚠️ Warnings:</strong><ul>{warning_items}</ul></div>"
        )

    # Build disclaimer HTML
    disclaimer_html = (
        f'<div style="background:#fee2e2;border:1px solid #ef4444;'
        f'padding:8px;margin:8px 0;border-radius:4px;">'
        f'<strong>{html_stdlib.escape(_DISCLAIMER)}</strong></div>'
    )

    # Full HTML summary for OpenEMR UI
    html_summary = (
        f'<div class="ai-chart-summary">'
        f"{disclaimer_html}"
        f'<div style="margin:8px 0">{badge_html}</div>'
        f"{warning_html}"
        f'<div class="summary-body">{html_body}</div>'
        f"</div>"
    )

    # Determine status
    if raw_summary.startswith("[ERROR]"):
        status = "failed"
    elif errors:
        status = "partial"
    else:
        status = "complete"

    # Build metadata model
    model_used = metadata.get("model_used") or settings.LLM_MODEL
    request_id = metadata.get("request_id") or str(uuid.uuid4())
    total_latency_ms = (
        (metadata.get("retrieval_time_ms", 0) or 0)
        + (metadata.get("generation_time_ms", 0) or 0)
    )

    summary_metadata = SummaryMetadata(
        request_id=request_id,
        patient_id=state.get("patient_id", "unknown"),
        model_used=model_used,
        provider=settings.LLM_PROVIDER,
        input_tokens=metadata.get("input_tokens", 0),
        output_tokens=metadata.get("output_tokens", 0),
        latency_ms=total_latency_ms,
        data_sections_retrieved=[
            f for f in (["demographics"] + _LIST_FIELDS)
            if state.get(f)
        ],
        specialty_context=state.get("specialty", "primary_care"),
    )

    vr_model = vr or VerificationResult(
        verified_claims=[],
        unverified_claims=[],
        confidence_score=confidence_score,
        confidence_level=confidence_level,  # type: ignore[arg-type]
        flags=flags,
    )

    final_summary = SummaryResponse(
        summary_text=raw_summary,
        html_summary=html_summary,
        citations=citations,
        confidence_level=confidence_level,  # type: ignore[arg-type]
        metadata=summary_metadata,
        verification_result=vr_model,
        status=status,  # type: ignore[arg-type]
        disclaimer=_DISCLAIMER,
    )

    return {"final_summary": final_summary}
