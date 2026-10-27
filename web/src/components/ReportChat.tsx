"use client";

import { useId, useState } from "react";

import type { EvidenceItem, ReportFlag } from "@/lib/api/types";
import { describeTool, readEvents, segments } from "@/lib/chat";

type Turn =
  | { role: "analyst"; text: string }
  | { role: "assistant"; text: string; steps: string[]; unknown: string[]; pending: boolean };

function Answer({ text, evidence }: { text: string; evidence: Map<string, EvidenceItem> }) {
  return (
    <p className="whitespace-pre-wrap">
      {segments(text).map((s, i) => {
        if ("text" in s) return s.text;
        const item = evidence.get(s.cite);
        if (item) {
          return (
            <a key={i} href={item.citation.url} target="_blank" rel="noopener noreferrer" title={item.label}
              className="mx-0.5 rounded bg-zinc-100 px-1 font-mono text-xs underline dark:bg-zinc-800">
              {s.cite}
            </a>
          );
        }
        if (s.cite.startsWith("F")) {
          return <a key={i} href={`#${s.cite}`} className="mx-0.5 rounded bg-zinc-100 px-1 font-mono text-xs underline dark:bg-zinc-800">{s.cite}</a>;
        }
        return <span key={i} className="font-mono text-xs text-red-600">[{s.cite}?]</span>;
      })}
    </p>
  );
}

/** "Why is this flagged?": questions about this report, answered from its own records.
 * Everything said is stored with the report; the assistant explains, the analyst decides. */
export function ReportChat({ reportId, flags, evidence }: { reportId: number; flags: ReportFlag[]; evidence: EvidenceItem[] }) {
  const inputId = useId();
  const byId = new Map(evidence.map((e) => [e.id, e]));
  const [turns, setTurns] = useState<Turn[]>([]);
  const [question, setQuestion] = useState("");
  const [conversationId, setConversationId] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [unavailable, setUnavailable] = useState(false);

  const updateLast = (fn: (t: Extract<Turn, { role: "assistant" }>) => Partial<Turn>) =>
    setTurns((ts) => {
      const last = ts.at(-1);
      if (last?.role !== "assistant") return ts;
      return [...ts.slice(0, -1), { ...last, ...fn(last) } as Turn];
    });

  async function ask(q: string) {
    q = q.trim();
    if (!q || busy) return;
    setBusy(true);
    setError(null);
    setQuestion("");
    setTurns((ts) => [...ts, { role: "analyst", text: q }, { role: "assistant", text: "", steps: [], unknown: [], pending: true }]);
    try {
      const res = await fetch(`/api/reports/${reportId}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: q, conversation_id: conversationId }),
      });
      if (!res.ok || !res.body) {
        const data = await res.json().catch(() => ({}));
        if (res.status === 503) setUnavailable(true);
        throw new Error((data.error as string | undefined) ?? "The assistant is unavailable");
      }
      for await (const ev of readEvents(res.body)) {
        if (ev.event === "conversation") setConversationId(ev.conversation_id);
        else if (ev.event === "text") updateLast((t) => ({ text: t.text + ev.text }));
        else if (ev.event === "tool") updateLast((t) => ({ steps: [...t.steps, describeTool(ev.name, ev.input)] }));
        else if (ev.event === "done") updateLast(() => ({ unknown: ev.unknown_citations, pending: false }));
        else if (ev.event === "error") throw new Error(ev.message);
      }
    } catch (err) {
      setError((err as Error).message);
      setTurns((ts) => (ts.at(-1)?.role === "assistant" && !ts.at(-1)?.text ? ts.slice(0, -1) : ts));
    } finally {
      updateLast(() => ({ pending: false }));
      setBusy(false);
    }
  }

  const suggestions = [...flags.map((f) => `Why is ${f.id} flagged?`), "Which checks passed, and what did they look at?"];

  return (
    <section aria-labelledby="chat-heading" className="rounded-lg border border-zinc-200 p-4 dark:border-zinc-800">
      <h2 id="chat-heading" className="text-lg font-semibold">Ask about this report</h2>
      <p className="mt-1 text-sm text-zinc-500">
        Answers come only from this report&apos;s records, with citations. The assistant explains; you decide. Conversations are kept with the audit trail.
      </p>

      {unavailable ? (
        <p role="status" className="mt-3 text-sm text-zinc-600 dark:text-zinc-400">
          The assistant is switched off on this server (no LLM API key is configured).
        </p>
      ) : (
        <>
          {turns.length > 0 && (
            <ol className="mt-4 space-y-3" aria-live="polite">
              {turns.map((t, i) =>
                t.role === "analyst" ? (
                  <li key={i} className="ml-8 rounded-lg bg-zinc-100 p-3 text-sm dark:bg-zinc-800">{t.text}</li>
                ) : (
                  <li key={i} className="mr-8 space-y-2 rounded-lg border border-zinc-200 p-3 text-sm dark:border-zinc-800">
                    {t.steps.length > 0 && (
                      <ul aria-label="What the assistant looked at" className="space-y-0.5 text-xs text-zinc-500">
                        {t.steps.map((s, j) => <li key={j}>↳ {s}</li>)}
                      </ul>
                    )}
                    {t.text ? <Answer text={t.text} evidence={byId} /> : t.pending && <p className="text-zinc-500">Thinking…</p>}
                    {t.unknown.length > 0 && (
                      <p role="alert" className="text-xs text-red-600">
                        This answer cites {t.unknown.join(", ")}, which {t.unknown.length === 1 ? "isn't" : "aren't"} in the report. Treat it with care.
                      </p>
                    )}
                  </li>
                ),
              )}
            </ol>
          )}

          {turns.length === 0 && (
            <div className="mt-3 flex flex-wrap gap-2">
              {suggestions.map((s) => (
                <button key={s} type="button" onClick={() => ask(s)} disabled={busy}
                  className="rounded-full border border-zinc-300 px-3 py-1 text-xs hover:bg-zinc-100 disabled:opacity-50 dark:border-zinc-700 dark:hover:bg-zinc-800">
                  {s}
                </button>
              ))}
            </div>
          )}

          <form className="mt-4 flex gap-2" onSubmit={(e) => { e.preventDefault(); void ask(question); }}>
            <label htmlFor={inputId} className="sr-only">Question</label>
            <input id={inputId} value={question} onChange={(e) => setQuestion(e.target.value)} maxLength={1000}
              placeholder="e.g. Why wasn't director churn flagged?" disabled={busy}
              className="flex-1 rounded-md border border-zinc-300 bg-transparent px-3 py-2 text-sm dark:border-zinc-700" />
            <button type="submit" disabled={busy || !question.trim()}
              className="rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900">
              {busy ? "Answering…" : "Ask"}
            </button>
          </form>
        </>
      )}
      {error && !unavailable && <p role="alert" className="mt-2 text-sm text-red-600">{error}</p>}
    </section>
  );
}
