import { describe, expect, it } from "vitest";
import { runUsageLabel, runUsageTitle } from "@/lib/run-usage";

describe("run usage badge (ADR-2308)", () => {
  it("shows the reported model and the token total", () => {
    expect(runUsageLabel({ model_id: "claude-haiku-5-5", tokens: 90085 })).toBe("claude-haiku-5-5 · 90,085 tok");
  });

  it("still shows tokens when the CLI reported no model", () => {
    expect(runUsageLabel({ tokens: 1200 })).toBe("1,200 tok");
    expect(runUsageTitle({ tokens: 1200 })).toContain("reported no model");
  });

  it("names the attempts a retried node's total covers", () => {
    expect(runUsageLabel({ model_id: "m", tokens: 10, attempts: 3 })).toBe("m · 10 tok · 3 attempts");
    expect(runUsageTitle({ model_id: "m", tokens: 10, attempts: 3 })).toContain("3 attempts");
    expect(runUsageTitle({ model_id: "m", tokens: 10, attempts: 3 })).toContain("reached the CLI");
    expect(runUsageLabel({ attempts: 2 })).toBe("2 attempts");
    expect(runUsageLabel({ model_id: "m", attempts: 1 })).toBe("m");
  });

  it("says the figure includes cache tokens", () => {
    expect(runUsageTitle({ model_id: "m", tokens: 5 })).toContain("cache read + cache write");
  });
});
