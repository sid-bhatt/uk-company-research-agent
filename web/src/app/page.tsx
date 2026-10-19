import Link from "next/link";

import { ArchiveButton } from "@/components/ArchiveButton";
import { NewReportForm } from "@/components/NewReportForm";
import { api } from "@/lib/api/server";
import { date } from "@/lib/format";

const STATUS_STYLE: Record<string, string> = {
  succeeded: "text-emerald-700 dark:text-emerald-300",
  failed: "text-red-700 dark:text-red-300",
  running: "text-amber-700 dark:text-amber-300",
  queued: "text-zinc-500",
};

export default async function Home(props: PageProps<"/">) {
  const archived = (await props.searchParams).archived === "1";
  const reports = await api.listReports(archived ? 50 : 20, archived);
  return (
    <div className="space-y-10">
      <section className="space-y-3">
        <h1 className="text-2xl font-bold">New KYB check</h1>
        <p className="max-w-2xl text-sm text-zinc-600 dark:text-zinc-400">
          Runs every check against Companies House records: filing deadlines, director and ownership changes,
          office moves, sector risk, directors&apos; other companies and filed accounts. Every flag cites the record it
          rests on, and an analyst reviews each one.
        </p>
        <NewReportForm />
      </section>

      <section>
        <div className="flex items-baseline justify-between">
          <h2 className="text-lg font-semibold">{archived ? "Archived reports" : "Recent reports"}</h2>
          <Link href={archived ? "/" : "/?archived=1"} className="text-sm text-zinc-500 underline">
            {archived ? "← Back to recent reports" : "Show archived"}
          </Link>
        </div>
        {reports.length === 0 ? (
          <p className="mt-2 text-sm text-zinc-500">{archived ? "Nothing archived." : "No reports yet."}</p>
        ) : (
          <table className="mt-2 w-full text-sm">
            <thead>
              <tr className="text-left text-zinc-500">
                <th className="py-1 font-medium">Company</th>
                <th className="py-1 font-medium">Score</th>
                <th className="py-1 font-medium">Status</th>
                <th className="py-1 font-medium">Decision</th>
                <th className="py-1 font-medium">Requested</th>
                <th className="py-1"><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {reports.map((r) => (
                <tr key={r.id} className="border-t border-zinc-200 dark:border-zinc-800">
                  <td className="py-2">
                    <Link href={`/reports/${r.id}`} className="underline">{r.company_name ?? r.company_number}</Link>
                    {r.company_name && <span className="ml-2 font-mono text-xs text-zinc-500">{r.company_number}</span>}
                  </td>
                  <td className="py-2 tabular-nums">{r.risk_score ?? "—"}</td>
                  <td className={`py-2 ${STATUS_STYLE[r.status] ?? ""}`}>{r.status}</td>
                  <td className="py-2">
                    {r.review_status === "approve" ? (
                      <span className="text-emerald-700 dark:text-emerald-300">Approved</span>
                    ) : r.review_status === "reject" ? (
                      <span className="text-red-700 dark:text-red-300">Rejected</span>
                    ) : r.status === "succeeded" ? (
                      <span className="text-zinc-500">Awaiting review</span>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="py-2 text-zinc-500">
                    {date(r.created_at)} · {r.requested_by}
                    {r.archived_at && <span className="block text-xs">archived {date(r.archived_at)} by {r.archived_by}</span>}
                  </td>
                  <td className="py-2 text-right">
                    <ArchiveButton reportId={r.id} label={r.company_name ?? r.company_number} archived={Boolean(r.archived_at)} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
