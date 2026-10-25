import { describe, expect, it } from "vitest";
import { adviceText, type Advice } from "@/components/video-learning-overview";

const base = { applied: false, min_samples: 5, margin: 0.5 } as const;

describe("adviceText", () => {
  it("names the best variant, the runner-up and says the advice is not applied", () => {
    const a: Advice = { ...base, reason: "ok", advice: { variant: "openai", mean_rating: 4.5, over: "edge", over_mean_rating: 3 } };
    const text = adviceText(a);
    expect(text).toMatch(/openai rated best \(mean 4\.5 of 5\) against edge \(3\.0\)/);
    expect(text).toMatch(/Advice only/);
  });

  it("says why there is no advice instead of guessing", () => {
    expect(adviceText({ ...base, reason: "insufficient_data", advice: null })).toMatch(/at least 5 rated scenes/);
    expect(adviceText({ ...base, reason: "no_clear_difference", advice: null })).toMatch(/within 0\.5 points/);
  });
});
