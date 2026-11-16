import { describe, expect, it } from "vitest";
import { openIssuesLabel, STAGE_LABELS } from "@/lib/api/personal-kb";

describe("personal space helpers", () => {
  it("states an unmeasured loss as such, never as zero", () => {
    expect(openIssuesLabel(undefined)).toBe("not measured yet");
    expect(openIssuesLabel(null)).toBe("not measured yet");
    expect(openIssuesLabel(Number.NaN)).toBe("not measured yet");
  });
  it("names no open issues only for a measured zero", () => {
    expect(openIssuesLabel(0)).toBe("no open issues");
  });
  it("shows the share of failed checks as a whole percentage", () => {
    expect(openIssuesLabel(0.2857)).toBe("29% of checks open");
  });
  it("labels every decoder stage", () => {
    expect(Object.keys(STAGE_LABELS)).toEqual(["seed", "shape", "choices", "plan", "done"]);
  });
});
