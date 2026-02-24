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
LangGraph pipeline for the Chart Summarizer Agent.

Graph topology:

  retrieve_data → [conditional] → structure_data → generate_summary
                       |                                   ↓
                  (all failed)                       verify_summary → [conditional] → format_output
                       |                                                    |
                       └──────────── format_output ◄────────────────────── ┘
                                                       (low confidence &
                                                        retry_count < 1 → back to generate_summary)

Conditional edges:
  - After retrieve_data: if ALL tools failed → skip to format_output
  - After verify_summary: if confidence < 0.50 AND retry_count < 1 → regenerate

Backward-compat note: the helper functions _build_system_prompt, _extract_citations,
_fmt_section, _format_patient_data, _sort_by_date are preserved here so existing
tests that import from chart_summarizer.graph.pipeline continue to work.
"""

import logging
import re
from typing import Any, Optional

from langgraph.graph import END, START, StateGraph

from chart_summarizer.graph.nodes import (
    _LIST_FIELDS,
    format_output_node,
    make_generate_summary_node,
    make_retrieve_data_node,
    structure_data_node,
    verify_summary_node,
)
from chart_summarizer.graph.state import SummarizerState

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Backward-compatible helpers (kept so existing test imports still work)
# ---------------------------------------------------------------------------

_SOURCE_RE = re.compile(r"\[Source:\s*([^\]]+)\]")

_TOOL_SECTION_MAP: dict[str, str] = {
    "get_patient_demographics": "demographics",
    "get_problem_list": "conditions",
    "get_medications": "medications",
    "get_allergies": "allergies",
    "get_lab_results": "labs",
    "get_vitals_history": "vitals",
    "get_encounter_notes": "encounters",
    "get_immunizations": "immunizations",
    "get_procedures": "procedures",
}

_DATE_SORT_FIELD: dict[str, str] = {
    "conditions": "onset_date",
    "medications": "start_date",
    "labs": "effective_date",
    "vitals": "effective_date",
    "encounters": "date",
    "immunizations": "occurrence_date",
    "procedures": "performed_date",
}

_SECTION_ID_FIELDS: dict[str, tuple[str, ...]] = {
    "demographics": ("fhir_id", "patient_id"),
    "conditions": ("condition_id",),
    "medications": ("medication_id",),
    "allergies": ("allergy_id",),
    "labs": ("lab_id",),
    "vitals": ("vital_id",),
    "encounters": ("encounter_id",),
    "immunizations": ("immunization_id",),
    "procedures": ("procedure_id",),
}


def _sort_by_date(records: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    def _key(r: dict[str, Any]) -> str:
        v = r.get(field)
        return str(v) if v else ""
    return sorted(records, key=_key, reverse=True)


def _build_system_prompt(specialty: str) -> str:
    specialty_display = specialty.replace("_", " ").title()
    return (
        f"You are a clinical chart summarization assistant supporting a "
        f"{specialty_display} provider.\n\n"
        "Generate a DRAFT patient chart summary from the structured data below.\n\n"
        "RULES:\n"
        "1. Use ONLY information present in the provided patient data. "
        "Never infer, extrapolate, or add detail.\n"
        "2. Cite every clinical claim with [Source: <record-id>] using the exact "
        "bracketed ID shown in the data (e.g., [Source: med-001]).\n"
        "3. Organise into sections matching the available data: Demographics, "
        "Active Problems, Medications, Allergies, Recent Labs, Vital Signs, "
        "Recent Encounters, Immunizations, Procedures. Omit empty sections.\n"
        "4. Begin with the line:\n"
        "   ## \u26a0\ufe0f DRAFT \u2014 AI-GENERATED \u2014 REQUIRES CLINICIAN REVIEW\n"
        f"5. Use clinical language appropriate for a {specialty_display} provider.\n"
        "6. Every uncited claim will be flagged as unverified in post-generation "
        "review, so cite every clinical fact.\n"
    )


def _fmt_section(title: str, lines: list[str]) -> str:
    if not lines:
        return ""
    return f"=== {title} ===\n" + "\n".join(lines)


def _format_patient_data(structured_data: dict[str, Any]) -> str:
    sections: list[str] = []

    if demo := structured_data.get("demographics"):
        rid = demo.get("fhir_id") or demo.get("patient_id", "N/A")
        lines = [
            f"[{rid}] {demo.get('first_name', '')} {demo.get('last_name', '')}".strip(),
            f"DOB: {demo.get('date_of_birth', 'Unknown')} | "
            f"Sex: {demo.get('sex', 'Unknown')}",
        ]
        for label, field in (
            ("Race", "race"), ("Ethnicity", "ethnicity"),
            ("Language", "primary_language"), ("Insurance", "insurance_name"),
            ("PCP", "primary_care_provider"),
        ):
            if demo.get(field):
                lines.append(f"{label}: {demo[field]}")
        sections.append(_fmt_section("PATIENT DEMOGRAPHICS", lines))

    if conds := structured_data.get("conditions"):
        lines = []
        for c in conds:
            icd = f" (ICD-10: {c['icd10_code']})" if c.get("icd10_code") else ""
            onset = f" | onset: {c['onset_date']}" if c.get("onset_date") else ""
            lines.append(
                f"[{c.get('condition_id', '?')}] "
                f"{c.get('display_name', 'Unknown')}{icd} | "
                f"{c.get('clinical_status', 'unknown')}{onset}"
            )
        sections.append(_fmt_section("CONDITIONS / PROBLEM LIST", lines))

    if meds := structured_data.get("medications"):
        lines = []
        for m in meds:
            dose = f" {m['dosage']}" if m.get("dosage") else ""
            freq = f" \u2014 {m['frequency']}" if m.get("frequency") else ""
            route = f" \u2014 {m['route']}" if m.get("route") else ""
            lines.append(
                f"[{m.get('medication_id', '?')}] "
                f"{m.get('name', 'Unknown')}{dose}{freq}{route} | "
                f"{m.get('status', 'unknown')}"
            )
        sections.append(_fmt_section("MEDICATIONS", lines))

    if allergies := structured_data.get("allergies"):
        lines = []
        for a in allergies:
            reaction = f" \u2014 reaction: {a['reaction']}" if a.get("reaction") else ""
            sev = f" \u2014 severity: {a['severity']}" if a.get("severity") else ""
            lines.append(
                f"[{a.get('allergy_id', '?')}] "
                f"{a.get('substance', 'Unknown')}{reaction}{sev} | "
                f"{a.get('clinical_status', 'active')}"
            )
        sections.append(_fmt_section("ALLERGIES & ADVERSE REACTIONS", lines))

    if labs := structured_data.get("labs"):
        lines = []
        for lab in labs:
            val = f": {lab['value']}" if lab.get("value") else ""
            unit = f" {lab['unit']}" if lab.get("unit") else ""
            ref = (
                f" [ref: {lab['reference_range']}]"
                if lab.get("reference_range") else ""
            )
            interp = f" ({lab['interpretation']})" if lab.get("interpretation") else ""
            dt = (
                f" | date: {str(lab.get('effective_date', ''))[:10]}"
                if lab.get("effective_date") else ""
            )
            lines.append(
                f"[{lab.get('lab_id', '?')}] "
                f"{lab.get('test_name', 'Unknown')}{val}{unit}{ref}{interp}{dt}"
            )
        sections.append(_fmt_section("LABORATORY RESULTS", lines))

    if vitals := structured_data.get("vitals"):
        lines = []
        for v in vitals:
            unit = f" {v['unit']}" if v.get("unit") else ""
            dt = (
                f" | date: {str(v.get('effective_date', ''))[:10]}"
                if v.get("effective_date") else ""
            )
            lines.append(
                f"[{v.get('vital_id', '?')}] "
                f"{v.get('type', 'unknown')}: {v.get('value', '?')}{unit}{dt}"
            )
        sections.append(_fmt_section("VITAL SIGNS", lines))

    if encs := structured_data.get("encounters"):
        lines = []
        for e in encs:
            dt = f" | date: {str(e.get('date', ''))[:10]}" if e.get("date") else ""
            prov = f" | provider: {e['provider']}" if e.get("provider") else ""
            cc = (
                f"\n  Chief complaint: {e['chief_complaint']}"
                if e.get("chief_complaint") else ""
            )
            dx = (
                f"\n  Diagnoses: {', '.join(e['diagnoses'])}"
                if e.get("diagnoses") else ""
            )
            lines.append(
                f"[{e.get('encounter_id', '?')}] "
                f"{e.get('encounter_type', 'Encounter')}{dt}{prov}{cc}{dx}"
            )
        sections.append(_fmt_section("ENCOUNTERS", lines))

    if imms := structured_data.get("immunizations"):
        lines = []
        for i in imms:
            dt = f" | date: {i['occurrence_date']}" if i.get("occurrence_date") else ""
            dose = f" | dose: {i['dose_number']}" if i.get("dose_number") else ""
            lines.append(
                f"[{i.get('immunization_id', '?')}] "
                f"{i.get('vaccine_name', 'Unknown')}{dose}{dt}"
            )
        sections.append(_fmt_section("IMMUNIZATIONS", lines))

    if procs := structured_data.get("procedures"):
        lines = []
        for p in procs:
            dt = f" | date: {p['performed_date']}" if p.get("performed_date") else ""
            perf = f" | by: {p['performer']}" if p.get("performer") else ""
            lines.append(
                f"[{p.get('procedure_id', '?')}] "
                f"{p.get('name', 'Unknown')}{dt}{perf} | {p.get('status', 'unknown')}"
            )
        sections.append(_fmt_section("PROCEDURES", lines))

    return "\n\n".join(s for s in sections if s)


def _extract_citations(
    text: str, patient_data: dict[str, Any]
) -> list[dict[str, Any]]:
    _SECTION_TYPE: dict[str, str] = {
        "conditions": "condition", "medications": "medication",
        "allergies": "allergy", "labs": "lab", "vitals": "vital",
        "encounters": "encounter", "immunizations": "immunization",
        "procedures": "procedure", "demographics": "demographics",
    }
    id_to_type: dict[str, str] = {}
    for section, data in patient_data.items():
        type_label = _SECTION_TYPE.get(section, section)
        id_fields = _SECTION_ID_FIELDS.get(section, ())
        if isinstance(data, list):
            for rec in data:
                if isinstance(rec, dict):
                    for field in id_fields:
                        if rid := rec.get(field):
                            id_to_type[str(rid)] = type_label
                            break
        elif isinstance(data, dict):
            for field in id_fields:
                if rid := data.get(field):
                    id_to_type[str(rid)] = type_label
                    break

    citations: list[dict[str, Any]] = []
    for match in _SOURCE_RE.finditer(text):
        source_id = match.group(1).strip()
        line_start = text.rfind("\n", 0, match.start()) + 1
        line_end = text.find("\n", match.end())
        line = text[line_start: (line_end if line_end != -1 else len(text))]
        claim_text = _SOURCE_RE.sub("", line).strip(" -\u2022*#>").strip()
        citations.append({
            "claim_text": claim_text,
            "source_type": id_to_type.get(source_id, "unknown"),
            "source_id": source_id,
            "source_date": None,
            "verified": False,
        })
    return citations


# ---------------------------------------------------------------------------
# Conditional edge routing
# ---------------------------------------------------------------------------


def _route_after_retrieve(state: SummarizerState) -> str:
    """Skip to format_output if ALL tools failed (no data at all)."""
    any_data = (
        state.get("demographics") is not None
        or any(bool(state.get(f)) for f in _LIST_FIELDS)
    )
    return "structure_data" if any_data else "format_output"


def _route_after_verify(state: SummarizerState) -> str:
    """Retry generation if confidence < 50% and retry budget remains."""
    vr = state.get("verification_result")
    retry_count = state.get("retry_count", 0)

    if vr is not None:
        score = (
            vr.confidence_score
            if hasattr(vr, "confidence_score")
            else vr.get("confidence_score", 1.0)  # type: ignore[union-attr]
        )
        if score < 0.50 and retry_count < 1:
            logger.info(
                "Confidence %.0f%% < 50%%; routing to regenerate (retry_count=%d)",
                score * 100,
                retry_count,
            )
            return "generate_summary"

    return "format_output"


# ---------------------------------------------------------------------------
# Pipeline factory
# ---------------------------------------------------------------------------


def create_pipeline(
    tools: Optional[list[Any]] = None,
    llm_provider: Optional[Any] = None,
) -> Any:
    """
    Build and compile the LangGraph StateGraph for the chart summarizer.

    Args:
        tools: List of FHIRTool instances.  Defaults to mock tools when None.
        llm_provider: LLMProvider instance.  Defaults to create_llm_provider() when None.

    Returns:
        Compiled LangGraph runnable accepting SummarizerState as input.
    """
    from chart_summarizer.tools.mock import create_mock_tools

    resolved_tools = tools if tools is not None else create_mock_tools()

    retrieve_node = make_retrieve_data_node(resolved_tools)
    generate_node = make_generate_summary_node(llm_provider)

    graph: StateGraph = StateGraph(SummarizerState)

    graph.add_node("retrieve_data", retrieve_node)
    graph.add_node("structure_data", structure_data_node)
    graph.add_node("generate_summary", generate_node)
    graph.add_node("verify_summary", verify_summary_node)
    graph.add_node("format_output", format_output_node)

    graph.add_edge(START, "retrieve_data")

    graph.add_conditional_edges(
        "retrieve_data",
        _route_after_retrieve,
        {"structure_data": "structure_data", "format_output": "format_output"},
    )

    graph.add_edge("structure_data", "generate_summary")
    graph.add_edge("generate_summary", "verify_summary")

    graph.add_conditional_edges(
        "verify_summary",
        _route_after_verify,
        {"generate_summary": "generate_summary", "format_output": "format_output"},
    )

    graph.add_edge("format_output", END)

    return graph.compile()
