import { NextResponse } from "next/server";

import { auth } from "@/auth";
import { ApiError, api } from "@/lib/api/server";
import { normaliseCompanyNumber } from "@/lib/format";

export async function POST(request: Request) {
  if (!(await auth())?.user) return NextResponse.json({ error: "not signed in" }, { status: 401 });
  const body = (await request.json().catch(() => ({}))) as { company_number?: string; as_of?: string };
  const number = normaliseCompanyNumber(body.company_number ?? "");
  if (!number) return NextResponse.json({ error: "Enter a valid company number" }, { status: 422 });
  try {
    const created = await api.createReport(number, body.as_of || undefined);
    return NextResponse.json({ id: created.id }, { status: 202 });
  } catch (err) {
    const status = err instanceof ApiError ? err.status : 502;
    return NextResponse.json({ error: (err as Error).message }, { status });
  }
}
