import { describe, expect, it } from "vitest";
import type { ChangedFile } from "@/lib/api/projects";
import { STATUS_META, ago, emptyMessage, groupByArea, linkTarget, parseDiff, splitPath, summaryLine, totals } from "@/pages/projects/changes-logic";

const f = (path: string, area: ChangedFile["area"], a: number | null = 1, d: number | null = 0, status: ChangedFile["status"] = "modified"): ChangedFile =>
  ({ path, area, status, additions: a, deletions: d, links: [] });

describe("grouping and totals", () => {
  it("groups code, then documents, then knowledge, and drops empty groups", () => {
    const g = groupByArea([f("docs/kb/tasks/T-1.md", "knowledge"), f("a.py", "code"), f("docs/n.md", "docs"), f("b.py", "code")]);
    expect(g.map((x) => x.area)).toEqual(["code", "docs", "knowledge"]);
    expect(g[0].files.map((x) => x.path)).toEqual(["a.py", "b.py"]);
    expect(groupByArea([f("a.py", "code")]).map((x) => x.area)).toEqual(["code"]);
    expect(groupByArea([])).toEqual([]);
  });
  it("counts what is known and never invents numbers for binaries (null counts as 0, the file still counts)", () => {
    expect(totals([f("a", "code", 3, 1), f("b", "code", null, null)])).toEqual({ files: 2, additions: 3, deletions: 1 });
    expect(summaryLine([f("a", "code", 12, 4)])).toBe("1 file, +12 -4");
    expect(summaryLine([f("a", "code"), f("b", "code")])).toBe("2 files, +2 -0");
    expect(summaryLine([])).toBe("No changes");
  });
  it("tells a status in a letter and a word, so colour is never the only signal", () => {
    for (const s of Object.values(STATUS_META)) { expect(s.letter.length).toBe(1); expect(s.label.length).toBeGreaterThan(2); }
    expect(new Set(Object.values(STATUS_META).map((s) => s.letter)).size).toBe(Object.keys(STATUS_META).length);
  });
  it("splits a path into folder and name", () => {
    expect(splitPath("a/b/c.py")).toEqual({ dir: "a/b/", name: "c.py" });
    expect(splitPath("README.md")).toEqual({ dir: "", name: "README.md" });
  });
});

describe("diff lines", () => {
  const diff = "diff --git a/x b/x\nindex 1..2 100644\n--- a/x\n+++ b/x\n@@ -1,2 +1,2 @@\n ctx\n-old\n+new\n--- not a header inside a hunk\n";
  it("types lines; header lines before the first hunk are meta even when they start with + or -", () => {
    const l = parseDiff(diff);
    expect(l.slice(0, 4).map((x) => x.kind)).toEqual(["meta", "meta", "meta", "meta"]);
    expect(l[4]).toEqual({ kind: "hunk", text: "@@ -1,2 +1,2 @@" });
    expect(l.slice(5).map((x) => x.kind)).toEqual(["ctx", "del", "add", "del"]);   // inside a hunk a leading - is a deletion
  });
  it("keeps the +/- in the text and handles an empty diff", () => {
    expect(parseDiff("@@ -1 +1 @@\n+a\n").map((x) => x.text)).toEqual(["@@ -1 +1 @@", "+a"]);
    expect(parseDiff("")).toEqual([]);
  });
});

describe("links and wording", () => {
  it("tasks go to the project's board, everything else to its graph, scoped to the project", () => {
    expect(linkTarget("plum", { id: "T-0001", kind: "task" })).toBe("/app/initiatives?scope=project:plum");
    expect(linkTarget("plum", { id: "ADR-2319", kind: "knowledge" })).toBe("/app/corvin-knowledge?scope=project:plum");
    expect(linkTarget("a b", { id: "T-0001", kind: "task" })).toContain("project:a%20b");
  });
  it("says how long ago, in plain words", () => {
    const now = 1_000_000_000_000;
    const s = now / 1000;
    expect(ago(s - 10, now)).toBe("just now");
    expect(ago(s - 300, now)).toBe("5 min ago");
    expect(ago(s - 7200, now)).toBe("2 h ago");
    expect(ago(s - 90000, now)).toBe("yesterday");
    expect(ago(s - 10 * 86400, now)).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(ago(0, now)).toBe("");
  });
  it("the empty message names the reason", () => {
    expect(emptyMessage(false, false)).toContain("not a git repository");
    expect(emptyMessage(true, true)).toContain("commit changed no files");
    expect(emptyMessage(true, false)).toContain("appear here");
  });
});
