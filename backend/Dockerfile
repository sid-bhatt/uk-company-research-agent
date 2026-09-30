# syntax=docker/dockerfile:1
# KYB API image. Built in CI, run on ECS Fargate (week 3). Also runs one-off tasks:
#   docker run … ukco db upgrade      /      docker run … ukco report <number>
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv
WORKDIR /app

# Dependencies first: this layer only rebuilds when the lockfile changes.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-install-project

COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
COPY seed ./seed
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev

ARG GIT_SHA=dev
ENV GIT_SHA=${GIT_SHA} PATH="/app/.venv/bin:${PATH}" CACHE_DIR=/tmp/ukco-cache
RUN useradd --uid 10001 --no-create-home app
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')" || exit 1
CMD ["uvicorn", "ukco.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
