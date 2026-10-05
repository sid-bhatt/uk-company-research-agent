/** Review rules, mirrored from the API (which enforces them) so the UI can explain them. */
export const MIN_REASON_CHARS = 10;

export function needsReason(decision: string): boolean {
  return decision === "dismiss" || decision === "reject";
}

export function reasonProblem(decision: string, note: string): string | null {
  if (!needsReason(decision)) return null;
  return note.trim().length >= MIN_REASON_CHARS
    ? null
    : `Give a reason (at least ${MIN_REASON_CHARS} characters): it goes in the audit trail.`;
}

export function pendingFlags(flagIds: string[], decided: Record<string, unknown>): string[] {
  return flagIds.filter((id) => !(id in decided));
}

export const DECISION_LABEL: Record<string, string> = {
  confirm: "Confirmed",
  dismiss: "Dismissed",
  approve: "Approved",
  reject: "Rejected",
};
