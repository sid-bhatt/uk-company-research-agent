/** Archive (POST) or restore (DELETE) a report. Archiving hides it from lists; nothing is
 * deleted, because reports and their reviews are the audit record. */
import { NextResponse } from "next/server";

import { auth } from "@/auth";
import { apiFetch } from "@/lib/api/server";

async function forward(method: "POST" | "DELETE", ctx: RouteContext<"/api/reports/[id]/archive">) {
  if (!(await auth())?.user) return NextResponse.json({ error: "not signed in" }, { status: 401 });
  const { id } = await ctx.params;
  if (!/^\d+$/.test(id)) return NextResponse.json({ error: "bad id" }, { status: 400 });
  const res = await apiFetch(`/reports/${id}/archive`, { method });
  if (res.ok) return new Response(null, { status: 204 });
  const body = await res.json().catch(() => ({}));
  return NextResponse.json({ error: body.detail ?? "Could not update the report" }, { status: res.status });
}

export const POST = (_: Request, ctx: RouteContext<"/api/reports/[id]/archive">) => forward("POST", ctx);
export const DELETE = (_: Request, ctx: RouteContext<"/api/reports/[id]/archive">) => forward("DELETE", ctx);
