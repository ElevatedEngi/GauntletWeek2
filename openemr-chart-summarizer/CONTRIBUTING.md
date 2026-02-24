# Contributing to OpenEMR Chart Summarizer

Thank you for your interest in contributing!

## Development Setup

1. Fork the repository
2. Clone your fork
3. Create a virtual environment: `python -m venv venv`
4. Activate: `venv\Scripts\activate` (Windows)
5. Install dependencies: `pip install -r agent/requirements.txt`
6. Run tests: `make test`

## Code Style

- Use type hints
- Follow PEP 8
- Add docstrings to all functions
- Run `make lint` before committing

## Pull Requests

- Create feature branches from `main`
- Write tests for new functionality
- Update documentation as needed
- Ensure all CI checks pass