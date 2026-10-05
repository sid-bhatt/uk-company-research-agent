import type { ReactNode } from "react";

import type { EvidenceItem, ReportFlag, ReviewOut, RiskReport } from "@/lib/api/types";
import { METRIC_LABELS, RULE_LABELS, SEVERITY_ORDER, date, money, scoreBand } from "@/lib/format";

const SEVERITY_STYLE = {
  high: "bg-red-100 text-red-800 ring-red-200 dark:bg-red-950 dark:text-red-200 dark:ring-red-900",
  medium: "bg-amber-100 text-amber-900 ring-amber-200 dark:bg-amber-950 dark:text-amber-200 dark:ring-amber-900",
  low: "bg-sky-100 text-sky-900 ring-sky-200 dark:bg-sky-950 dark:text-sky-200 dark:ring-sky-900",
} as const;

const STATUS_STYLE = {
  flagged: "text-red-700 dark:text-red-300",
  clear: "text-emerald-700 dark:text-emerald-300",
  not_assessed: "text-zinc-500 dark:text-zinc-400",
} as const;

const STATUS_LABEL = { flagged: "Flagged", clear: "Clear", not_assessed: "Not assessed" } as const;

export function SeverityBadge({ severity }: { severity: ReportFlag["severity"] }) {
  return (
    <span className={`rounded-full px-2 py-0.5 text-xs font-semibold uppercase tracking-wide ring-1 ${SEVERITY_STYLE[severity]}`}>
      {severity}
    </span>
  );
}

function EvidenceLinks({ ids, evidence }: { ids: string[]; evidence: Map<string, EvidenceItem> }) {
  return (
    <ul className="mt-3 flex flex-wrap gap-2" aria-label="Evidence">
      {ids.map((id) => {
        const item = evidence.get(id);
        if (!item) return null;
        return (
          <li key={id}>
            <a
              href={item.citation.url}
              target="_blank"
              rel="noopener noreferrer"
              title={item.label}
              className="inline-flex items-center gap-1 rounded-md border border-zinc-200 px-2 py-1 text-xs text-zinc-700 hover:border-zinc-400 hover:bg-zinc-50 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
            >
              <span className="font-mono font-semibold">{id}</span>
              <span className="max-w-56 truncate">{item.label}</span>
              <span aria-hidden>↗</span>
            </a>
          </li>
        );
      })}
    </ul>
  );
}

function FlagCard({
  flag,
  evidence,
  whyItMatters,
  decision,
  actions,
}: {
  flag: ReportFlag;
  evidence: Map<string, EvidenceItem>;
  whyItMatters?: string;
  decision?: ReviewOut;
  actions?: ReactNode;
}) {
  return (
    <article
      id={flag.id}
      className="rounded-lg border border-zinc-200 bg-white p-4 shadow-sm dark:border-zinc-800 dark:bg-zinc-900"
    >
      <header className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-sm text-zinc-500">{flag.id}</span>
        <h3 className="font-semibold text-zinc-900 dark:text-zinc-100">{flag.title}</h3>
        <SeverityBadge severity={flag.severity} />
        <span className="text-xs text-zinc-500">+{flag.points} points</span>
        {decision && !actions && (
          <span className="ml-auto rounded bg-zinc-100 px-2 py-0.5 text-xs text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300">
            {decision.decision === "confirm" ? "Confirmed" : "Dismissed"} by {decision.reviewer}
          </span>
        )}
      </header>
      <p className="mt-2 text-sm text-zinc-700 dark:text-zinc-300">{flag.explanation}</p>
      {whyItMatters && (
        <p className="mt-2 text-sm text-zinc-600 dark:text-zinc-400">
          <span className="font-medium">Why it matters: </span>
          {whyItMatters}
        </p>
      )}
      <EvidenceLinks ids={flag.evidence_ids} evidence={evidence} />
      {actions}
    </article>
  );
}

