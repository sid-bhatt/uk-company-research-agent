import { NextResponse } from "next/server";

import { auth } from "@/auth";
import { ApiError, api } from "@/lib/api/server";

export async function GET(request: Request) {
  if (!(await auth())?.user) return NextResponse.json({ error: "not signed in" }, { status: 401 });
  const q = new URL(request.url).searchParams.get("q")?.trim() ?? "";
  if (q.length < 2) return NextResponse.json([]);
  try {
    return NextResponse.json(await api.searchCompanies(q.slice(0, 160)));
  } catch (err) {
    const status = err instanceof ApiError ? err.status : 502;
    return NextResponse.json({ error: (err as Error).message }, { status });
  }
}
