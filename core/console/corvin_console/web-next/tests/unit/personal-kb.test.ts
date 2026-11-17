import { describe, expect, it } from "vitest";
import { openIssuesLabel, STAGE_LABELS } from "@/lib/api/personal-kb";
import * as pk from "@/lib/api/personal-kb";

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

describe("splitObjections (ADR-2329: the critic's rank, not its count)", () => {
  it("makes the open objection with the best rank the main point and keeps the others quiet", () => {
    const objs = [
      { resolved: false, rank: 2, text: "b" },
      { resolved: false, rank: 1, text: "a" },
      { resolved: true, rank: 3, text: "c" },
    ];
    const { main, rest, done } = pk.splitObjections(objs);
    expect(main?.[0].text).toBe("a");
    expect(main?.[1]).toBe(1);                              // the original index, which the API needs
    expect(rest.map(([o]) => o.text)).toEqual(["b"]);
    expect(done.map(([o]) => o.text)).toEqual(["c"]);
  });
  it("falls back to the stored order for objections written before ranks existed", () => {
    const { main, rest } = pk.splitObjections([{ resolved: false, text: "first" }, { resolved: false, text: "second" }]);
    expect(main?.[0].text).toBe("first");
    expect(rest.map(([o]) => o.text)).toEqual(["second"]);
  });
  it("has no main point when everything is resolved", () => {
    expect(pk.splitObjections([{ resolved: true, rank: 1 }]).main).toBeNull();
  });
});

describe("splitObjections per step", () => {
  it("takes the main point from the step that ran last and lists earlier steps apart", () => {
    const objs = [
      { resolved: false, rank: 1, stage: "shape", text: "s1" },
      { resolved: false, rank: 1, stage: "plan", text: "p1" },
      { resolved: false, rank: 2, stage: "plan", text: "p2" },
    ];
    const r = pk.splitObjections(objs, "plan");
    expect(r.main?.[0].text).toBe("p1");
    expect(r.rest.map(([o]) => o.text)).toEqual(["p2"]);
    expect(r.earlier.map(([o]) => o.text)).toEqual(["s1"]);
  });
});
