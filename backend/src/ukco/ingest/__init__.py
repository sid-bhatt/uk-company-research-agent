"""Ingestion pipeline steps. Plain functions: callable from the CLI, Airflow, or an ECS task."""

import csv
from dataclasses import dataclass
from pathlib import Path

SEED_FILE = Path(__file__).resolve().parents[3] / "seed" / "companies.csv"


@dataclass(frozen=True)
class SeedCompany:
    company_number: str
    name: str
    group: str


def load_seed(path: Path = SEED_FILE) -> list[SeedCompany]:
    """Companies to ingest: rows in seed/companies.csv that have a company number."""
    with path.open() as f:
        return [
            SeedCompany(row["company_number"], row["name"], row["group"])
            for row in csv.DictReader(f)
            if row["company_number"]
        ]
