import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ArchiveButton } from "./ArchiveButton";

const refresh = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh }) }));
const fetchMock = vi.fn();

beforeEach(() => {
  refresh.mockReset();
  fetchMock.mockReset().mockResolvedValue(new Response(null, { status: 204 }));
  vi.stubGlobal("fetch", fetchMock);
});

describe("ArchiveButton", () => {
  it("asks before archiving and does nothing if cancelled", async () => {
    vi.stubGlobal("confirm", vi.fn(() => false));
    render(<ArchiveButton reportId={7} label="ARGOS LIMITED" />);
    await userEvent.click(screen.getByRole("button", { name: "Archive report on ARGOS LIMITED" }));
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("archives with POST and restores with DELETE", async () => {
    vi.stubGlobal("confirm", vi.fn(() => true));
    const { rerender } = render(<ArchiveButton reportId={7} label="ARGOS LIMITED" />);
    await userEvent.click(screen.getByRole("button", { name: /Archive/ }));
    expect(fetchMock).toHaveBeenLastCalledWith("/api/reports/7/archive", { method: "POST" });
    expect(refresh).toHaveBeenCalled();

    rerender(<ArchiveButton reportId={7} label="ARGOS LIMITED" archived />);
    await userEvent.click(screen.getByRole("button", { name: /Restore/ }));
    expect(fetchMock).toHaveBeenLastCalledWith("/api/reports/7/archive", { method: "DELETE" });
  });

  it("shows the error if the server refuses", async () => {
    vi.stubGlobal("confirm", vi.fn(() => true));
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ error: "report 7 not found" }), { status: 404 }));
    render(<ArchiveButton reportId={7} label="X" />);
    await userEvent.click(screen.getByRole("button"));
    expect((await screen.findByRole("alert")).textContent).toBe("report 7 not found");
  });
});
