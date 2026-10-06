import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { EvidenceItem, ReportFlag } from "@/lib/api/types";
import { describeTool, readEvents, segments } from "@/lib/chat";
import { ReportChat } from "./ReportChat";

const fetchMock = vi.fn();
beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

/** A streamed response, split mid-frame to prove the parser buffers correctly. */
function sse(events: object[]): Response {
  const text = events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join("");
  const chunks = [text.slice(0, 17), text.slice(17, 60), text.slice(60)];
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      for (const chunk of chunks) c.enqueue(new TextEncoder().encode(chunk));
      c.close();
    },
  });
  return new Response(body, { headers: { "Content-Type": "text/event-stream" } });
}

const evidence = [
  { id: "E1", label: "Accounts to 2025-04-05", citation: { url: "https://find-and-update.company-information.service.gov.uk/x" } },
] as unknown as EvidenceItem[];
const flags = [{ id: "F1" }] as unknown as ReportFlag[];

describe("chat helpers", () => {
  it("split answers into text and citations", () => {
    expect(segments("Fell [E1], see [F1].")).toEqual([{ text: "Fell " }, { cite: "E1" }, { text: ", see " }, { cite: "F1" }, { text: "." }]);
  });

  it("describe tool calls in plain words", () => {
    expect(describeTool("get_check", { rule: "director_churn" })).toBe("Looked up the check director churn");
    expect(describeTool("get_review_history", {})).toBe("Read the review history");
  });

  it("parse events however the stream is chunked", async () => {
    const events = [{ event: "text", text: "a" }, { event: "text", text: "b" }];
    const out = [];
    for await (const e of readEvents(sse(events).body!)) out.push(e);
    expect(out).toEqual(events);
  });
});

describe("ReportChat", () => {
  it("streams an answer with linked citations and shows what it looked at", async () => {
    fetchMock.mockResolvedValue(
      sse([
        { event: "conversation", conversation_id: 4 },
        { event: "tool", name: "get_flag_evidence", input: { flag_id: "F1" } },
        { event: "text", text: "Net assets fell [E1]" },
        { event: "text", text: " (see [F1]); also [E9]." },
        { event: "done", conversation_id: 4, answer: "", citations: ["E1", "F1"], unknown_citations: ["E9"], cost_usd: 0.03, latency_ms: 900 },
      ]),
    );
    render(<ReportChat reportId={7} flags={flags} evidence={evidence} />);
    await userEvent.click(screen.getByRole("button", { name: "Why is F1 flagged?" }));

    expect(await screen.findByText("↳ Read the evidence for F1")).toBeTruthy();
    expect(screen.getByRole("link", { name: "E1" }).getAttribute("href")).toContain("company-information");
    expect(screen.getByRole("link", { name: "F1" }).getAttribute("href")).toBe("#F1");
    expect(screen.getByRole("alert").textContent).toMatch(/cites E9, which isn't in the report/);
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ question: "Why is F1 flagged?", conversation_id: null });

    // A follow-up continues the same conversation.
    fetchMock.mockResolvedValue(sse([{ event: "conversation", conversation_id: 4 }, { event: "done", conversation_id: 4, answer: "", citations: [], unknown_citations: [], cost_usd: 0, latency_ms: 1 }]));
    await userEvent.type(screen.getByLabelText("Question"), "And F2?{Enter}");
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({ question: "And F2?", conversation_id: 4 });
  });

  it("says plainly when the assistant is switched off", async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ error: "chat is not configured" }), { status: 503 }));
    render(<ReportChat reportId={7} flags={flags} evidence={evidence} />);
    await userEvent.click(screen.getByRole("button", { name: "Why is F1 flagged?" }));
    expect((await screen.findByRole("status")).textContent).toMatch(/switched off/);
    expect(screen.queryByLabelText("Question")).toBeNull();
  });

  it("shows a stream error without losing the question", async () => {
    fetchMock.mockResolvedValue(sse([{ event: "conversation", conversation_id: 1 }, { event: "error", message: "The assistant declined to answer that." }]));
    render(<ReportChat reportId={7} flags={flags} evidence={evidence} />);
    await userEvent.click(screen.getByRole("button", { name: "Why is F1 flagged?" }));
    expect((await screen.findByRole("alert")).textContent).toBe("The assistant declined to answer that.");
    expect(screen.getByText("Why is F1 flagged?")).toBeTruthy();
  });
});
