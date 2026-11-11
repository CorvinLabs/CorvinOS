/** The Share dialogs' decisions (pages/projects/share-logic.ts): class gating, the export button, conflict rows, the import button. */
import { describe, expect, it } from "vitest";
import type { ImportCard, PartRow } from "@/lib/api/projects";
import {
  COPY_SUFFIX, TITLE_MAX, conflictRows, copyTitle, exportClassLine, exportEnabled, exportGate, fromLine, importEnabled, importProtection, includedLine, initialChoices,
  jobLine, leftOutLines, offersWithoutHistory, pruneSelected, skippableParts, suggestId, titleProblem, toggleSelected, verdictKey, willCreateLine,
} from "@/pages/projects/share-logic";

const parts = (on: Partial<Record<PartRow["name"], boolean>>): PartRow[] =>
  (["files", "kb", "git_history", "videos_index", "generated"] as const).map((name) => ({ name, included: !!(on[name] ?? name === "files") }));

describe("export class gating", () => {
  it("secret is never exported: refused with the reason, no confirmation, no passphrase", () => {
    const g = exportGate([{ id: "vault", classification: "secret" }], true);
    expect(g.refused).toBe("Secret projects cannot be exported.");
    expect(g.passphrase).toBe(false);
    expect(exportEnabled({ gate: g, typed: "vault", passphrase: "", busy: false, ready: true })).toBe(false);
  });
  it("confidential needs the typed id; the button turns on only when it matches", () => {
    const g = exportGate([{ id: "beta", classification: "confidential" }], false);
    expect(g.refused).toBeNull();
    expect(g.confirmWord).toBe("beta");
    expect(g.classLine).toBe("Data class: Confidential — This project is confidential. Type beta to confirm the export.");
    const s = { gate: g, typed: "", passphrase: "", busy: false, ready: true };
    expect(exportEnabled(s)).toBe(false);
    expect(exportEnabled({ ...s, typed: "bet" })).toBe(false);
    expect(exportEnabled({ ...s, typed: "beta" })).toBe(true);
  });
  it("internal and public export freely and say so", () => {
    expect(exportClassLine({ id: "a", classification: "internal" }, true)).toBe("Data class: Internal — can be shared.");
    expect(exportClassLine({ id: "a", classification: "public" }, true)).toBe("Data class: Public — can be shared.");
    const g = exportGate([{ id: "a", classification: "internal" }], true);
    expect(exportEnabled({ gate: g, typed: "", passphrase: "", busy: false, ready: true })).toBe(true);
  });
  it("the passphrase field exists only with gpg; without it the sentence says why", () => {
    expect(exportGate([{ id: "a" }], true)).toMatchObject({ passphrase: true, passphraseNote: null });
    expect(exportGate([{ id: "a" }], false)).toMatchObject({ passphrase: false,
      passphraseNote: "A passphrase needs gpg, which is not installed on this machine." });
  });
  it("a passphrase shorter than 8 characters keeps the button off", () => {
    const g = exportGate([{ id: "a" }], true);
    const s = { gate: g, typed: "", passphrase: "short", busy: false, ready: true };
    expect(exportEnabled(s)).toBe(false);
    expect(exportEnabled({ ...s, passphrase: "long enough" })).toBe(true);
  });
  it("a set: one secret project refuses the whole set and is named; several confidential ones need one phrase", () => {
    expect(exportGate([{ id: "a" }, { id: "s", classification: "secret" }], true).refused).toBe("Secret projects cannot be exported (s).");
    expect(exportGate([{ id: "a", classification: "confidential" }, { id: "b", classification: "confidential" }], true).confirmWord)
      .toBe("export confidential");
    expect(exportGate([{ id: "a" }, { id: "b", classification: "confidential" }], true).confirmWord).toBe("b");
  });
  it("busy or not loaded: off", () => {
    const g = exportGate([{ id: "a" }], true);
    expect(exportEnabled({ gate: g, typed: "", passphrase: "", busy: true, ready: true })).toBe(false);
    expect(exportEnabled({ gate: g, typed: "", passphrase: "", busy: false, ready: false })).toBe(false);
  });
});

