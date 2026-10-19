"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

/** Archive hides a report from the list (asks first); restore brings it back. */
export function ArchiveButton({ reportId, label, archived = false }: { reportId: number; label: string; archived?: boolean }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    if (!archived && !window.confirm(`Archive the report on ${label}? It's hidden from this list, not deleted, and can be restored.`)) return;
    setBusy(true);
    const res = await fetch(`/api/reports/${reportId}/archive`, { method: archived ? "DELETE" : "POST" });
    setBusy(false);
    if (!res.ok) return setError(((await res.json().catch(() => ({}))) as { error?: string }).error ?? "Failed");
    router.refresh();
  }

  return (
    <span className="inline-flex items-center gap-2">
      <button type="button" onClick={run} disabled={busy} aria-label={`${archived ? "Restore" : "Archive"} report on ${label}`}
        className="rounded border border-zinc-300 px-2 py-0.5 text-xs text-zinc-600 hover:bg-zinc-100 disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-400 dark:hover:bg-zinc-800">
        {busy ? "…" : archived ? "Restore" : "Archive"}
      </button>
      {error && <span role="alert" className="text-xs text-red-600">{error}</span>}
    </span>
  );
}
