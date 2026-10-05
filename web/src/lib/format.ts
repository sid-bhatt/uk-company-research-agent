/** Pure formatting and parsing helpers (unit-tested). */

export type Severity = "low" | "medium" | "high";

export const SEVERITY_ORDER: Record<Severity, number> = { high: 3, medium: 2, low: 1 };

/** Mirrors the API's normalisation: "502851" → "00502851", "sc123456" → "SC123456". */
export function normaliseCompanyNumber(input: string): string | null {
  let n = input.trim().toUpperCase().replace(/\s+/g, "");
  if (/^\d{1,7}$/.test(n)) n = n.padStart(8, "0");
  return /^[A-Z0-9]{8}$/.test(n) && /\d/.test(n) ? n : null;
}

export function money(value: string): string {
  const n = Number(value);
  if (!Number.isFinite(n)) return value;
  const abs = Math.abs(n).toLocaleString("en-GB", { maximumFractionDigits: 0 });
  return n < 0 ? `-£${abs}` : `£${abs}`;
}

export function date(value: string | null | undefined): string {
  if (!value) return "—";
  const d = new Date(value.length === 10 ? `${value}T00:00:00Z` : value);
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
}

export const RULE_LABELS: Record<string, string> = {
  overdue_accounts: "Overdue accounts",
  overdue_confirmation_statement: "Overdue confirmation statement",
  director_churn: "Director turnover",
  psc_changes: "Ownership (PSC) changes",
  office_changes: "Registered office changes",
  new_company_high_risk_sic: "New company in a higher-risk sector",
  officer_network: "Directors' other companies",
  financial_deterioration: "Filed accounts",
};

export const METRIC_LABELS: Record<string, string> = {
  net_assets: "Net assets",
  net_current_assets: "Net current assets",
  current_assets: "Current assets",
  cash: "Cash",
  turnover: "Turnover",
  profit_loss: "Profit / loss",
  employees: "Employees",
};

/** Score band for display. The score is a triage aid, not a probability. */
export function scoreBand(score: number): { label: string; tone: "low" | "medium" | "high" } {
  if (score >= 45) return { label: "High", tone: "high" };
  if (score >= 15) return { label: "Elevated", tone: "medium" };
  return { label: "Low", tone: "low" };
}
