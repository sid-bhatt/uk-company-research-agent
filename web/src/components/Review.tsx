"use client";

import { useRouter } from "next/navigation";
import { useId, useState } from "react";

import type { ReviewOut } from "@/lib/api/types";
import { date } from "@/lib/format";
import { DECISION_LABEL, pendingFlags, reasonProblem } from "@/lib/review";

async function submit(reportId: number, body: { flag_id?: string; decision: string; note?: string }) {
  const res = await fetch(`/api/reports/${reportId}/reviews`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (res.ok) return null;
  const data = await res.json().catch(() => ({}));
  return (data.error as string | undefined) ?? "Could not save the decision";
}

/** Confirm or dismiss one flag. Dismissing needs a reason; every decision is appended to
 * the audit trail, so changing your mind adds a new entry rather than editing the old one. */
export function FlagReview({
  reportId,
  flagId,
  current,
}: {
  reportId: number;
  flagId: string;
  current?: ReviewOut;
}) {
  const router = useRouter();
  const noteId = useId();
  const [decision, setDecision] = useState<"confirm" | "dismiss" | null>(null);
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function save() {
    if (!decision) return;
    const problem = reasonProblem(decision, note);
    if (problem) return setError(problem);
    setSaving(true);
    const failure = await submit(reportId, { flag_id: flagId, decision, note });
    setSaving(false);
    if (failure) return setError(failure);
    setDecision(null);
    setNote("");
    setError(null);
    router.refresh();
  }

  return (
    <div className="mt-3 border-t border-zinc-100 pt-3 dark:border-zinc-800">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        {current ? (
          <span className="text-zinc-600 dark:text-zinc-400">
            {DECISION_LABEL[current.decision]} by {current.reviewer}
            {current.note ? `: “${current.note}”` : ""}
          </span>
        ) : (
          <span className="text-zinc-500">Not reviewed yet.</span>
        )}
        <span className="ml-auto flex gap-2">
          {(["confirm", "dismiss"] as const).map((d) => (
            <button
              key={d}
              type="button"
              aria-pressed={decision === d}
              onClick={() => {
                setDecision(decision === d ? null : d);
                setError(null);
              }}
              className={`rounded-md border px-3 py-1 text-xs font-medium ${
                decision === d
                  ? "border-zinc-900 bg-zinc-900 text-white dark:border-zinc-100 dark:bg-zinc-100 dark:text-zinc-900"
                  : "border-zinc-300 dark:border-zinc-700"
              }`}
            >
              {d === "confirm" ? "Confirm flag" : "Dismiss flag"}
            </button>
          ))}
        </span>
      </div>
      {decision && (
        <div className="mt-2 space-y-2">
          <label htmlFor={noteId} className="block text-xs font-medium">
            {decision === "dismiss" ? "Why is this flag not a concern? (required)" : "Note (optional)"}
          </label>
          <textarea
            id={noteId}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            rows={2}
            maxLength={2000}
            className="w-full rounded-md border border-zinc-300 bg-white px-2 py-1 text-sm dark:border-zinc-700 dark:bg-zinc-900"
          />
          <button
            type="button"
            onClick={() => void save()}
            disabled={saving}
            className="rounded-md bg-zinc-900 px-3 py-1 text-xs font-medium text-white disabled:opacity-40 dark:bg-zinc-100 dark:text-zinc-900"
          >
            {saving ? "Saving…" : `Save: ${decision === "confirm" ? "confirm" : "dismiss"} ${flagId}`}
          </button>
        </div>
      )}
      {error && <p role="alert" className="mt-2 text-xs text-red-700 dark:text-red-300">{error}</p>}
    </div>
  );
}

/** Final decision on the report. Approval is only possible once every flag is reviewed. */
export function ReportDecision({
  reportId,
  flagIds,
  decisions,
}: {
  reportId: number;
  flagIds: string[];
  decisions: Record<string, ReviewOut>;
}) {
  const router = useRouter();
  const noteId = useId();
  const [rejecting, setRejecting] = useState(false);
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const pending = pendingFlags(flagIds, decisions);
  const current = decisions.report;

  async function decide(decision: "approve" | "reject") {
    const problem = reasonProblem(decision, note);
    if (problem) return setError(problem);
    setSaving(true);
    const failure = await submit(reportId, { decision, note });
    setSaving(false);
    if (failure) return setError(failure);
    setRejecting(false);
    setNote("");
    setError(null);
    router.refresh();
  }

  return (
    <section aria-labelledby="decision-heading" className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
      <h2 id="decision-heading" className="text-lg font-semibold">Decision</h2>
      {current ? (
        <p className="mt-1 text-sm">
          <span className={current.decision === "approve" ? "font-semibold text-emerald-700 dark:text-emerald-300" : "font-semibold text-red-700 dark:text-red-300"}>
            {DECISION_LABEL[current.decision]}
          </span>{" "}
          by {current.reviewer} on {date(current.created_at)}
          {current.note ? `: “${current.note}”` : ""}
        </p>
      ) : (
        <p className="mt-1 text-sm text-zinc-500">
          {pending.length
            ? `Review every flag before approving. Pending: ${pending.join(", ")}.`
            : "All flags reviewed. Approve or reject the customer."}
        </p>
      )}
      <div className="mt-3 flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => void decide("approve")}
          disabled={saving || pending.length > 0}
          title={pending.length ? `Pending: ${pending.join(", ")}` : undefined}
          className="rounded-md bg-emerald-700 px-4 py-2 text-sm font-medium text-white disabled:opacity-40"
        >
          Approve
        </button>
        <button
          type="button"
          onClick={() => setRejecting(!rejecting)}
          aria-expanded={rejecting}
          className="rounded-md border border-red-300 px-4 py-2 text-sm font-medium text-red-700 dark:border-red-800 dark:text-red-300"
        >
          Reject…
        </button>
      </div>
      {rejecting && (
        <div className="mt-3 space-y-2">
          <label htmlFor={noteId} className="block text-xs font-medium">Reason for rejecting (required)</label>
          <textarea
            id={noteId}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            rows={2}
            maxLength={2000}
            className="w-full rounded-md border border-zinc-300 bg-white px-2 py-1 text-sm dark:border-zinc-700 dark:bg-zinc-900"
          />
          <button
            type="button"
            onClick={() => void decide("reject")}
            disabled={saving}
            className="rounded-md bg-red-700 px-3 py-1 text-xs font-medium text-white disabled:opacity-40"
          >
            Confirm rejection
          </button>
        </div>
      )}
      {error && <p role="alert" className="mt-2 text-sm text-red-700 dark:text-red-300">{error}</p>}
    </section>
  );
}

/** Every decision ever made on this report, oldest first. Nothing here is ever edited. */
export function AuditTrail({ reviews }: { reviews: ReviewOut[] }) {
  return (
    <section aria-labelledby="audit-heading">
      <h2 id="audit-heading" className="text-lg font-semibold">Audit trail</h2>
      {reviews.length === 0 ? (
        <p className="mt-1 text-sm text-zinc-500">No decisions yet.</p>
      ) : (
        <ol className="mt-2 space-y-2 text-sm">
          {reviews.map((r) => (
            <li key={r.id} className="flex flex-wrap gap-x-2 border-l-2 border-zinc-200 pl-3 dark:border-zinc-700">
              <time dateTime={r.created_at} className="text-zinc-500">
                {new Date(r.created_at).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" })} UTC
              </time>
              <span className="font-medium">{r.reviewer}</span>
              <span>
                {DECISION_LABEL[r.decision].toLowerCase()} {r.target === "report" ? "the report" : r.target}
              </span>
              {r.note && <span className="text-zinc-600 dark:text-zinc-400">“{r.note}”</span>}
              <span className="text-xs text-zinc-400">rules {r.rules_version}{r.model_id ? ` · ${r.model_id}` : ""}</span>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