function Financials({ report, evidence }: { report: RiskReport; evidence: Map<string, EvidenceItem> }) {
  const figures = report.company.financials;
  if (!figures.length) return null;
  const periods = [...new Set(figures.map((f) => f.period_end))].sort();
  const metrics = [...new Set(figures.map((f) => f.metric))];
  return (
    <section aria-labelledby="financials-heading">
      <h2 id="financials-heading" className="text-lg font-semibold">Filed accounts</h2>
      <div className="mt-2 overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-zinc-500">
              <th className="py-1 pr-4 font-medium">Metric</th>
              {periods.map((p) => (
                <th key={p} className="py-1 pr-4 text-right font-medium">{date(p)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {metrics.map((metric) => (
              <tr key={metric} className="border-t border-zinc-100 dark:border-zinc-800">
                <td className="py-1 pr-4">{METRIC_LABELS[metric] ?? metric}</td>
                {periods.map((p) => {
                  const fig = figures.find((f) => f.metric === metric && f.period_end === p);
                  const item = fig ? evidence.get(fig.evidence_id) : undefined;
                  const value = fig ? (metric === "employees" ? fig.value : money(fig.value)) : "—";
                  return (
                    <td key={p} className="py-1 pr-4 text-right tabular-nums">
                      {item ? (
                        <a href={item.citation.url} target="_blank" rel="noopener noreferrer" className="underline decoration-dotted underline-offset-2" title={`${fig?.evidence_id}: ${item.label}`}>
                          {value}
                        </a>
                      ) : (
                        value
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function ReportView({
  report,
  decisions = {},
  flagActions,
}: {
  report: RiskReport;
  decisions?: Record<string, ReviewOut>;
  /** Review controls rendered under each flag (the report page passes them in). */
  flagActions?: (flag: ReportFlag) => ReactNode;
}) {
  const evidence = new Map(report.evidence.map((e) => [e.id, e]));
  const flags = [...report.flags].sort(
    (a, b) => SEVERITY_ORDER[b.severity] - SEVERITY_ORDER[a.severity] || b.points - a.points,
  );
  const why = new Map(report.narrative?.flag_explanations.map((e) => [e.flag_id, e.text]) ?? []);
  const band = scoreBand(report.risk_score);
  const c = report.company;

  return (
    <div className="space-y-8">
      <section className="flex flex-wrap items-start justify-between gap-6">
        <div>
          <p className="text-sm text-zinc-500">
            {c.company_number} · {c.status ?? "status unknown"} · incorporated {date(c.incorporated)}
          </p>
          <h1 className="text-2xl font-bold text-zinc-900 dark:text-zinc-50">{c.name}</h1>
          <p className="mt-1 text-sm text-zinc-500">
            As of {date(report.as_of)} ({report.live ? "live" : "back-test"}) · {report.flags.length} flag(s) ·{" "}
            {report.evidence.length} citations, all verified against stored records
          </p>
        </div>
        <div
          className={`rounded-xl px-5 py-3 text-center ring-1 ${SEVERITY_STYLE[band.tone]}`}
          aria-label={`Risk score ${report.risk_score} out of 100, ${band.label}`}
        >
          <div className="text-3xl font-bold tabular-nums">{report.risk_score}</div>
          <div className="text-xs font-semibold uppercase tracking-wide">{band.label} · of 100</div>
        </div>
      </section>

      {report.narrative && (
        <section aria-labelledby="summary-heading" className="rounded-lg bg-zinc-50 p-4 dark:bg-zinc-900">
          <h2 id="summary-heading" className="text-sm font-semibold uppercase tracking-wide text-zinc-500">Summary</h2>
          <p className="mt-1 text-zinc-800 dark:text-zinc-200">{report.narrative.summary}</p>
        </section>
      )}

      <section aria-labelledby="flags-heading">
        <h2 id="flags-heading" className="text-lg font-semibold">Red flags</h2>
        {flags.length === 0 ? (
          <p className="mt-2 text-sm text-zinc-600 dark:text-zinc-400">No red flags raised. See the checks below for what was examined.</p>
        ) : (
          <div className="mt-3 space-y-3">
            {flags.map((f) => (
              <FlagCard key={f.id} flag={f} evidence={evidence} whyItMatters={why.get(f.id)} decision={decisions[f.id]} actions={flagActions?.(f)} />
            ))}
          </div>
        )}
        {report.narrative && report.narrative.next_steps.length > 0 && (
          <div className="mt-4">
            <h3 className="text-sm font-semibold">Suggested next steps</h3>
            <ul className="mt-1 list-disc pl-5 text-sm text-zinc-700 dark:text-zinc-300">
              {report.narrative.next_steps.map((s) => <li key={s}>{s}</li>)}
            </ul>
          </div>
        )}
      </section>

      <section aria-labelledby="checks-heading">
        <h2 id="checks-heading" className="text-lg font-semibold">Every check</h2>
        <ul className="mt-2 divide-y divide-zinc-100 text-sm dark:divide-zinc-800">
          {report.checks.map((check) => (
            <li key={check.rule} className="flex flex-wrap gap-x-3 py-2">
              <span className={`w-28 shrink-0 font-medium ${STATUS_STYLE[check.status]}`}>{STATUS_LABEL[check.status]}</span>
              <span className="font-medium">{RULE_LABELS[check.rule] ?? check.rule}</span>
              <span className="text-zinc-600 dark:text-zinc-400">
                {check.status === "flagged" ? check.flag_ids.map((id) => <a key={id} href={`#${id}`} className="mr-1 underline">{id}</a>) : check.note}
              </span>
            </li>
          ))}
        </ul>
      </section>

      <section aria-labelledby="company-heading" className="grid gap-6 md:grid-cols-2">
        <div>
          <h2 id="company-heading" className="text-lg font-semibold">Company</h2>
          <dl className="mt-2 grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm">
            <dt className="text-zinc-500">Type</dt><dd>{c.type ?? "—"}</dd>
            <dt className="text-zinc-500">SIC codes</dt><dd>{c.sic_codes.join(", ") || "—"}</dd>
            <dt className="text-zinc-500">Registered office</dt><dd>{c.registered_office ?? "—"}</dd>
            {c.previous_names.length > 0 && (<><dt className="text-zinc-500">Previous names</dt><dd>{c.previous_names.join(", ")}</dd></>)}
            {c.accounts_next_due && (<><dt className="text-zinc-500">Accounts due</dt><dd>{date(c.accounts_next_due)}</dd></>)}
          </dl>
          <EvidenceLinks ids={c.evidence_ids} evidence={evidence} />
        </div>
        <div>
          <h2 className="text-lg font-semibold">People</h2>
          <ul className="mt-2 space-y-1 text-sm">
            {c.current_officers.map((o) => (
              <li key={`${o.name}-${o.role}`}>{o.name} <span className="text-zinc-500">· {o.role}, since {date(o.appointed_on)}</span></li>
            ))}
            {c.recent_resignations.map((o) => (
              <li key={`r-${o.name}-${o.role}`} className="text-zinc-500">{o.name} · {o.role}, resigned {date(o.resigned_on)}</li>
            ))}
            {c.pscs.map((p) => (
              <li key={`psc-${p.name}-${p.notified_on}`}>{p.name ?? p.kind} <span className="text-zinc-500">· significant control since {date(p.notified_on)}{p.ceased_on ? `, ceased ${date(p.ceased_on)}` : ""}</span></li>
            ))}
          </ul>
        </div>
      </section>

      <Financials report={report} evidence={evidence} />

      <footer className="border-t border-zinc-200 pt-4 text-xs text-zinc-500 dark:border-zinc-800">
        <p>{report.disclaimer}</p>
        <p className="mt-1">
          Rules <code>{report.provenance.rules_version}</code> · policy v{report.provenance.policy_version} · narrative{" "}
          {report.provenance.narrative_status}
          {report.provenance.llm ? ` (${report.provenance.llm.model}, $${report.provenance.llm.cost_usd.toFixed(4)})` : ""} ·{" "}
          {report.provenance.companies_house_requests ?? 0} Companies House requests
        </p>
      </footer>
    </div>
  );
}
