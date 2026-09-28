-- Runs once when the local Postgres volume is first created.
-- On AWS RDS the same statement runs from the first Alembic migration.
CREATE EXTENSION IF NOT EXISTS vector;
