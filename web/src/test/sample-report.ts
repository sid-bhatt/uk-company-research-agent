import type { RiskReport } from "@/lib/api/types";

/** A small synthetic report in the API's shape (no real company or people). */
export const sampleReport: RiskReport = {
  schema_version: "1",
  generated_at: "2026-10-01T09:00:00Z",
  as_of: "2026-10-01",
  live: true,
  company: {
    company_number: "12345678",
    name: "EXAMPLE TRADING LTD",
    status: "active",
    type: "ltd",
    incorporated: "2021-03-01",
    age_years: 5.6,
    sic_codes: ["62020"],
    registered_office: "ADDRESS 1",
    previous_names: [],
    current_officers: [{ name: "PERSON 1", role: "director", appointed_on: "2021-03-01", resigned_on: null }],
    recent_resignations: [],
    pscs: [],
    accounts_next_due: "2026-12-31",
    confirmation_statement_next_due: null,
    financials: [
      { metric: "net_assets", period_end: "2024-03-31", value: "44235.00", evidence_id: "E2" },
      { metric: "net_assets", period_end: "2025-03-31", value: "6855.00", evidence_id: "E3" },
    ],
    evidence_ids: ["E1"],
  },
  risk_score: 45,
  flags: [
    {
      id: "F2", flag_type: "office_changes", severity: "low", points: 5, title: "Frequent registered-office changes",
      explanation: "The registered office changed 2 times in the 24 months to 2026-10-01.", evidence_ids: ["E1"], details: {},
    },
    {
      id: "F1", flag_type: "net_liabilities", severity: "high", points: 30, title: "Net assets turned negative",
      explanation: "Net assets were -£12,000 at 2025-03-31, down from £15,000 at 2024-03-31.", evidence_ids: ["E2", "E3"], details: {},
    },
  ],
  checks: [
    { rule: "financial_deterioration", status: "flagged", note: null, flag_ids: ["F1"] },
    { rule: "officer_network", status: "not_assessed", note: "No network data.", flag_ids: [] },
    { rule: "overdue_accounts", status: "clear", note: "Next accounts due 2026-12-31.", flag_ids: [] },
  ],
  evidence: [
    { id: "E1", label: "Company profile", citation: { kind: "api", snapshot_id: 1, document_id: null, pointer: "", path: "/company/12345678", url: "https://find-and-update.company-information.service.gov.uk/company/12345678" } },
    { id: "E2", label: "Filed accounts, net assets at 2024-03-31", citation: { kind: "document", snapshot_id: null, document_id: "D1", pointer: "ix:x[1]", path: "/document/D1", url: "https://example.test/doc1" } },
    { id: "E3", label: "Filed accounts, net assets at 2025-03-31", citation: { kind: "document", snapshot_id: null, document_id: "D1", pointer: "ix:x[2]", path: "/document/D1", url: "https://example.test/doc2" } },
  ],
  narrative: null,
  provenance: {
    rules_version: "abc1234", policy_version: 1, policy_sha256: "f".repeat(64), ingestion_run_id: 1, snapshot_ids: [1],
    llm: null, narrative_status: "disabled", narrative_note: null, timings_ms: {}, companies_house_requests: 9, trace_id: null,
  },
  disclaimer: "Automated screening. Not advice.",
};
