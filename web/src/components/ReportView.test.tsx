import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { sampleReport } from "@/test/sample-report";
import { ReportView } from "./ReportView";

describe("ReportView", () => {
  it("shows the score with its band", () => {
    render(<ReportView report={sampleReport} />);
    expect(screen.getByLabelText("Risk score 45 out of 100, High")).toBeInTheDocument();
  });

  it("orders flags by severity and links every cited record", () => {
    render(<ReportView report={sampleReport} />);
    const flags = screen.getAllByRole("article");
    expect(flags.map((f) => within(f).getByRole("heading").textContent)).toEqual([
      "Net assets turned negative",
      "Frequent registered-office changes",
    ]);
    const evidence = within(flags[0]).getAllByRole("link");
    expect(evidence.map((a) => a.getAttribute("href"))).toEqual(["https://example.test/doc1", "https://example.test/doc2"]);
    expect(evidence[0]).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("lists every check, including ones not assessed", () => {
    render(<ReportView report={sampleReport} />);
    const checks = screen.getByRole("heading", { name: "Every check" }).parentElement!;
    expect(within(checks).getByText("Not assessed")).toBeInTheDocument();
    expect(within(checks).getByText("No network data.")).toBeInTheDocument();
  });

  it("renders filed figures as linked money values", () => {
    render(<ReportView report={sampleReport} />);
    expect(screen.getByRole("link", { name: "£44,235" })).toHaveAttribute("href", "https://example.test/doc1");
    expect(screen.getByRole("link", { name: "£6,855" })).toBeInTheDocument();
  });

  it("shows a reviewer's decision on a flag", () => {
    render(
      <ReportView
        report={sampleReport}
        decisions={{ F1: { id: 1, target: "F1", decision: "dismiss", note: null, reviewer: "github:lead", created_at: "2026-10-01T10:00:00Z", rules_version: "abc1234", model_id: null } }}
      />,
    );
    expect(screen.getByText("Dismissed by github:lead")).toBeInTheDocument();
  });
});
