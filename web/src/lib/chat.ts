/** Helpers for the report assistant: reading its event stream and linking its citations. */

export type ChatStreamEvent =
  | { event: "conversation"; conversation_id: number }
  | { event: "text"; text: string }
  | { event: "tool"; name: string; input: Record<string, unknown> }
  | {
      event: "done";
      conversation_id: number;
      answer: string;
      citations: string[];
      unknown_citations: string[];
      cost_usd: number;
      latency_ms: number;
    }
  | { event: "error"; message: string };

/** Yields each `data:` payload from a server-sent event stream, however it is chunked. */
export async function* readEvents(body: ReadableStream<Uint8Array>): AsyncGenerator<ChatStreamEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let end: number;
    while ((end = buffer.indexOf("\n\n")) >= 0) {
      const frame = buffer.slice(0, end);
      buffer = buffer.slice(end + 2);
      const data = frame
        .split("\n")
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice(5).trimStart())
        .join("\n");
      if (data) yield JSON.parse(data) as ChatStreamEvent;
    }
  }
}

export type Segment = { text: string } | { cite: string };

/** Splits an answer into plain text and [E3] / [F1] citation markers. */
export function segments(answer: string): Segment[] {
  const out: Segment[] = [];
  let last = 0;
  for (const m of answer.matchAll(/\[([EF]\d+)\]/g)) {
    if (m.index > last) out.push({ text: answer.slice(last, m.index) });
    out.push({ cite: m[1] });
    last = m.index + m[0].length;
  }
  if (last < answer.length) out.push({ text: answer.slice(last) });
  return out;
}

const TOOL_LABEL: Record<string, string> = {
  get_flag_evidence: "Read the evidence for",
  get_check: "Looked up the check",
  get_company_snapshot: "Read the company details",
  get_review_history: "Read the review history",
};

/** "Read the evidence for F1": what the assistant looked at, in plain words. */
export function describeTool(name: string, input: Record<string, unknown>): string {
  const label = TOOL_LABEL[name] ?? name;
  const arg = input.flag_id ?? input.rule;
  return typeof arg === "string" ? `${label} ${arg.replaceAll("_", " ")}` : label;
}