describe("export preview lines", () => {
  it("included: counts, size, knowledge base and history only when included", () => {
    expect(includedLine({ counts: { files: 212, bytes: 5_347_738 }, parts: parts({ kb: true, git_history: true }), kb_entries: 41, commits: 38 }))
      .toBe("212 files · 5.1 MB · knowledge base (41 entries) · git history (38 commits)");
    expect(includedLine({ counts: { files: 1, bytes: 10 }, parts: parts({}), kb_entries: 0, commits: null })).toBe("1 file · 10 B");
  });
  it("left out names the reason in words and caps the list", () => {
    expect(leftOutLines([{ path: ".env", reason: "secret_name" }, { path: "link", reason: "link" }]))
      .toEqual([".env (secret name)", "link (link)"]);
    const many = Array.from({ length: 10 }, (_, i) => ({ path: `f${i}`, reason: "generated" }));
    expect(leftOutLines(many, 3)).toEqual(["f0 (generated)", "f1 (generated)", "f2 (generated)", "and 7 more"]);
  });
  it("offers 'Export without git history' only when the included history has findings", () => {
    const p = { id: "a", title: "A", template: "", role: "", classification: "internal" as const, docs_mode: "plain" as const, features: {},
      left_out: [], counts: { files: 1, bytes: 1 }, commits: 2, kb_entries: 0 };
    expect(offersWithoutHistory({ projects: [{ ...p, parts: parts({ git_history: true }), history_findings: 1 }], notes: [], warnings: [], gpg: false })).toBe(true);
    expect(offersWithoutHistory({ projects: [{ ...p, parts: parts({}), history_findings: 1 }], notes: [], warnings: [], gpg: false })).toBe(false);
    expect(offersWithoutHistory({ projects: [{ ...p, parts: parts({ git_history: true }), history_findings: 0 }], notes: [], warnings: [], gpg: false })).toBe(false);
  });
  it("a job: running progress, done, the server's failure sentence", () => {
    expect(jobLine({ state: "running", progress: { files: 3, bytes: 2048 } })).toBe("Building the ZIP: 3 files, 2.0 KB so far…");
    expect(jobLine({ state: "done", progress: { files: 3, bytes: 2048 } })).toBe("The ZIP is ready.");
    expect(jobLine({ state: "error", progress: { files: 0, bytes: 0 }, message: "x.txt looks like it contains a secret." }))
      .toBe("x.txt looks like it contains a secret.");
  });
});

const CARD: ImportCard = {
  id: "alpha", title: "Alpha", role: "research", classification: "public", docs_mode: "nested", template: "research@1", features: {},
  counts: { files: 12, bytes: 4096 }, parts: parts({ kb: true, git_history: true }), git: { head: "refs/heads/main" }, left_out: [],
  warnings: [], default_location: { storage: "managed", path: "/w/alpha-2" },
  conflicts: [
    { kind: "id_taken", message: 'A project "alpha" already exists in this list.', fix: { suggested_id: "alpha-2" } },
    { kind: "same_source", message: "This was exported from a project in this list. It can only be imported as a copy.", fix: { suggested_id: "alpha-2" } },
    { kind: "folder_not_empty", message: "That folder already exists and is not empty.", fix: { choose_location: true } },
    { kind: "nested", message: "It lies inside another project (beta).", fix: { choose_location: true } },
  ],
};

describe("import conflict rows", () => {
  it("each conflict gets its single fix", () => {
    expect(conflictRows(CARD.conflicts).map((r) => [r.kind, r.action, r.fixLabel, r.suggestedId])).toEqual([
      ["id_taken", "new_id", "New id", "alpha-2"],
      ["same_source", "copy", "Import as a copy", "alpha-2"],
      ["folder_not_empty", "choose_place", "Choose another place", undefined],
      ["nested", "choose_place", "Choose another place", undefined],
    ]);
  });
  it("an unknown location class still offers another place; one without a fix offers none", () => {
    expect(conflictRows([{ kind: "hint_word", message: "m", fix: { choose_location: true } }])[0].action).toBe("choose_place");
    expect(conflictRows([{ kind: "x", message: "m", fix: {} }])[0].action).toBe("none");
  });
});

describe("the Import button follows the verdict for the current choices", () => {
  const choices = { ...initialChoices(CARD), projectId: "alpha-2" };
  it("off while the verdict is loading, stale or names a conflict", () => {
    expect(importEnabled({ choices, verdict: undefined, applying: false })).toBe(false);
    expect(importEnabled({ choices, verdict: { for: verdictKey({ projectId: "alpha", location: null }), conflicts: [] }, applying: false })).toBe(false);
    expect(importEnabled({ choices, verdict: { for: verdictKey(choices), conflicts: [CARD.conflicts[2]] }, applying: false })).toBe(false);
  });
  it("on when the verdict for exactly these choices is empty; off again while applying or with an invalid id", () => {
    const v = { for: verdictKey(choices), conflicts: [] };
    expect(importEnabled({ choices, verdict: v, applying: false })).toBe(true);
    expect(importEnabled({ choices, verdict: v, applying: true })).toBe(false);
    const bad = { ...choices, projectId: "Alpha 2" };
    expect(importEnabled({ choices: bad, verdict: { for: verdictKey(bad), conflicts: [] }, applying: false })).toBe(false);
  });
  it("a chosen place is part of the key", () => {
    const placed = { ...choices, location: "/home/u/code/alpha-2" };
    expect(verdictKey(placed)).not.toBe(verdictKey(choices));
  });
});

