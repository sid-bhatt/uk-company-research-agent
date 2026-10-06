/** Server-sent progress events, proxied from the API with the user's token. */
import { auth } from "@/auth";
import { apiFetch } from "@/lib/api/server";

export async function GET(_request: Request, ctx: RouteContext<"/api/reports/[id]/events">) {
  if (!(await auth())?.user) return new Response("not signed in", { status: 401 });
  const { id } = await ctx.params;
  if (!/^\d+$/.test(id)) return new Response("bad id", { status: 400 });
  const upstream = await apiFetch(`/reports/${id}/events`);
  if (!upstream.ok || !upstream.body) return new Response("unavailable", { status: upstream.status });
  return new Response(upstream.body, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      "X-Accel-Buffering": "no",
    },
  });
}
