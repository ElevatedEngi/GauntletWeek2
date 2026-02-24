FROM python:3.12-slim

# Create non-root user
RUN useradd --create-home --shell /bin/bash app

WORKDIR /app

# System deps: gcc for cryptography, curl for healthcheck
RUN apt-get update && apt-get install -y \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first (layer-cache friendly)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source and install as a package so chart_summarizer is importable
COPY src/ ./src/
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e . --no-deps

RUN chown -R app:app /app
USER app

# PORT is set by Railway/Fly/Render at runtime; default to 8000 for local Docker
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:${PORT:-8000}/health || exit 1

CMD ["sh", "-c", "uvicorn chart_summarizer.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
