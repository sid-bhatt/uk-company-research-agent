/** API types, generated from the backend's OpenAPI schema (npm run gen:api). */
import type { components } from "./schema";

type S = components["schemas"];
export type RiskReport = S["RiskReport"];
export type ReportDetail = S["ReportDetail"];
export type ReportSummary = S["ReportSummary"];
export type CompanyHit = S["CompanyHit"];
export type ReviewOut = S["ReviewOut"];
export type ReportFlag = S["ReportFlag"];
export type EvidenceItem = S["EvidenceItem"];
export type CheckResult = S["CheckResult"];
