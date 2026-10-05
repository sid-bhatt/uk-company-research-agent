import { describe, expect, it } from "vitest";

import { money, normaliseCompanyNumber, scoreBand } from "./format";

describe("normaliseCompanyNumber", () => {
  it.each([
    ["00502851", "00502851"],
    ["502851", "00502851"],
    [" sc123456 ", "SC123456"],
    ["NI676991", "NI676991"],
  ])("accepts %s", (input, expected) => expect(normaliseCompanyNumber(input)).toBe(expected));

  it.each(["", "greggs", "123456789", "../etc", "ABCDEFGH"])("rejects %s", (input) =>
    expect(normaliseCompanyNumber(input)).toBeNull(),
  );
});

describe("money", () => {
  it("formats pounds, including negatives", () => {
    expect(money("44235.00")).toBe("£44,235");
    expect(money("-12000")).toBe("-£12,000");
  });
});

describe("scoreBand", () => {
  it("bands the score", () => {
    expect(scoreBand(0).tone).toBe("low");
    expect(scoreBand(15).tone).toBe("medium");
    expect(scoreBand(60).tone).toBe("high");
  });
});
