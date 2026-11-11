/** REVIEW-0076 N18: a task chip in a project's changes opens the board filtered to the task's id; a cancelled KB task is shown as
 * archived, which the board hides by default - the card the query names exactly must still be found. */
import { describe, expect, it } from "vitest";
import { EMPTY_FILTERS, matches } from "@/pages/tasks/encodings";
import { linkTarget } from "@/pages/projects/changes-logic";
import type { Item } from "@/lib/api/task-tracking";

const card = (title: string, status: string) => ({ id: "kb:x", title, status, labels: [], priority: "medium", kind: "task",
  overdue: false, approval_state: "none", completed_at: null, description: null, assignee: null, owner: null, category: "kb" }) as unknown as Item;
const q = (s: string) => ({ ...EMPTY_FILTERS, q: s });

describe("an archived card the search names exactly is found", () => {
  it("the chip's query finds a cancelled task", () => {
    const url = new URL(`http://x${linkTarget("alpha", { id: "T-0003", kind: "task" })}`);
    expect(matches(card("T-0003 · Drop the old export", "archived"), q(url.searchParams.get("q")!))).toBe(true);
  });
  it("a partial query or no query still hides archived cards", () => {
    expect(matches(card("T-0003 · Drop the old export", "archived"), q("T-000"))).toBe(false);
    expect(matches(card("T-0003 · Drop the old export", "archived"), q("export"))).toBe(false);
    expect(matches(card("T-0003 · Drop the old export", "archived"), EMPTY_FILTERS)).toBe(false);
  });
  it("a status filter still decides on its own", () => {
    expect(matches(card("T-0003 · x", "archived"), { ...q("T-0003"), statuses: ["open"] })).toBe(false);
    expect(matches(card("T-0003 · x", "open"), q("t-0003"))).toBe(true);
  });
});