describe("import card lines", () => {
  it("skip boxes only for parts the bundle carries", () => {
    expect(skippableParts(CARD)).toEqual({ history: true, kb: true });
    expect(skippableParts({ parts: parts({}), git: null })).toEqual({ history: false, kb: false });
  });
  it("what will be created follows the skip boxes", () => {
    expect(willCreateLine(CARD)).toBe("12 files · 4.0 KB · knowledge base · git history");
    expect(willCreateLine(CARD, { skipHistory: true, skipKb: true })).toBe("12 files · 4.0 KB");
  });
  it("from line names the file, the day and the exporter", () => {
    expect(fromLine("a.zip", { created_at: "2026-11-10T08:00:00Z", exporter: { app: "corvinos", version: "1.9" } }))
      .toBe("From a.zip · exported 2026-11-10 by CorvinOS 1.9");
  });
  it("protection is a floor: public becomes internal, confidential stays", () => {
    expect(importProtection("public")).toBe("internal");
    expect(importProtection("confidential")).toBe("confidential");
  });
});

describe("duplicate id and list selection", () => {
  it("suggests the server's next free id", () => {
    expect(suggestId("alpha", ["alpha"])).toBe("alpha-2");
    expect(suggestId("alpha", ["alpha", "alpha-2"])).toBe("alpha-3");
    expect(suggestId("a".repeat(28), ["a".repeat(28)])).toBe(`${"a".repeat(26)}-2`);
  });
  it("toggles and prunes the selection to usable own projects", () => {
    expect(toggleSelected(["a"], "b")).toEqual(["a", "b"]);
    expect(toggleSelected(["a", "b"], "a")).toEqual(["b"]);
    expect(pruneSelected(["a", "corvin", "gone"], [{ id: "a", state: "ok" }, { id: "corvin", state: "ok", builtin: true },
      { id: "gone", state: "missing" }])).toEqual(["a"]);
  });
});

// REVIEW-0071 N5: the dialog's default title is the server's default (projects_service._copy_title), never more than 120 characters
describe("the copy's default title", () => {
  it("is '<title> (copy)' for an ordinary title, the id when there is none", () => {
    expect(copyTitle("Alpha research", "alpha")).toBe("Alpha research (copy)");
    expect(copyTitle("", "alpha")).toBe("alpha (copy)");
    expect(copyTitle(undefined, "alpha")).toBe("alpha (copy)");
  });
  it("clips the base so a 114..120 character title still gives at most 120 characters, as the server does", () => {
    for (const n of [113, 114, 118, 120]) {
      const out = copyTitle("y".repeat(n), "p");
      expect(Array.from(out).length).toBeLessThanOrEqual(TITLE_MAX);
      expect(titleProblem(out)).toBeNull();
    }
    expect(copyTitle("y".repeat(118), "p")).toBe("y".repeat(113) + COPY_SUFFIX);
    expect(copyTitle("y".repeat(112) + " z", "p")).toBe("y".repeat(112) + " (copy)");     // trimmed again: no double space
  });
  it("puts an odd source title on one line, counting code points like the server", () => {
    expect(copyTitle("a\u0001b\u2028c\n d", "p")).toBe("a b c d (copy)");
    expect(copyTitle("\u0001\u0002", "orig")).toBe("orig (copy)");
    const emoji = "\u{1F600}".repeat(118);
    expect(Array.from(copyTitle(emoji, "p")).length).toBe(TITLE_MAX);
  });
  it("names the server's refusal of a title before it is sent", () => {
    expect(titleProblem("  ")).toBe("Give the copy a title.");
    expect(titleProblem("bad\u0001title")).toBe("The title must be one line, without control characters.");
    expect(titleProblem("x".repeat(121))).toBe("The title can have at most 120 characters.");
    expect(titleProblem("  fine  ")).toBeNull();
  });
});
