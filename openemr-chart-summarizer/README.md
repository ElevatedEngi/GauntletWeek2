# OpenEMR Chart Summarizer

An AI-powered patient chart summarizer for OpenEMR, providing automated summaries of patient medical records using advanced language models.

## Features

- FHIR-compliant data retrieval from OpenEMR
- AI-generated patient chart summaries
- HIPAA-safe logging and data handling
- Modular architecture with LLM abstraction
- Docker-based deployment

## Quick Start

1. Clone the repository
2. Copy `.env.example` to `.env` and configure your settings
3. Run `make dev` to start development environment
4. Access the API at `http://localhost:8000`

## Architecture

This project consists of:
- **Agent**: Python microservice handling AI summarization
- **Module**: OpenEMR PHP module for integration

## License

GPL v3