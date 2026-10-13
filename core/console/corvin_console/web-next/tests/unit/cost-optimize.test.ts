import { describe, expect, it } from "vitest";
import {
  describeState,
  fromPercent,
  inputMaxPercent,
  toPercent,
  type CostOptimizeResponse,
} from "@/lib/cost-optimize";

const free = (over: Partial<CostOptimizeResponse> = {}): CostOptimizeResponse => ({
  tier: "free",
  choice: { cap_ratio: null, floor_usd: null },
  ceiling: { ratio: 0.1, floor_usd: 5 },
  effective: { ratio: 0.1, floor_usd: 5 },
  clamped: false,
  state: { capped: false, reason: "under_cap" },
  measured: {
    month: "2026-10",
    window: { unit: "utc_month", month: "2026-10" },
    ratio: 0.034,
    saved_usd: 3.4,
    counterfactual_usd: 100,
    actual_usd: 96.6,
    counted_turns: 40,
    total_turns: 42,
    default_model: "claude-sonnet-5-5",
    priced: true,
  },
  meta: { cap_ratio: { min: 0, max: 1 }, floor_usd: { min: 0, max: 1000 } },
  ...over,
});

describe("percent encoding", () => {
  it("round-trips without float noise", () => {
    expect(toPercent(0.1)).toBe(10);
    expect(toPercent(0.07)).toBe(7);
    expect(fromPercent(7)).toBe(0.07);
    expect(fromPercent(12.5)).toBe(0.125);
  });
  it("null stays null (tier default / unlimited)", () => {
    expect(toPercent(null)).toBeNull();
  });
  it("rejects what the server would reject", () => {
    for (const bad of [NaN, Infinity, -1, 100.5, 1000]) expect(fromPercent(bad)).toBeUndefined();
  });
  it("accepts the whole closed range", () => {
    expect(fromPercent(0)).toBe(0);
    expect(fromPercent(100)).toBe(1);
  });
});

describe("input limits come from the server, never from a literal", () => {
  it("Free is capped at the ceiling", () => {
    expect(inputMaxPercent(free())).toBe(10);
    expect(inputMaxPercent(free({ ceiling: { ratio: 0.25, floor_usd: 5 } }))).toBe(25);
  });
  it("Member may use the whole range", () => {
    expect(inputMaxPercent(free({ tier: "member" }))).toBe(100);
  });
});

describe("describeState", () => {
  it("names the window above the number and the reference model", () => {
    const d = describeState(free());
    expect(d.window).toBe("UTC month 2026-10");
    expect(d.reference).toContain("claude-sonnet-5-5");
    expect(d.headline).toContain("3.4 %");
  });
  it("says 'measuring' instead of inventing a number while the meter warms up", () => {
    const d = describeState(free({ measured: null, state: { capped: false, reason: "meter_warming" } }));
    expect(d.headline).toMatch(/measuring/i);
    expect(d.headline).not.toMatch(/\d %/);
  });
  it("explains a paused automation and how to continue", () => {
    const d = describeState(free({ state: { capped: true, reason: "cap_reached" } }));
    expect(d.tone).toBe("paused");
    expect(d.detail).toMatch(/pin a model/i);
  });
  it("reports 'off' when the operator switched it off", () => {
    const d = describeState(free({ effective: { ratio: 0, floor_usd: 5 }, state: { capped: true, reason: "automation_off" } }));
    expect(d.tone).toBe("off");
  });
  it("tells the operator when the stored value is clamped by the tier", () => {
    const d = describeState(free({ clamped: true, choice: { cap_ratio: 0.5, floor_usd: null } }));
    expect(d.clampNotice).toMatch(/10 %/);
    expect(d.clampNotice).toMatch(/Member/);
  });
  it("shows no clamp notice for Member", () => {
    expect(describeState(free({ tier: "member", clamped: false })).clampNotice).toBeNull();
  });
  it("never prints an ADR id", () => {
    const d = describeState(free({ state: { capped: true, reason: "cap_reached" } }));
    expect(JSON.stringify(d)).not.toMatch(/ADR-\d/);
  });
});
