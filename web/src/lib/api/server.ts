/**
 * Server-side API client (backend-for-frontend). The browser never calls the API directly:
 * Next.js verifies the user's session, then calls the API with a short-lived token naming
 * them, signed with API_JWT_SECRET. Locally, without a secret, it sends X-User instead.
 */
import "server-only";

import { SignJWT } from "jose";

import { auth } from "@/auth";
import type { CompanyHit, ReportDetail, ReportSummary, ReviewOut } from "./types";

const API_URL = process.env.API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function authHeaders(): Promise<Record<string, string>> {
  const session = await auth();
  const user = session?.user?.id;
  if (!user) throw new ApiError(401, "not signed in");
  const secret = process.env.API_JWT_SECRET;
  if (!secret) return { "X-User": user };
  const token = await new SignJWT({ name: session.user?.name ?? undefined })
    .setProtectedHeader({ alg: "HS256" })
    .setSubject(user)
    .setIssuer("ukco-web")
    .setAudience("ukco-api")
    .setIssuedAt()
    .setExpirationTime("5m")
    .sign(new TextEncoder().encode(secret));
  return { Authorization: `Bearer ${token}` };
}

export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = { ...(await authHeaders()), ...(init.headers as Record<string, string>) };
  return fetch(`${API_URL}${path}`, { ...init, headers, cache: "no-store" });
}

async function json<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await apiFetch(path, init);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new ApiError(res.status, typeof body.detail === "string" ? body.detail : res.statusText);
  }
  return (await res.json()) as T;
}

export const api = {
  listReports: (limit = 20, archived = false) =>
    json<ReportSummary[]>(`/reports?limit=${limit}${archived ? "&archived=true" : ""}`),
  getReport: (id: number) => json<ReportDetail>(`/reports/${id}`),
  createReport: (companyNumber: string, asOf?: string) =>
    json<{ id: number; status: string }>("/reports", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ company_number: companyNumber, ...(asOf ? { as_of: asOf } : {}) }),
    }),
  listReviews: (id: number) => json<ReviewOut[]>(`/reports/${id}/reviews`),
  addReview: (id: number, body: { flag_id?: string; decision: string; note?: string }) =>
    json<ReviewOut>(`/reports/${id}/reviews`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  searchCompanies: (q: string) =>
    json<CompanyHit[]>(`/companies/search?q=${encodeURIComponent(q)}&limit=8`),
};
