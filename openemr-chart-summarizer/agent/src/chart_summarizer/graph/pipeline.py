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

Graph topology (single agent, multi-step):

  retrieve → structure → summarize → verify → END

Each node is an async function that accepts the current PipelineState and
returns a dict of updated state keys.  Node implementations live inside
``create_pipeline()`` as closures so that tools and the LLM provider can be
injected at construction time (enabling easy testing without real APIs).
"""

import asyncio
import logging
import re
from datetime import date, timedelta
from typing import Any, Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from chart_summarizer.config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pipeline state
# ---------------------------------------------------------------------------


class PipelineState(TypedDict):
    """
    Shared state that flows through every node in the pipeline.

    Each node reads what it needs and writes back its outputs.
    All keys are optional so nodes only set what they produce.
    """

    # Input
    patient_id: str
    specialty: str
    date_range_months: int
    requested_sections: list[str]
    requesting_provider_id: Optional[str]

    # Produced by retrieve node
    patient_data: dict[str, Any]
    retrieval_errors: list[str]

    # Produced by structure node
    structured_data: dict[str, Any]

    # Produced by summarize node
    summary_text: str
    citations: list[dict[str, Any]]
    model_used: str
    input_tokens: int
    output_tokens: int

    # Produced by verify node
    verification_result: dict[str, Any]
    confidence_level: str

    # Pipeline metadata
    pipeline_errors: list[str]


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

# Maps tool_name → patient_data section key
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

# Sort field per section (for date-descending ordering in structure_node)
_DATE_SORT_FIELD: dict[str, str] = {
    "conditions": "onset_date",
    "medications": "start_date",
    "labs": "effective_date",
    "vitals": "effective_date",
    "encounters": "date",
    "immunizations": "occurrence_date",
    "procedures": "performed_date",
}

# Tools that should NOT be date-filtered (return all records always)
_NO_DATE_FILTER_TOOLS = frozenset({"get_patient_demographics", "get_allergies"})

# Regex to find [Source: <id>] citations in LLM output
_SOURCE_RE = re.compile(r"\[Source:\s*([^\]]+)\]")

# Section ID field names (used when building citation source_type index)
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


# ---------------------------------------------------------------------------
# Shared helpers (module-level, pure functions)
# ---------------------------------------------------------------------------


def _sort_by_date(records: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    """Sort records by a date-string field, most recent first. None sorts last."""

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
    """Render structured patient data as a human-readable block for the LLM."""
    sections: list[str] = []

    if demo := structured_data.get("demographics"):
        rid = demo.get("fhir_id") or demo.get("patient_id", "N/A")
        lines = [
            f"[{rid}] {demo.get('first_name', '')} {demo.get('last_name', '')}".strip(),
            f"DOB: {demo.get('date_of_birth', 'Unknown')} | "
            f"Sex: {demo.get('sex', 'Unknown')}",
        ]
        for label, field in (
            ("Race", "race"),
            ("Ethnicity", "ethnicity"),
            ("Language", "primary_language"),
            ("Insurance", "insurance_name"),
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
                if lab.get("reference_range")
                else ""
            )
            interp = (
                f" ({lab['interpretation']})" if lab.get("interpretation") else ""
            )
            dt = (
                f" | date: {str(lab.get('effective_date', ''))[:10]}"
                if lab.get("effective_date")
                else ""
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
                if v.get("effective_date")
                else ""
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
                if e.get("chief_complaint")
                else ""
            )
            dx = (
                f"\n  Diagnoses: {', '.join(e['diagnoses'])}"
                if e.get("diagnoses")
                else ""
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
    """Parse [Source: <id>] citations from LLM output into serialisable dicts."""
    _SECTION_TYPE: dict[str, str] = {
        "conditions": "condition",
        "medications": "medication",
        "allergies": "allergy",
        "labs": "lab",
        "vitals": "vital",
        "encounters": "encounter",
        "immunizations": "immunization",
        "procedures": "procedure",
        "demographics": "demographics",
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
        line = text[line_start : (line_end if line_end != -1 else len(text))]
        claim_text = _SOURCE_RE.sub("", line).strip(" -\u2022*#>").strip()
        citations.append(
            {
                "claim_text": claim_text,
                "source_type": id_to_type.get(source_id, "unknown"),
                "source_id": source_id,
                "source_date": None,
                "verified": False,
            }
        )
    return citations


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
        tools: List of FHIRTool instances to use for data retrieval.
               Defaults to create_mock_tools() when None (no live FHIR required).
        llm_provider: LLMProvider instance for the summarize node.
                      Defaults to create_llm_provider() (reads from settings) when None.

    Returns:
        A compiled LangGraph runnable that accepts PipelineState as input.
    """
    from chart_summarizer.tools.mock import create_mock_tools

    resolved_tools: list[Any] = tools if tools is not None else create_mock_tools()
    # Mutable list so the closure can lazily initialise without nonlocal assignment.
    _llm: list[Any] = [llm_provider]

    # -----------------------------------------------------------------------
    # Node 1 — Data Retrieval
    # -----------------------------------------------------------------------

    async def retrieve_node(state: PipelineState) -> dict[str, Any]:
        date_range = state.get("date_range_months", settings.SUMMARY_DEFAULT_MONTHS)
        date_from = (date.today() - timedelta(days=30 * date_range)).isoformat()

        async def _run(tool: Any) -> tuple[Any, Any]:
            kwargs: dict[str, Any] = (
                {}
                if tool.tool_name in _NO_DATE_FILTER_TOOLS
                else {"date_from": date_from}
            )
            return tool, await tool.execute(state["patient_id"], **kwargs)

        raw_results = await asyncio.gather(
            *[_run(t) for t in resolved_tools],
            return_exceptions=True,
        )

        patient_data: dict[str, Any] = {}
        retrieval_errors: list[str] = []

        for item in raw_results:
            if isinstance(item, Exception):
                logger.error("Tool raised exception: %s", item)
                retrieval_errors.append(f"Tool error: {item}")
                continue
            tool, result = item
            section = _TOOL_SECTION_MAP.get(tool.tool_name, tool.tool_name)
            if result.success:
                patient_data[section] = result.data
            else:
                err = f"{tool.tool_name}: {result.error_message}"
                logger.warning("Tool returned error: %s", err)
                retrieval_errors.append(err)

        return {
            "patient_data": patient_data,
            "retrieval_errors": retrieval_errors,
            "pipeline_errors": state.get("pipeline_errors", []),
        }

    # -----------------------------------------------------------------------
    # Node 2 — Data Structuring
    # -----------------------------------------------------------------------

    async def structure_node(state: PipelineState) -> dict[str, Any]:
        raw = state.get("patient_data", {})
        requested = set(
            state.get("requested_sections", list(_TOOL_SECTION_MAP.values()))
        )

        structured: dict[str, Any] = {}
        for section, data in raw.items():
            if section not in requested:
                continue
            if isinstance(data, list):
                if date_field := _DATE_SORT_FIELD.get(section):
                    data = _sort_by_date(data, date_field)
                if section == "encounters":
                    data = data[: settings.MAX_ENCOUNTERS_PER_SUMMARY]
                structured[section] = data
            else:
                structured[section] = data

        return {"structured_data": structured}

    # -----------------------------------------------------------------------
    # Node 3 — LLM Summarisation
    # -----------------------------------------------------------------------

    async def summarize_node(state: PipelineState) -> dict[str, Any]:
        if _llm[0] is None:
            from chart_summarizer.llm.factory import create_llm_provider

            _llm[0] = create_llm_provider()
        llm = _llm[0]

        structured_data = state.get("structured_data", {})
        specialty = state.get("specialty", "primary_care")
        system_prompt = _build_system_prompt(specialty)
        user_message = _format_patient_data(structured_data)

        try:
            llm_response = await llm.generate(
                system_prompt=system_prompt,
                messages=[{"role": "user", "content": user_message}],
            )
        except Exception as exc:
            logger.error("LLM generation failed: %s", exc)
            errors = list(state.get("pipeline_errors", []))
            errors.append(f"summarize_node: {exc}")
            return {
                "summary_text": f"[ERROR] Summary generation failed: {exc}",
                "citations": [],
                "model_used": "",
                "input_tokens": 0,
                "output_tokens": 0,
                "pipeline_errors": errors,
            }

        patient_data = state.get("patient_data", {})
        citations = _extract_citations(llm_response.content, patient_data)

        return {
            "summary_text": llm_response.content,
            "citations": citations,
            "model_used": llm_response.model,
            "input_tokens": llm_response.input_tokens,
            "output_tokens": llm_response.output_tokens,
        }

    # -----------------------------------------------------------------------
    # Node 4 — Post-Generation Verification
    # -----------------------------------------------------------------------

    async def verify_node(state: PipelineState) -> dict[str, Any]:
        from chart_summarizer.verification.verifier import SummaryVerifier

        verifier = SummaryVerifier()
        result = await verifier.verify(
            summary_text=state.get("summary_text", ""),
            patient_data=state.get("patient_data", {}),
        )

        return {
            "verification_result": result.model_dump(mode="json"),
            "confidence_level": result.confidence_level,
        }

    # -----------------------------------------------------------------------
    # Graph construction
    # -----------------------------------------------------------------------

    graph: StateGraph = StateGraph(PipelineState)

    graph.add_node("retrieve", retrieve_node)
    graph.add_node("structure", structure_node)
    graph.add_node("summarize", summarize_node)
    graph.add_node("verify", verify_node)

    graph.add_edge(START, "retrieve")
    graph.add_edge("retrieve", "structure")
    graph.add_edge("structure", "summarize")
    graph.add_edge("summarize", "verify")
    graph.add_edge("verify", END)

    return graph.compile()
