import { describe, expect, it } from "vitest";
import { ApiError } from "@/lib/api/client";
import { BASE } from "@/lib/api/client";
import {
  ratingStatus,
  recentDecisionsPath,
  SKILL_FEEDBACK_PATH,
} from "@/components/learning-loop-rating";
import { describeAnomaly } from "@/components/learning-anomalies";

describe("ratingStatus", () => {
  it("names the gate's reason on a 422", () => {
    const err = new ApiError(422, { detail: { error: "feedback_rejected", reason: "no_execution_in_window" } });
    expect(ratingStatus(null, err)).toBe("Refused by the feedback gate: no_execution_in_window");
  });
  it("reports other failures without inventing success", () => {
    expect(ratingStatus(null, new ApiError(500, "x"))).toBe("Not recorded (HTTP 500)");
  });
  it("says whether the learned config moved", () => {
    expect(ratingStatus({ config_applied: true, trust_weight: 1 }, null)).toBe("Recorded — learned config updated");
    expect(ratingStatus({ config_applied: false, trust_weight: 1 }, null)).toBe("Recorded");
  });
});

describe("feedback paths", () => {
  // api() prepends BASE; a path that repeats it resolves to
  // /v1/console/v1/console/feedback/skill and every rating 404s.
  it("are relative to the client's BASE and resolve to the served routes", () => {
    expect(SKILL_FEEDBACK_PATH.startsWith(BASE)).toBe(false);
    expect(recentDecisionsPath("os.flow_guard").startsWith(BASE)).toBe(false);
    expect(`${BASE}${SKILL_FEEDBACK_PATH}`).toBe("/v1/console/feedback/skill");
    expect(`${BASE}${recentDecisionsPath("os.flow_guard")}`).toBe(
      "/v1/console/feedback/skill/recent?skill_id=os.flow_guard&limit=5",
    );
  });
  it("encode the skill id", () => {
    expect(recentDecisionsPath("a b&c")).toBe("/feedback/skill/recent?skill_id=a%20b%26c&limit=5");
  });
});

describe("describeAnomaly", () => {
  it("shows the measured numbers behind each anomaly", () => {
    expect(describeAnomaly({ event: "skill.high_rejection_rate", skill_id: "s", since: "",
      rejection_count: 12, sample_count: 12 })).toBe("12 of 12 ratings refused in the last 24 h");
    expect(describeAnomaly({ event: "skill.config_oscillation", skill_id: "s", since: "", change_count: 4 }))
      .toBe("4 config versions in the last hour");
    expect(describeAnomaly({ event: "skill.confidence_regression", skill_id: "s", since: "",
      confidence_before: 0.9, confidence_after: 0.79 }))
      .toBe("mean confidence 0.90 → 0.79 (last 24 h vs the 6 days before)");
  });
});
