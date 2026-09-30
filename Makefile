.PHONY: setup up down migrate doctor ingest-metadata api docker-build test lint fmt

setup:            ## Install Python deps and git hooks
	cd backend && uv sync
	cd backend && uv run pre-commit install --config ../.pre-commit-config.yaml
	@test -f .env || (cp .env.example .env && echo "Created .env — add your Companies House key")

up:               ## Start local Postgres + pgvector
	docker compose up -d --wait db

down:
	docker compose down

migrate:          ## Apply database migrations
	cd backend && uv run ukco db upgrade

ingest-metadata:  ## Fetch profiles, officers, filings for all seed companies (safe to re-run)
	cd backend && uv run ukco ingest metadata

api:              ## Run the API locally with reload (http://localhost:8000/docs)
	cd backend && uv run uvicorn ukco.api.app:create_app --factory --reload

docker-build:     ## Build the API image
	docker build -t ukco-api:dev --build-arg GIT_SHA=$$(git rev-parse --short HEAD) backend

doctor:           ## Check local setup
	cd backend && uv run ukco doctor

test:
	cd backend && uv run pytest -v

lint:
	cd backend && uv run ruff check . && uv run ruff format --check . && uv run pyright

fmt:
	cd backend && uv run ruff check --fix . && uv run ruff format .
