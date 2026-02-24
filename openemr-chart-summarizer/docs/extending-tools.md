# Extending the FHIR Tool Set

This guide explains how to add new data retrieval tools to the Chart Summarizer Agent.

---

## Overview

Each tool is a subclass of `FHIRTool` that fetches a specific FHIR resource type
for a patient. Tools are called in parallel during the `retrieve` node of the
LangGraph pipeline.

All tools are **read-only**. No write operations to the FHIR API are permitted.

---

## Step 1: Create the Tool Class

Create a new file in `agent/src/chart_summarizer/tools/`:

```python
# agent/src/chart_summarizer/tools/get_medications.py

from chart_summarizer.models.patient import Medication
from chart_summarizer.tools.base import FHIRTool, ToolResult


class GetMedicationsTool(FHIRTool):
    """Retrieve the patient's current medication list from FHIR."""

    @property
    def tool_name(self) -> str:
        return "get_medications"

    @property
    def description(self) -> str:
        return "Fetch active and recently stopped medications for the patient."

    async def execute(self, patient_id: str, **kwargs) -> ToolResult:
        try:
            # Call the FHIR MedicationRequest endpoint
            response = await self._fhir_get(
                f"/MedicationRequest",
                params={
                    "patient": patient_id,
                    "status": "active,completed,stopped",
                    "_count": "100",
                }
            )

            # Parse into Pydantic models
            medications = [
                Medication(
                    medication_id=entry["resource"]["id"],
                    name=entry["resource"]["medicationCodeableConcept"]["text"],
                    status=entry["resource"]["status"],
                    # ... map remaining fields
                )
                for entry in response.get("entry", [])
            ]

            return ToolResult(
                tool_name=self.tool_name,
                success=True,
                data=[m.model_dump() for m in medications],
                records_returned=len(medications),
            )

        except Exception as exc:
            return ToolResult(
                tool_name=self.tool_name,
                success=False,
                error_message=str(exc),
            )
```

---

## Step 2: Register the Tool

Add the tool to the retrieve node in `agent/src/chart_summarizer/graph/pipeline.py`:

```python
from chart_summarizer.tools.get_medications import GetMedicationsTool

async def retrieve_node(state: PipelineState) -> dict:
    tools = [
        GetMedicationsTool(),
        # ... other tools
    ]

    results = await asyncio.gather(
        *[tool.execute(state["patient_id"]) for tool in tools],
        return_exceptions=True,
    )

    patient_data = {}
    retrieval_errors = []

    for tool, result in zip(tools, results):
        if isinstance(result, Exception):
            retrieval_errors.append(f"{tool.tool_name}: {result}")
        elif result.success:
            patient_data[tool.tool_name] = result.data
        else:
            retrieval_errors.append(f"{tool.tool_name}: {result.error_message}")
            patient_data[tool.tool_name] = []  # graceful degradation

    return {"patient_data": patient_data, "retrieval_errors": retrieval_errors}
```

---

## Step 3: Add Unit Tests

Create a test file in `agent/tests/unit/`:

```python
# agent/tests/unit/test_get_medications.py

import pytest
from unittest.mock import AsyncMock, patch

from chart_summarizer.tools.get_medications import GetMedicationsTool


@pytest.mark.asyncio
async def test_happy_path(mock_fhir_medications_response):
    tool = GetMedicationsTool(fhir_base_url="http://mock-fhir:8080/fhir")

    with patch.object(tool, "_fhir_get", new=AsyncMock(return_value=mock_fhir_medications_response)):
        result = await tool.execute("TEST-001")

    assert result.success is True
    assert result.records_returned > 0


@pytest.mark.asyncio
async def test_empty_response():
    tool = GetMedicationsTool(fhir_base_url="http://mock-fhir:8080/fhir")

    with patch.object(tool, "_fhir_get", new=AsyncMock(return_value={"entry": []})):
        result = await tool.execute("TEST-001")

    assert result.success is True
    assert result.records_returned == 0
    assert result.data == []


@pytest.mark.asyncio
async def test_fhir_500_error():
    tool = GetMedicationsTool(fhir_base_url="http://mock-fhir:8080/fhir")

    with patch.object(tool, "_fhir_get", new=AsyncMock(side_effect=Exception("HTTP 500"))):
        result = await tool.execute("TEST-001")

    assert result.success is False
    assert "500" in result.error_message
```

---

## FHIR Resource Mapping Reference

| Tool | FHIR Resource | Key Fields |
|------|--------------|------------|
| `get_patient_demographics` | `Patient` | name, birthDate, gender |
| `get_problem_list` | `Condition` | code (ICD-10), clinicalStatus, onsetDateTime |
| `get_medications` | `MedicationRequest` | medicationCodeableConcept, status, dosageInstruction |
| `get_allergies` | `AllergyIntolerance` | substance, reaction, severity, clinicalStatus |
| `get_lab_results` | `Observation` (laboratory) | code (LOINC), valueQuantity, referenceRange |
| `get_vitals_history` | `Observation` (vital-signs) | code, valueQuantity, effectiveDateTime |
| `get_encounter_notes` | `Encounter` + `DocumentReference` | type, period, participant |
| `get_immunizations` | `Immunization` | vaccineCode (CVX), occurrenceDateTime, status |
| `get_procedures` | `Procedure` | code (CPT/SNOMED), performedDateTime, status |

---

## Error Handling Guidelines

Every tool must return a `ToolResult` — never raise exceptions to the pipeline.

| FHIR Error | Behaviour |
|-----------|-----------|
| 401 Unauthorized | Attempt token refresh; if still 401, return `success=False` |
| 404 Not Found | Return `success=True`, `data=[]`, `records_returned=0` |
| 429 Rate Limited | Retry with exponential backoff (×3); then return `success=False` |
| 500 Server Error | Retry ×2 with 1s delay; then return `success=False` |
| Timeout | Return `success=False`, note "FHIR API timeout" in error_message |

The pipeline's `retrieve` node treats `success=False` as a degraded (partial)
summary — it notes the missing section but continues to generate the summary
from available data.
