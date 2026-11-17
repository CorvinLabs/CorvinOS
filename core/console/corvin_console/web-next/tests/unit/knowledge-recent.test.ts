import { describe, expect, it } from "vitest";
import {
  dayKey, dayLabel, emptyCopy, groupByDay, relativeTime, showSummary, sortedTypes, typeLabel, type RecentItem,
} from "@/pages/corvin-knowledge/recent-model";

const NOW = new Date(2026, 10, 17, 14, 30).getTime();            // 17 Nov 2026, 14:30 local
const at = (daysAgo: number, h = 9) => new Date(2026, 10, 17 - daysAgo, h, 0).toISOString();
const item = (over: Partial<RecentItem>): RecentItem => ({
  id: "01X", label: "ADR-0001", type: "decision", title: "t", status: "proposed", changed_at: at(0), summary: "s",
  commits: 1, kind: "new", ...over,
});

describe("relativeTime", () => {
  it("steps from just now to a date", () => {
    expect(relativeTime(new Date(NOW - 20_000).toISOString(), NOW)).toBe("just now");
    expect(relativeTime(new Date(NOW - 12 * 60_000).toISOString(), NOW)).toBe("12 min ago");
    expect(relativeTime(new Date(NOW - 5 * 3_600_000).toISOString(), NOW)).toBe("5 h ago");
    expect(relativeTime(new Date(NOW - 3 * 86_400_000).toISOString(), NOW)).toBe("3 d ago");
    expect(relativeTime(new Date(2026, 9, 1, 12).toISOString(), NOW)).toBe("Oct 1, 2026");
  });
  it("never shows a negative age and never throws on a bad date", () => {
    expect(relativeTime(new Date(NOW + 5 * 60_000).toISOString(), NOW)).toBe("just now");
    expect(relativeTime("not a date", NOW)).toBe("date unknown");
  });
});

describe("day grouping", () => {
  it("labels today, yesterday and older days in the reader's local calendar", () => {
    expect(dayLabel(dayKey(at(0)), NOW)).toBe("Today");
    expect(dayLabel(dayKey(at(1)), NOW)).toBe("Yesterday");
    expect(dayLabel(dayKey(at(5)), NOW)).toBe("Thu, Nov 12");
    expect(dayLabel("2025-12-31", NOW)).toBe("Wed, Dec 31, 2025");      // another year shows the year
    expect(dayLabel("unknown", NOW)).toBe("Date unknown");
  });
  it("groups newest day first, keeps the server order inside a day and puts unknown dates last", () => {
    const items = [
      item({ id: "a", changed_at: at(0, 11) }), item({ id: "b", changed_at: at(0, 9) }),
      item({ id: "x", changed_at: "garbage" }), item({ id: "c", changed_at: at(1) }), item({ id: "d", changed_at: at(4) }),
    ];
    const g = groupByDay(items, NOW);
    expect(g.map((x) => x.label)).toEqual(["Today", "Yesterday", "Fri, Nov 13", "Date unknown"]);
    expect(g[0].items.map((i) => i.id)).toEqual(["a", "b"]);
  });
  it("returns nothing for nothing", () => expect(groupByDay([], NOW)).toEqual([]));
});

describe("types", () => {
  it("lists the types of a period, most frequent first and alphabetical on a tie", () => {
    expect(sortedTypes({ task: 1, decision: 2, concept: 1 })).toEqual([
      { type: "decision", count: 2 }, { type: "concept", count: 1 }, { type: "task", count: 1 }]);
    expect(sortedTypes({})).toEqual([]);
  });
  it("names types the reader knows and passes unknown ones through", () => {
    expect(typeLabel("decision")).toBe("Decision");
    expect(typeLabel("widget")).toBe("Widget");
    expect(typeLabel("")).toBe("Entry");
    expect(typeLabel("constructor")).toBe("Constructor");                 // a name on Object.prototype is just a type name
  });
});

describe("commit line", () => {
  it("is hidden when it only repeats the title and shown when it adds something", () => {
    expect(showSummary({ title: "Add the New view", summary: "kb: new task T-0003 — Add the New view" })).toBe(false);
    expect(showSummary({ title: "Add the New view", summary: "ADD THE NEW VIEW" })).toBe(false);
    expect(showSummary({ title: "Owner binding", summary: "docs: record the review rounds" })).toBe(true);
    expect(showSummary({ title: "x", summary: "" })).toBe(false);
    // a short title must not hide a line that carries news
    expect(showSummary({ title: "Tasks", summary: "kb: rename the Tasks board columns" })).toBe(true);
    expect(showSummary({ title: "Tasks", summary: "kb: new epic E-004 — Tasks" })).toBe(false);
    // small words do not make a near-copy of the title news
    expect(showSummary({ title: "Corvin as a built-in project", summary: "concept: CONCEPT-0129 - Corvin itself as a built-in project" })).toBe(false);
    // a subject cut off at 140 characters ends mid-word: it is still only the title
    expect(showSummary({ title: "Seal pending link requests to the owner key", summary: "kb: new task T-0407 — Seal pending link requests to the ow" })).toBe(false);
  });
});

describe("empty copy", () => {
  it("names each state and never claims there is no history when it simply was not read", () => {
    expect(emptyCopy("missing", 14, false).title).toMatch(/not been indexed/);
    expect(emptyCopy("no_history", 14, false).title).toMatch(/No change history/);
    expect(emptyCopy("no_commits", 14, false).title).toBe("No commits yet");
    expect(emptyCopy("error", 14, false).title).toMatch(/could not be read/);
    expect(emptyCopy("error", 14, false).hint).not.toMatch(/in a moment/);          // a refused repository is not a passing hiccup
    expect(emptyCopy("empty", 30, false).title).toBe("Nothing new in the last 30 days");
    expect(emptyCopy("ok", 14, true).title).toBe("Nothing matches these filters");
  });
});
