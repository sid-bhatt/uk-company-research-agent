"""Typed views over Companies House API responses.

Models are deliberately tolerant: unknown fields are ignored and most fields are
optional, because the API omits keys rather than sending nulls and varies by
company type. Only fields we actually use downstream are declared.
"""

import datetime as dt

from pydantic import BaseModel, ConfigDict


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class Address(_Model):
    premises: str | None = None
    address_line_1: str | None = None
    address_line_2: str | None = None
    locality: str | None = None
    region: str | None = None
    postal_code: str | None = None
    country: str | None = None

    def one_line(self) -> str:
        parts = [
            self.premises,
            self.address_line_1,
            self.address_line_2,
            self.locality,
            self.region,
            self.postal_code,
            self.country,
        ]
        return ", ".join(p for p in parts if p)


# --- Search -----------------------------------------------------------------


class CompanySearchItem(_Model):
    company_number: str
    title: str
    company_status: str | None = None
    company_type: str | None = None
    date_of_creation: dt.date | None = None
    address_snippet: str | None = None


class CompanySearchResult(_Model):
    items: list[CompanySearchItem] = []
    total_results: int = 0
    start_index: int = 0
    items_per_page: int = 0


# --- Company profile ----------------------------------------------------------


class PreviousName(_Model):
    name: str
    ceased_on: dt.date | None = None
    effective_from: dt.date | None = None


class CompanyProfile(_Model):
    company_number: str
    company_name: str
    company_status: str | None = None
    type: str | None = None
    jurisdiction: str | None = None
    date_of_creation: dt.date | None = None
    date_of_cessation: dt.date | None = None
    registered_office_address: Address | None = None
    sic_codes: list[str] = []
    has_charges: bool | None = None
    has_insolvency_history: bool | None = None
    previous_company_names: list[PreviousName] = []


# --- Officers -----------------------------------------------------------------


class OfficerAppointmentLinks(_Model):
    appointments: str | None = None


class OfficerLinks(_Model):
    self: str | None = None  # /company/{number}/appointments/{appointment_id}
    officer: OfficerAppointmentLinks | None = None


class PartialDate(_Model):
    month: int | None = None
    year: int | None = None


class Officer(_Model):
    name: str
    officer_role: str
    appointed_on: dt.date | None = None
    resigned_on: dt.date | None = None
    nationality: str | None = None
    occupation: str | None = None
    country_of_residence: str | None = None
    date_of_birth: PartialDate | None = None
    links: OfficerLinks | None = None

    @property
    def is_active(self) -> bool:
        return self.resigned_on is None

    @property
    def officer_id(self) -> str | None:
        """Stable person id across companies, from `/officers/{id}/appointments`.

        This is what lets us answer "which directors sit on both boards?".
        """
        link = self.links.officer.appointments if self.links and self.links.officer else None
        if not link:
            return None
        parts = link.strip("/").split("/")
        return parts[1] if len(parts) >= 3 and parts[0] == "officers" else None

    @property
    def appointment_id(self) -> str | None:
        """Unique id of this appointment (one person, one role, one company)."""
        link = self.links.self if self.links else None
        if not link:
            return None
        parts = link.strip("/").split("/")
        return parts[3] if len(parts) == 4 and parts[2] == "appointments" else None


class OfficerList(_Model):
    items: list[Officer] = []
    total_results: int = 0
    active_count: int | None = None
    resigned_count: int | None = None
    start_index: int = 0
    items_per_page: int = 0


# --- Filing history -------------------------------------------------------------


class FilingLinks(_Model):
    self: str | None = None
    document_metadata: str | None = None


class FilingHistoryItem(_Model):
    transaction_id: str
    category: str | None = None
    type: str | None = None
    date: dt.date | None = None
    description: str | None = None
    description_values: dict[str, object] = {}
    pages: int | None = None
    barcode: str | None = None
    paper_filed: bool | None = None  # scanned paper filing → PDF likely has no text layer
    links: FilingLinks | None = None

    @property
    def document_id(self) -> str | None:
        url = self.links.document_metadata if self.links else None
        return url.rstrip("/").rsplit("/", 1)[-1] if url else None


class FilingHistoryList(_Model):
    items: list[FilingHistoryItem] = []
    total_count: int = 0
    start_index: int = 0
    items_per_page: int = 0
    filing_history_status: str | None = None


# --- Document API -------------------------------------------------------------------


class DocumentResource(_Model):
    content_length: int | None = None


class DocumentMetadata(_Model):
    company_number: str | None = None
    barcode: str | None = None
    category: str | None = None
    significant_date: dt.datetime | None = None
    pages: int | None = None
    created_at: dt.datetime | None = None
    resources: dict[str, DocumentResource] = {}

    @property
    def media_types(self) -> list[str]:
        """Downloadable formats, e.g. application/pdf, application/xhtml+xml."""
        return list(self.resources)
