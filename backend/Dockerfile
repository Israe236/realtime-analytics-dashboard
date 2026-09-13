# syntax=docker/dockerfile:1
# Build context: repository root (the uv workspace lock file lives there).

FROM python:3.13-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.3 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app

# Dependencies first: this layer is reused until uv.lock changes.
COPY pyproject.toml uv.lock .python-version ./
COPY backend/pyproject.toml backend/
COPY generator/pyproject.toml generator/
COPY bench/pyproject.toml bench/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package analytics-api --no-install-workspace

COPY backend backend
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package analytics-api --no-editable

FROM python:3.13-slim
RUN useradd --create-home --uid 1000 app
COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER app
EXPOSE 8000
CMD ["uvicorn", "--factory", "analytics_api.main:create_app", \
     "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
