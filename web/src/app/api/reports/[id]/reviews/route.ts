/** Record a review decision. The API enforces the rules (reasons, review-before-approve);
 * this handler only checks the session and forwards with the user's signed token. */
import { NextResponse } from "next/server";

import { auth } from "@/auth";
import { ApiError, api } from "@/lib/api/server";

const DECISIONS = new Set(["confirm", "dismiss", "approve", "reject"]);

export async function POST(request: Request, ctx: RouteContext<"/api/reports/[id]/reviews">) {
  if (!(await auth())?.user) return NextResponse.json({ error: "not signed in" }, { status: 401 });
  const { id } = await ctx.params;
  if (!/^\d+$/.test(id)) return NextResponse.json({ error: "bad id" }, { status: 400 });
  const body = (await request.json().catch(() => ({}))) as { flag_id?: string; decision?: string; note?: string };
  if (!body.decision || !DECISIONS.has(body.decision)) {
    return NextResponse.json({ error: "unknown decision" }, { status: 422 });
  }
  try {
    const review = await api.addReview(Number(id), {
      decision: body.decision,
      ...(body.flag_id ? { flag_id: body.flag_id } : {}),
      ...(body.note?.trim() ? { note: body.note.trim() } : {}),
    });
    return NextResponse.json(review, { status: 201 });
  } catch (err) {
    const status = err instanceof ApiError ? err.status : 502;
    return NextResponse.json({ error: (err as Error).message }, { status });
  }
}
