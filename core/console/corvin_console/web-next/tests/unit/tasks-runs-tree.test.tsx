/** RunsTree follows the data: a group opens when it gets an active run, and
 *  only what the operator toggled is remembered (ADR-2081 P4, review round 3). */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import type { UnifiedTask } from "@/lib/api/initiatives";
import { RunsTree } from "@/pages/tasks/run-views";

afterEach(() => cleanup());

const NOW = Date.parse("2026-09-27T12:00:00Z");
function r(id: string, over: Partial<UnifiedTask> = {}): UnifiedTask {
  return { id, type: "chat", type_label: "Chat", subtype: "discord", title: id, status: "done", raw_status: null,
    created_at: "2026-09-27T11:00:00Z", started_at: "2026-09-27T11:00:00Z", ended_at: "2026-09-27T11:30:00Z",
    sort_ts: 0, duration_s: 1, stale_reason: null, detail: null, ...over };
}
const noop = () => {};

describe("RunsTree", () => {
  it("opens a group once it has an active run, even after the first render", () => {
    const { rerender } = render(<RunsTree runs={[r("old")]} now={NOW} onLink={noop} />);
    const group = () => screen.getByTestId("run-group-chat:discord");
    expect((group().querySelector("button[aria-expanded]") as HTMLButtonElement).getAttribute("aria-expanded")).toBe("false");
    rerender(<RunsTree runs={[r("live", { status: "running", ended_at: null }), r("old")]} now={NOW} onLink={noop} />);
    expect((group().querySelector("button[aria-expanded]") as HTMLButtonElement).getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText("live")).toBeTruthy();
  });

  it("keeps a group the operator closed closed", () => {
    const live = [r("live", { status: "running", ended_at: null })];
    const { rerender } = render(<RunsTree runs={live} now={NOW} onLink={noop} />);
    const btn = () => screen.getByTestId("run-group-chat:discord").querySelector("button[aria-expanded]") as HTMLButtonElement;
    fireEvent.click(btn());
    expect(btn().getAttribute("aria-expanded")).toBe("false");
    rerender(<RunsTree runs={[...live, r("live2", { status: "running", ended_at: null })]} now={NOW} onLink={noop} />);
    expect(btn().getAttribute("aria-expanded")).toBe("false");
  });
});
