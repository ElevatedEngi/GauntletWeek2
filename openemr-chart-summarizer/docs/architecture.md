# Architecture Overview

## OpenEMR Chart Summarizer Agent

This document describes the system architecture for the AI-powered patient chart summarizer.

---

## High-Level Architecture

```
┌─────────────────────────────────────────────────────┐
│                   OpenEMR (v8.0.0)                  │
│  ┌──────────────────────────────────────────────┐   │
│  │         Chart Summarizer PHP Module           │   │
│  │  (UI, settings, audit log, menu integration)  │   │
│  └──────────────────┬───────────────────────────┘   │
│                     │ HTTP POST /summarize            │
│  ┌──────────────────▼───────────────────────────┐   │
│  │           FHIR R4 API (OAuth2)                │   │
│  └──────────────────┬───────────────────────────┘   │
└─────────────────────┼───────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────┐
│          Python Agent Microservice (FastAPI)          │
│  ┌─────────────────────────────────────────────┐    │
│  │            LangGraph Pipeline                │    │
│  │  retrieve → structure → summarize → verify   │    │
│  └──────────────┬──────────────────┬───────────┘    │
│                 │                  │                  │
│  ┌──────────────▼──────┐  ┌───────▼──────────┐     │
│  │   9 FHIR Tools      │  │  LLM Provider    │     │
│  │   (read-only)       │  │  (abstraction)   │     │
│  └─────────────────────┘  └───────┬──────────┘     │
└───────────────────────────────────┼─────────────────┘
                                    │
                      ┌─────────────▼──────────────┐
                      │  Claude Haiku 4.5 (primary) │
                      │  GPT-4o-mini (fallback)     │
                      │  (HIPAA BAA required)        │
                      └────────────────────────────┘
```

---

## Component Descriptions

### OpenEMR PHP Module (`module/ChartSummarizer/`)

The PHP module integrates the summarizer directly into the OpenEMR user interface.

| Component | Description |
|-----------|-------------|
| `Module.php` | Zend/Laminas module entry point; registers routes and event listeners |
| `SummarizerController` | Handles HTTP requests; ACL checks; proxies to Python microservice |
| `install.sql` | Creates module database tables (`chart_summarizer_requests`, etc.) |
| `index.phtml` | Frontend template; renders the summary UI and action buttons |
| `summarizer.js` | Client-side JS; submits form via fetch(); renders Markdown; handles actions |

### Python Agent Microservice (`agent/`)

A FastAPI + LangGraph microservice that runs the AI pipeline.

| Component | Description |
|-----------|-------------|
| `main.py` | FastAPI app factory with CORS, middleware, and route registration |
| `config.py` | Pydantic Settings; all config from environment variables |
| `api/routes.py` | POST `/summarize`, GET `/health`, GET `/config` |
| `api/middleware.py` | Request logging and HIPAA audit middleware |

### LangGraph Pipeline (`agent/src/chart_summarizer/graph/`)

A 4-node directed graph with no branching (linear pipeline).

```
retrieve → structure → summarize → verify → END
```

| Node | Input | Output |
|------|-------|--------|
| `retrieve_node` | `patient_id`, `date_range` | `patient_data`, `retrieval_errors` |
| `structure_node` | `patient_data` | `structured_data` |
| `summarize_node` | `structured_data`, `specialty` | `summary_text`, `citations` |
| `verify_node` | `summary_text`, `patient_data` | `verification_result`, `confidence_level` |

### LLM Abstraction (`agent/src/chart_summarizer/llm/`)

Provider-agnostic interface. The factory selects the implementation at runtime.

| Provider | Model | Use case |
|----------|-------|----------|
| `AnthropicProvider` | `claude-haiku-4-5-20251001` | Primary (best cost/quality) |
| `OpenAIProvider` | `gpt-4o-mini` | Fallback if Anthropic unavailable |
| _(future)_ | Llama 3 | Air-gapped / on-premise deployments |

### FHIR Tools (`agent/src/chart_summarizer/tools/`)

9 read-only tools, all extending `FHIRTool`:

1. `get_patient_demographics`
2. `get_problem_list`
3. `get_medications`
4. `get_allergies`
5. `get_lab_results`
6. `get_vitals_history`
7. `get_encounter_notes`
8. `get_immunizations`
9. `get_procedures`

All tools use OAuth2 bearer tokens and are retried on transient failures.

---

## Data Flow

1. Clinician opens a patient chart in OpenEMR and clicks "Generate Summary"
2. The PHP module POSTs `{ patient_pid, specialty }` to the Python microservice
3. The microservice validates the request and starts the LangGraph pipeline
4. The `retrieve` node calls all 9 FHIR tools in parallel
5. The `structure` node maps FHIR JSON to Pydantic models
6. The `summarize` node calls the LLM with structured data + specialty context
7. The `verify` node validates every claim against source FHIR records
8. The microservice returns a `SummaryResponse` to the PHP module
9. The PHP module renders the summary as a DRAFT in the OpenEMR UI
10. The clinician reviews, approves/edits/rejects, and optionally saves to chart

---

## Security Architecture

| Concern | Implementation |
|---------|---------------|
| PHI in transit | TLS 1.2+ for all connections |
| PHI at rest | AES-256-GCM for stored summaries |
| PHI in logs | HIPAA filter redacts names, DOBs, SSNs |
| LLM provider | HIPAA BAA with Anthropic/OpenAI required |
| API keys | AWS Secrets Manager; never in code or env files |
| FHIR auth | OAuth2 client credentials flow |
| Prompt injection | PHI sent as structured JSON, not free text |
| Audit log | Every request logged: who, when, patient PID, model |

---

## Directory Structure

```
openemr-chart-summarizer/
├── agent/                    # Python microservice
│   ├── src/chart_summarizer/ # Application code
│   └── tests/                # Unit, integration, eval tests
├── module/ChartSummarizer/   # OpenEMR PHP module
├── eval/                     # Evaluation datasets and scripts
├── docs/                     # This documentation
├── docker-compose.yml        # Production services
├── docker-compose.dev.yml    # Development overrides
└── Makefile                  # Common commands
```
