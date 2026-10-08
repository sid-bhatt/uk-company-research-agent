.PHONY: setup up down migrate doctor ingest-metadata api web api-types docker-build prod-up prod-down test lint fmt

setup:            ## Install Python deps and git hooks
	cd backend && uv sync
	cd web && npm install
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

web:              ## Run the web app (http://localhost:3000); needs `make api` running
	cd web && npm run dev

api-types:        ## Regenerate the web app's TypeScript types after an API change
	cd backend && uv run ukco openapi
	cd web && npm run gen:api

docker-build:     ## Build the API image
	docker build -t ukco-api:dev --build-arg GIT_SHA=$$(git rev-parse --short HEAD) backend

prod-up:          ## Run the production stack locally (https://localhost); needs deploy/.env
	docker compose -f deploy/compose.yml --env-file deploy/.env up -d --wait

prod-down:
	docker compose -f deploy/compose.yml --env-file deploy/.env down

doctor:           ## Check local setup
	cd backend && uv run ukco doctor

test:
	cd backend && uv run pytest -v

lint:
	cd backend && uv run ruff check . && uv run ruff format --check . && uv run pyright

fmt:
	cd backend && uv run ruff check --fix . && uv run ruff format .
