# Two stages: dependencies resolve once and are cached, application code is the
# only layer that changes on a normal rebuild.
FROM python:3.12-slim AS base

# Tesseract is pinned into the image rather than left to the host, because
# ingestion's OCR accuracy depends on which binary is present — and README
# section 7 treats the preprocessing chain as part of the measured contract.
RUN apt-get update \
 && apt-get install --no-install-recommends -y \
      tesseract-ocr \
      libgl1 \
      libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

FROM base AS deps
# Lockfile first: dependency resolution is cached until the lock actually moves.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --extra service --no-install-project

FROM deps AS app
COPY src/ src/
COPY alembic.ini ./
COPY alembic/ alembic/
COPY data/policies/ data/policies/
RUN uv sync --locked --extra service

# Not root. A container that reads patient documents should not be able to
# rewrite its own code.
RUN useradd --create-home --uid 10001 rxauth \
 && mkdir -p /app/artifacts /app/reports \
 && chown -R rxauth:rxauth /app
USER rxauth

ENV PATH="/app/.venv/bin:$PATH" \
    RXAUTH_ENVIRONMENT=local \
    RXAUTH_LOG_FORMAT=json

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "rxauth_ai.api:app", "--host", "0.0.0.0", "--port", "8000"]
