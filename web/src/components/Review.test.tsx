import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ReviewOut } from "@/lib/api/types";
import { pendingFlags, reasonProblem } from "@/lib/review";
import { AuditTrail, FlagReview, ReportDecision } from "./Review";

const refresh = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh }) }));

const fetchMock = vi.fn();
beforeEach(() => {
  refresh.mockReset();
  fetchMock.mockReset().mockResolvedValue(new Response(JSON.stringify({}), { status: 201 }));
  vi.stubGlobal("fetch", fetchMock);
});

function review(target: string, decision: string, extra: Partial<ReviewOut> = {}): ReviewOut {
  return {
    id: 1, target, decision, note: null, reviewer: "github:analyst", created_at: "2026-10-02T09:30:00Z",
    rules_version: "abc1234", model_id: null, ...extra,
  };
}

describe("review rules", () => {
  it("require a reason only to dismiss or reject", () => {
    expect(reasonProblem("confirm", "")).toBeNull();
    expect(reasonProblem("dismiss", "too short")).toMatch(/reason/);
    expect(reasonProblem("reject", "Director is on a sanctions list")).toBeNull();
  });

  it("list flags without a decision", () => {
    expect(pendingFlags(["F1", "F2"], { F2: {} })).toEqual(["F1"]);
  });
});

describe("FlagReview", () => {
  it("won't dismiss without a reason, and says why", async () => {
    render(<FlagReview reportId={7} flagId="F1" />);
    await userEvent.click(screen.getByRole("button", { name: "Dismiss flag" }));
    await userEvent.click(screen.getByRole("button", { name: "Save: dismiss F1" }));

    expect(screen.getByRole("alert")).toHaveTextContent("Give a reason");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("posts the decision with its reason, then refreshes", async () => {
    render(<FlagReview reportId={7} flagId="F1" />);
    await userEvent.click(screen.getByRole("button", { name: "Dismiss flag" }));
    await userEvent.type(screen.getByLabelText(/Why is this flag not a concern/), "Explained by a one-off dividend");
    await userEvent.click(screen.getByRole("button", { name: "Save: dismiss F1" }));

    expect(fetchMock).toHaveBeenCalledWith("/api/reports/7/reviews", expect.objectContaining({ method: "POST" }));
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
      flag_id: "F1", decision: "dismiss", note: "Explained by a one-off dividend",
    });
    expect(refresh).toHaveBeenCalled();
  });

  it("shows the API's error if it refuses", async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ error: "report 7 is failed" }), { status: 409 }));
    render(<FlagReview reportId={7} flagId="F1" />);
    await userEvent.click(screen.getByRole("button", { name: "Confirm flag" }));
    await userEvent.click(screen.getByRole("button", { name: "Save: confirm F1" }));
    expect(screen.getByRole("alert")).toHaveTextContent("report 7 is failed");
  });

  it("shows the current decision", () => {
    render(<FlagReview reportId={7} flagId="F1" current={review("F1", "confirm", { note: "Checked" })} />);
    expect(screen.getByText(/Confirmed by github:analyst/)).toHaveTextContent("“Checked”");
  });
});

describe("ReportDecision", () => {
  it("can't approve while flags are unreviewed", () => {
    render(<ReportDecision reportId={7} flagIds={["F1", "F2"]} decisions={{ F2: review("F2", "confirm") }} />);
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled();
    expect(screen.getByText(/Pending: F1/)).toBeInTheDocument();
  });

  it("approves once every flag is reviewed", async () => {
    render(<ReportDecision reportId={7} flagIds={["F1"]} decisions={{ F1: review("F1", "dismiss") }} />);
    await userEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toMatchObject({ decision: "approve" });
  });

  it("rejection needs a reason", async () => {
    render(<ReportDecision reportId={7} flagIds={[]} decisions={{}} />);
    await userEvent.click(screen.getByRole("button", { name: "Reject…" }));
    await userEvent.click(screen.getByRole("button", { name: "Confirm rejection" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Give a reason");
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("AuditTrail", () => {
  it("lists every decision with who, what and the rules version", () => {
    render(
      <AuditTrail
        reviews={[
          review("F1", "confirm"),
          { ...review("F1", "dismiss", { note: "Explained by a dividend" }), id: 2 },
          { ...review("report", "approve", { reviewer: "github:lead" }), id: 3 },
        ]}
      />,
    );
    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(3);
    expect(items[1]).toHaveTextContent("dismissed F1");
    expect(items[1]).toHaveTextContent("“Explained by a dividend”");
    expect(items[2]).toHaveTextContent("github:lead");
    expect(items[2]).toHaveTextContent("approved the report");
    expect(items[0]).toHaveTextContent("rules abc1234");
  });
});
