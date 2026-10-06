/** Ask the report assistant a question. Streams the API's server-sent events through,
 * with the user's signed token; errors before the stream starts come back as JSON. */
import { NextResponse } from "next/server";

import { auth } from "@/auth";
import { apiFetch } from "@/lib/api/server";

export async function POST(request: Request, ctx: RouteContext<"/api/reports/[id]/chat">) {
  if (!(await auth())?.user) return NextResponse.json({ error: "not signed in" }, { status: 401 });
  const { id } = await ctx.params;
  if (!/^\d+$/.test(id)) return NextResponse.json({ error: "bad id" }, { status: 400 });
  const body = (await request.json().catch(() => ({}))) as { question?: unknown; conversation_id?: unknown };
  const question = typeof body.question === "string" ? body.question.trim() : "";
  if (!question || question.length > 1000) {
    return NextResponse.json({ error: "Questions must be 1–1000 characters" }, { status: 422 });
  }
  const conversationId = Number.isInteger(body.conversation_id) ? body.conversation_id : null;

  const upstream = await apiFetch(`/reports/${id}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, conversation_id: conversationId }),
    signal: request.signal, // the analyst closing the page stops the model
  });
  if (!upstream.ok || !upstream.body) {
    const data = await upstream.json().catch(() => ({}));
    const error = typeof data.detail === "string" ? data.detail : "The assistant is unavailable";
    return NextResponse.json({ error }, { status: upstream.status });
  }
  return new Response(upstream.body, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      "X-Accel-Buffering": "no",
    },
  });
}
