# =============================================================================
# Stage 1: builder — install Python deps into a venv we can copy in stage 2.
# =============================================================================
FROM python:3.11-slim AS builder

# Install build deps needed to compile psycopg2-binary and friends.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

# Create a virtualenv at /opt/venv that we'll copy to the runtime image.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install dependencies first (better layer caching when only app code changes).
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt


# =============================================================================
# Stage 2: runtime — minimal image, non-root user, healthcheck.
# =============================================================================
FROM python:3.11-slim AS runtime

# psycopg2 needs libpq at runtime.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root user (uid 1000) and use it for the app.
RUN groupadd --system --gid 1000 appuser \
    && useradd  --system --uid 1000 --gid appuser --home-dir /app --shell /bin/bash appuser \
    && mkdir -p /app /app/data /tmp/uploads \
    && chown -R appuser:appuser /app /tmp/uploads

# Copy the prebuilt venv from the builder stage.
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Copy only what we need at runtime (tests/, .github/, *.md excluded via .dockerignore).
COPY --chown=appuser:appuser app ./app
COPY --chown=appuser:appuser scripts ./scripts
COPY --chown=appuser:appuser transactions.csv ./transactions.csv
COPY --chown=appuser:appuser pyproject.toml ./pyproject.toml

USER appuser

EXPOSE 8000

# Liveness probe — the API itself answers this.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request, sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status == 200 else 1)"

# Entrypoint waits for the DB, runs ETL/init, then starts the API.
ENTRYPOINT ["python", "scripts/entrypoint.py"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
