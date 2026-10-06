import Link from "next/link";
import { notFound } from "next/navigation";

import { ReportChat } from "@/components/ReportChat";
import { ReportProgress } from "@/components/ReportProgress";
import { ReportView } from "@/components/ReportView";
import { AuditTrail, FlagReview, ReportDecision } from "@/components/Review";
import { ApiError, api } from "@/lib/api/server";

export default async function ReportPage(props: PageProps<"/reports/[id]">) {
  const { id } = await props.params;
  if (!/^\d+$/.test(id)) notFound();
  const detail = await api.getReport(Number(id)).catch((err) => {
    if (err instanceof ApiError && err.status === 404) notFound();
    throw err;
  });

  return (
    <div className="space-y-6">
      <Link href="/" className="text-sm text-zinc-500 underline">← All reports</Link>
      {detail.status === "succeeded" && detail.report ? (
        <>
          <ReportDecision reportId={detail.id} flagIds={detail.report.flags.map((f) => f.id)} decisions={detail.decisions} />
          <ReportView
            report={detail.report}
            decisions={detail.decisions}
            flagActions={(flag) => <FlagReview reportId={detail.id} flagId={flag.id} current={detail.decisions[flag.id]} />}
          />
          <ReportChat reportId={detail.id} flags={detail.report.flags} evidence={detail.report.evidence} />
          <AuditTrail reviews={await api.listReviews(detail.id)} />
        </>
      ) : detail.status === "failed" ? (
        <div role="alert" className="rounded-lg border border-red-200 bg-red-50 p-6 dark:border-red-900 dark:bg-red-950">
          <h1 className="font-semibold">Report for {detail.company_number} failed</h1>
          <p className="mt-1 text-sm">{detail.error}</p>
        </div>
      ) : (
        <>
          <h1 className="text-2xl font-bold">KYB check for {detail.company_number}</h1>
          <ReportProgress reportId={detail.id} />
        </>
      )}
    </div>
  );
}
