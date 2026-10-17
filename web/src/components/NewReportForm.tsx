"use client";

import { useRouter } from "next/navigation";
import { useEffect, useId, useState } from "react";

import type { CompanyHit } from "@/lib/api/types";
import { date, normaliseCompanyNumber } from "@/lib/format";

/** Type a company name (with suggestions) or a company number, then start a report. */
export function NewReportForm() {
  const router = useRouter();
  const listId = useId();
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<CompanyHit[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const asNumber = normaliseCompanyNumber(query);
  const searching = !asNumber && query.trim().length >= 2;
  const visibleHits = searching ? hits : [];

  useEffect(() => {
    if (!searching) return;
    const controller = new AbortController();
    const timer = setTimeout(async () => {
      const res = await fetch(`/api/companies/search?q=${encodeURIComponent(query)}`, { signal: controller.signal }).catch(() => null);
      if (res?.ok) setHits(await res.json());
    }, 250);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [query, searching]);

  async function start(companyNumber: string) {
    setBusy(true);
    setError(null);
    const res = await fetch("/api/reports", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ company_number: companyNumber }),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      setBusy(false);
      setError(body.error ?? "Could not start the report");
      return;
    }
    router.push(`/reports/${body.id}`);
  }

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (asNumber) void start(asNumber);
        else if (visibleHits[0]) void start(visibleHits[0].company_number);
      }}
      className="space-y-2"
    >
      <label htmlFor={`${listId}-q`} className="block text-sm font-medium">Company name or number</label>
      <div className="flex gap-2">
        <input
          id={`${listId}-q`}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="e.g. Greggs or 00502851"
          autoComplete="off"
          aria-controls={listId}
          className="w-full rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900"
        />
        <button
          type="submit"
          disabled={busy || (!asNumber && visibleHits.length === 0)}
          className="shrink-0 rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-40 dark:bg-zinc-100 dark:text-zinc-900"
        >
          {busy ? "Starting…" : "Run KYB check"}
        </button>
      </div>
      {asNumber && <p className="text-xs text-zinc-500">Company number {asNumber}</p>}
      {error && <p role="alert" className="text-sm text-red-700 dark:text-red-300">{error}</p>}
      {visibleHits.length > 0 && (
        <ul id={listId} className="divide-y divide-zinc-100 rounded-md border border-zinc-200 dark:divide-zinc-800 dark:border-zinc-800">
          {visibleHits.map((h) => (
            <li key={h.company_number}>
              <button
                type="button"
                onClick={() => void start(h.company_number)}
                disabled={busy}
                className="flex w-full items-baseline justify-between gap-4 px-3 py-2 text-left text-sm hover:bg-zinc-50 dark:hover:bg-zinc-800"
              >
                <span className={h.status === "active" ? "" : "opacity-60"}>
                  <span className="font-medium">{h.name}</span>{" "}
                  <span className="text-zinc-500">{h.company_number}</span>
                  {h.also_matched && <span className="ml-2 text-xs text-zinc-500">also matched: {h.also_matched}</span>}
                </span>
                <span className="text-xs text-zinc-500">{h.status} · {date(h.incorporated)}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </form>
  );
}
