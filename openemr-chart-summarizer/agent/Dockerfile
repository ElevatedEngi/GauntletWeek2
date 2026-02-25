# syntax=docker/dockerfile:1
# Copyright (C) 2026 OpenEMR Community — GPL v3

# ---------------------------------------------------------------------------
# To pin the base image digest before deploying to production, run:
#   docker pull python:3.12-slim
#   docker inspect --format='{{index .RepoDigests 0}}' python:3.12-slim
# Then replace the FROM lines with:
#   FROM python:3.12-slim@sha256:<digest> AS builder
# ---------------------------------------------------------------------------

# ===========================================================================
# Stage 1 — builder: compile C extensions, install all runtime deps.
# gcc is only needed here — it does NOT end up in the final image.
# ===========================================================================
FROM python:3.12-slim AS builder

# Install build tools (not carried into runtime stage)
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Create an isolated venv; everything installed here is copied to runtime.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install runtime dependencies only — strip dev/test packages.
# This keeps the runtime venv lean (no pytest, ruff, mypy, black, etc.).
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && grep -v -E "^\s*#|^\s*$|pytest|flake8|black|isort|mypy|ruff" requirements.txt \
      > /tmp/requirements-runtime.txt \
 && pip install --no-cache-dir -r /tmp/requirements-runtime.txt

# Install the package itself (non-editable → goes into site-packages).
COPY src/ ./src/
COPY pyproject.toml ./
RUN pip install --no-cache-dir --no-deps .


# ===========================================================================
# Stage 2 — runtime: minimal image with no build tools, no dev dependencies.
# ===========================================================================
FROM python:3.12-slim AS runtime

# curl is the only extra binary needed (HEALTHCHECK).
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Copy only the lean runtime venv from the builder stage.
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Non-root user with explicit uid/gid 1000 (avoids uid-collision with host).
RUN groupadd --gid 1000 app \
 && useradd --uid 1000 --gid 1000 --no-create-home --shell /bin/false app

WORKDIR /app
RUN chown app:app /app

USER app

# PORT is set by Railway/Fly/Render at runtime; default 8000 for local Docker.
EXPOSE 8000

# Health check against the versioned endpoint (matches railway.toml + monitoring).
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f "http://localhost:${PORT:-8000}/api/v1/health" || exit 1

CMD ["sh", "-c", "uvicorn chart_summarizer.main:app --host 0.0.0.0 --port ${PORT:-8000}"]