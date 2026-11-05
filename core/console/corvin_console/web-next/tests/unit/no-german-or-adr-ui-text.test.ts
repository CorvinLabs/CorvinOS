/**
 * Shipped UI is English and carries no ADR ids (CLAUDE.md "Console is a
 * Production Surface", ADR-0763).
 *
 * Scans the rendered text of every `src/**\/*.tsx` file: JSX text between tags
 * and the `title` / `placeholder` / `aria-label` / `label` string attributes.
 * Comments are skipped, and so are language-DETECTION regexes (they are code, not
 * rendered text). Review RC-11 (2026-11-05) found "Discord Bot Aktivierung" /
 * "Telegram Bot Aktivierung" left after a sweep that reported clean.
 *
 * Positive control first: the scan resolves `src/` relative to THIS file, never
 * the cwd (run from the repo root, the earlier ad-hoc scanner saw zero files and
 * reported success). It must reach > 100 files and must flag a planted sample.
 */
import { describe, it, expect } from "vitest";
import { readdirSync, readFileSync } from "node:fs";
import { join, relative, resolve } from "node:path";

const SRC = resolve(__dirname, "../../src");

const GERMAN = new RegExp(
  "\\b(Aktivierung|Einstellungen|Speichern|Abbrechen|Schlie(?:ß|ss)en|Fehler|Bitte|Löschen|"
  + "Hinzufügen|wird|nicht|Keine|keine|geladen|Benutzer|Verbindung|Aktivieren|Deaktivieren|"
  + "erfolgreich|aktiviert|Zurück|Übersicht|verfügbar|Konfiguration)\\b|[äöüÄÖÜß]",
);
const ADR_ID = /\bADR-\d{3,4}\b/;

function tsxFiles(dir: string): string[] {
  const out: string[] = [];
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, e.name);
    if (e.isDirectory()) {
      if (e.name !== "__tests__" && e.name !== "node_modules") out.push(...tsxFiles(p));
    } else if (e.name.endsWith(".tsx")) {
      out.push(p);
    }
  }
  return out;
}

/** Rendered strings on one source line. */
export function renderedTexts(line: string): string[] {
  const t = line.trim();
  if (t.startsWith("//") || t.startsWith("*") || t.startsWith("/*")) return [];
  const texts: string[] = [];
  let m: RegExpExecArray | null;
  const jsxText = />([^<>{}]+)</g;
  while ((m = jsxText.exec(line))) texts.push(m[1]);
  const attr = /\b(?:title|placeholder|aria-label|label)=["']([^"']+)["']/g;
  while ((m = attr.exec(line))) texts.push(m[1]);
  return texts.map((x) => x.trim()).filter(Boolean);
}

function findings(text: string): string[] {
  const out: string[] = [];
  text.split("\n").forEach((ln, i) => {
    for (const x of renderedTexts(ln)) {
      if (GERMAN.test(x) || ADR_ID.test(x)) out.push(`${i + 1}: ${x.slice(0, 100)}`);
    }
  });
  return out;
}

describe("rendered console text is English and carries no ADR id", () => {
  const files = tsxFiles(SRC);

  it("positive control: the scan reaches the source tree and flags a planted sample", () => {
    expect(files.length).toBeGreaterThan(100);
    expect(findings('<h2 className="x">🤖 Discord Bot Aktivierung</h2>')).toHaveLength(1);
    expect(findings('<p title="see ADR-0763">ok</p>')).toHaveLength(1);
    expect(findings("<p>Bot activated successfully!</p>")).toHaveLength(0);
  });

  it("no .tsx file renders German text or an ADR id", () => {
    const offenders: string[] = [];
    for (const f of files) {
      for (const hit of findings(readFileSync(f, "utf8"))) {
        offenders.push(`${relative(SRC, f)}:${hit}`);
      }
    }
    expect(offenders).toEqual([]);
  });
});
