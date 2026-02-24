.PHONY: dev test lint format eval build clean

dev:
	docker-compose -f docker-compose.dev.yml up --build

test:
	cd agent && python -m pytest tests/

lint:
	cd agent && python -m flake8 src/ tests/
	cd agent && python -m mypy src/

format:
	cd agent && python -m black src/ tests/
	cd agent && python -m isort src/ tests/

eval:
	cd eval && python scripts/run_eval.py

build:
	docker build -t openemr-chart-summarizer agent/

clean:
	docker-compose down -v
	rm -rf agent/__pycache__ agent/src/__pycache__ agent/tests/__pycache__