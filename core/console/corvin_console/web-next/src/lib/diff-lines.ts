/**
 * ADR-2241: classify the lines of a tool card's diff. The backend sends the
 * hunks Claude Code computed (CLI `structuredPatch`): every line carries exactly
 * one prefix — "@@" hunk header, "+" added, "-" removed, " " context — so the
 * first character decides; no file headers ("---"/"+++") ever occur.
 */
export type DiffLineKind = "hunk" | "add" | "del" | "ctx";

export interface DiffLine {
  kind: DiffLineKind;
  text: string;
}

export function classifyDiffLines(diff: string): DiffLine[] {
  return diff.split("\n").map((text) => ({
    text,
    kind: text.startsWith("@@") ? "hunk" : text.startsWith("+") ? "add" : text.startsWith("-") ? "del" : "ctx",
  }));
}

export function diffCounts(lines: DiffLine[]): { added: number; removed: number } {
  let added = 0;
  let removed = 0;
  for (const l of lines) {
    if (l.kind === "add") added++;
    else if (l.kind === "del") removed++;
  }
  return { added, removed };
}

export const DIFF_WITHHELD_TEXT: Record<string, string> = {
  credential: "Diff hidden: the change contains credential-shaped content.",
  scan_failed: "Diff hidden: the content scan could not complete.",
  turn_budget: "Diff hidden: this answer already shows the maximum amount of changes.",
};
