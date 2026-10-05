"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

const STAGES = [
  ["queued", "Queued"],
  ["fetching", "Fetching Companies House records"],
  ["storing_evidence", "Storing evidence"],
  ["assessing", "Running checks"],
  ["validating", "Verifying every citation"],
  ["writing_narrative", "Writing summary"],
] as const;

type Stage = (typeof STAGES)[number][0];

/** Live progress over server-sent events; refreshes the page when the report is ready. */
export function ReportProgress({ reportId }: { reportId: number }) {
  const router = useRouter();
  const [seen, setSeen] = useState<Stage[]>([]);
  const [failure, setFailure] = useState<string | null>(null);

  useEffect(() => {
    const source = new EventSource(`/api/reports/${reportId}/events`);
    source.addEventListener("queued", () => setSeen((s) => [...new Set([...s, "queued" as Stage])]));
    source.addEventListener("stage", (e) => {
      const stage = JSON.parse((e as MessageEvent).data).stage as Stage;
      setSeen((s) => [...new Set([...s, stage])]);
    });
    source.addEventListener("succeeded", () => {
      source.close();
      router.refresh();
    });
    source.addEventListener("failed", (e) => {
      source.close();
      setFailure(JSON.parse((e as MessageEvent).data).error ?? "Report failed");
      router.refresh();
    });
    return () => source.close();
  }, [reportId, router]);

  const current = seen.at(-1);
  return (
    <div className="rounded-lg border border-zinc-200 p-6 dark:border-zinc-800" aria-live="polite">
      <h2 className="font-semibold">Building report…</h2>
      <ol className="mt-3 space-y-1 text-sm">
        {STAGES.filter(([id]) => id !== "writing_narrative" || seen.includes(id)).map(([id, label]) => {
          const done = seen.includes(id) && id !== current;
          const active = id === current;
          return (
            <li key={id} className={done ? "text-zinc-500" : active ? "font-medium" : "text-zinc-400"}>
              <span aria-hidden className="mr-2 inline-block w-4">{done ? "✓" : active ? "…" : "·"}</span>
              {label}
            </li>
          );
        })}
      </ol>
      {failure && <p className="mt-3 text-sm text-red-700 dark:text-red-300">{failure}</p>}
    </div>
  );
}
