import { describe, expect, it } from "vitest";
import { pickLiveSummary } from "@/lib/auto-play-task-summaries";
import type { TaskVoiceSummary } from "@/lib/api";

const s = (task_id: string, completed_at: number, text = "Recap", audio_url: string | null = null) =>
  ({ sid: "c1", task_id, title: "t", created_at: completed_at, completed_at, lang: "de", text, audio_url }) as TaskVoiceSummary;

describe("pickLiveSummary", () => {
  it("shows the newest recap that finished after the pane opened, even without audio", () => {
    const got = pickLiveSummary({
      summaries: [s("old", 50), s("a", 120), s("b", 150, "Neu")],
      sinceS: 100,
      dismissed: new Set(),
    });
    expect(got?.task_id).toBe("b");
    expect(got?.audio_url).toBeNull();
  });
  it("skips dismissed, older and empty-text recaps", () => {
    expect(pickLiveSummary({
      summaries: [s("a", 120), s("b", 150, "  "), s("c", 10)],
      sinceS: 100,
      dismissed: new Set(["c1:a"]),
    })).toBeNull();
  });
});
