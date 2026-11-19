/**
 * Shipped UI is English and carries no ADR ids (CLAUDE.md "Console is a
 * Production Surface", ADR-0763).
 *
 * What is scanned, in every `src/**\/*.ts` and `src/**\/*.tsx` file (tests and
 * `.d.ts` excluded):
 *
 *  1. JSX text - whole-file, so text that wraps over several lines is seen, and
 *     text next to an expression is seen too (`Week {n}: ADR-0206` has no `>`
 *     in front of it). Only in `.tsx`.
 *  2. Every string literal: "...", '...' and template literals. That covers
 *     `title=` / `placeholder=` / `aria-label=` attributes, strings inside
 *     `{...}` (`{busy ? "Speichern" : "Save"}`), `toast(...)` /
 *     `setError(...)` arguments, and the label tables that live in plain `.ts`
 *     modules.
 *
 * Comments are removed first, and regex literals are skipped: language
 * DETECTION regexes are code, not rendered text.
 *
 * History of what earlier versions could not see: review RC-11 (2026-11-05)
 * found "Discord Bot Aktivierung" after a sweep that reported clean; review
 * 2026-11-19 found whole German sentences without an umlaut; its refutation
 * round found that the line-based `>text<` extraction missed multi-line JSX
 * text, strings in `{...}`, `toast(...)` and every `.ts` file - and a rendered
 * ADR id it therefore never reported.
 *
 * Positive controls first: the scan resolves `src/` relative to THIS file, never
 * the cwd (run from the repo root, the earlier ad-hoc scanner saw zero files and
 * reported success). It must reach > 100 `.tsx` and > 100 `.ts` files and must
 * flag one planted sample per extraction path.
 */
import { describe, it, expect } from "vitest";
import { readdirSync, readFileSync } from "node:fs";
import { join, relative, resolve } from "node:path";

const SRC = resolve(__dirname, "../../src");

const GERMAN = new RegExp(
  "\\b(Aktivierung|Einstellungen|Speichern|Abbrechen|Schlie(?:ß|ss)en|Fehler|Bitte|Löschen|"
  + "Hinzufügen|wird|nicht|Keine|keine|geladen|Benutzer|Verbindung|Aktivieren|Deaktivieren|"
  + "erfolgreich|aktiviert|Zurück|Übersicht|verfügbar|Konfiguration|"
  // Review 2026-11-19: the two bridge setup dialogs still rendered whole German
  // sentences that contain no umlaut and none of the words above.
  + "Schritte|findest|Validiere|validiert|Berechtigungen|Autorisierung|speichern|Deine|"
  + "einsatzbereit)\\b|[äöüÄÖÜß]",
);
// Case-insensitive, hyphen / space / nothing between the letters and the number:
// `adr-0763`, `ADR 0763` and `ADR0763` are the same reference to a reader.
const ADR_ID = /\bADR[- ]?\d{3,4}\b/i;

/**
 * Deliberate exceptions: `<file relative to src>` -> exact texts, each WITH a
 * reason. An entry that no longer matches anything fails the test (no stale
 * exemptions).
 */
const EXEMPT: Record<string, Record<string, string>> = {
  "pages/voice.tsx": {
    "Türkçe (Turkish)": "a language's own name in the speech-language picker; it is not UI copy",
  },
};

function sourceFiles(dir: string): string[] {
  const out: string[] = [];
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, e.name);
    if (e.isDirectory()) {
      if (e.name !== "__tests__" && e.name !== "node_modules") out.push(...sourceFiles(p));
    } else if (/\.tsx?$/.test(e.name) && !/\.(test|spec)\.tsx?$/.test(e.name) && !e.name.endsWith(".d.ts")) {
      out.push(p);
    }
  }
  return out;
}

interface LexedString {
  text: string;
  line: number;
  /** Offsets in the source; absent for a string nested in a `${...}`. */
  start?: number;
  end?: number;
}

interface Lexed {
  /** The source with comments and regex literals blanked (newlines kept). */
  code: string;
  /** Every closed string / template literal, with the line it starts on. */
  strings: LexedString[];
}

/**
 * Characters after which a `/` starts a regex literal rather than a division.
 * `>` and `}` are NOT in the set: they also end a JSX tag / expression, where a
 * `/` is rendered text. With them in it, `<span>/ Einstellungen / Profil</span>`
 * and `<p>{a} / Fehler / {b}</p>` were blanked as regex literals and never
 * scanned. They are handled separately in `regexMayStart`.
 */
const REGEX_PRECEDERS = new Set(["(", ",", "=", ":", "[", "!", "&", "|", "?", "{", ";", "\n", "+", "-", "*", "%", "<", "~", "^"]);

const ESCAPES: Record<string, string> = { n: "\n", t: "\t", r: "\r", b: "\b", f: "\f", v: "\v", "0": "\0" };

/** `Löschen` is "Löschen" on screen: resolve escapes before matching. */
export function decodeEscapes(raw: string): string {
  return raw.replace(
    /\\(?:u\{([0-9a-fA-F]{1,6})\}|u([0-9a-fA-F]{4})|x([0-9a-fA-F]{2})|([\s\S]))/g,
    (_m, brace, u4, x2, other) => {
      const hex = brace ?? u4 ?? x2;
      if (hex !== undefined) {
        const cp = parseInt(hex, 16);
        return cp <= 0x10ffff ? String.fromCodePoint(cp) : "";
      }
      if (other === "\n") return ""; // line continuation
      return ESCAPES[other] ?? other;
    },
  );
}

const NAMED_ENTITIES: Record<string, string> = {
  auml: "ä", ouml: "ö", uuml: "ü", Auml: "Ä", Ouml: "Ö", Uuml: "Ü", szlig: "ß",
  amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: " ", shy: "", hyphen: "-", dash: "-", ndash: "-",
};

/** `L&ouml;schen` / `L&#246;schen` / `L&#xf6;schen` are "Löschen" in JSX text. */
export function decodeEntities(text: string): string {
  return text.replace(/&(?:#x([0-9a-fA-F]{1,6})|#(\d{1,7})|([A-Za-z][A-Za-z0-9]{1,15}));/g, (m, hex, dec, name) => {
    if (hex !== undefined || dec !== undefined) {
      const cp = hex !== undefined ? parseInt(hex, 16) : parseInt(dec, 10);
      return cp > 0 && cp <= 0x10ffff ? String.fromCodePoint(cp) : m;
    }
    return name in NAMED_ENTITIES ? NAMED_ENTITIES[name] : m;
  });
}

/** Index after the closing quote of the '...' / "..." starting at `i`, or -1. */
function quotedEnd(src: string, i: number): number {
  const q = src[i];
  for (let j = i + 1; j < src.length; j++) {
    const ch = src[j];
    if (ch === "\\") { j++; continue; }
    if (ch === q) return j + 1;
    if (ch === "\n") return -1;
  }
  return -1;
}

interface Template {
  /** Index after the closing backtick. */
  end: number;
  /** The static text, each `${...}` replaced by one space. */
  raw: string;
  /** Source ranges of the `${...}` expressions (without the delimiters). */
  exprs: { from: number; to: number }[];
}

/**
 * A template literal with its `${...}` expressions skipped as CODE, so a
 * backtick inside one (a nested template) does not end the outer literal.
 * Before this, `\`${a ? \`Fehler\` : ""} x\`` was cut at the inner backtick and
 * the pieces were paired wrongly for the rest of the file.
 */
function templateAt(src: string, i: number): Template | null {
  let raw = "";
  const exprs: { from: number; to: number }[] = [];
  let j = i + 1;
  const n = src.length;
  while (j < n) {
    const ch = src[j];
    if (ch === "\\") { raw += src.slice(j, j + 2); j += 2; continue; }
    if (ch === "`") return { end: j + 1, raw, exprs };
    if (ch === "$" && src[j + 1] === "{") {
      let k = j + 2;
      let depth = 1;
      while (k < n && depth > 0) {
        const e = src[k];
        if (e === '"' || e === "'") { const q = quotedEnd(src, k); k = q > 0 ? q : k + 1; continue; }
        if (e === "`") { const t = templateAt(src, k); k = t ? t.end : k + 1; continue; }
        if (e === "{") depth++;
        else if (e === "}") depth--;
        k++;
      }
      if (depth !== 0) return null;
      exprs.push({ from: j + 2, to: k - 1 });
      raw += " ";
      j = k;
      continue;
    }
    raw += ch;
    j++;
  }
  return null;
}

/**
 * A small lexer: enough of TypeScript/JSX to separate comments, regex literals
 * and string literals from the rest. It is deliberately forgiving - a quote that
 * does not close on its line (an apostrophe in JSX text) is ordinary text.
 */
export function lex(src: string): Lexed {
  let code = "";
  const strings: LexedString[] = [];
  let i = 0;
  let line = 1;
  const n = src.length;
  const blank = (from: number, to: number) => {
    for (let k = from; k < to; k++) {
      if (src[k] === "\n") { code += "\n"; line++; } else code += " ";
    }
  };
  const keep = (from: number, to: number) => {
    for (let k = from; k < to; k++) {
      if (src[k] === "\n") line++;
      code += src[k];
    }
  };
  /** The last significant character and the one before it. */
  const lastSignificant = (): [string, string] => {
    for (let k = code.length - 1; k >= 0; k--) {
      const ch = code[k];
      if (ch === "\n") return ["\n", ""];
      if (ch !== " " && ch !== "\t" && ch !== "\r") return [ch, code[k - 1] ?? ""];
    }
    return ["\n", ""];
  };
  const regexMayStart = (next: string): boolean => {
    const [last, before] = lastSignificant();
    if (REGEX_PRECEDERS.has(last)) return true;
    // `=> /re/.test(x)`: an arrow body. A bare `>` closes a JSX tag - text follows.
    if (last === ">") return before === "=";
    // After `}` (a block) a regex may start a statement; in JSX text after an
    // expression the `/` is a separator and is followed by a space.
    if (last === "}") return next !== undefined && !/\s/.test(next);
    return false;
  };
  while (i < n) {
    const c = src[i];
    const d = src[i + 1];
    // line comment (not the `//` of a URL in JSX text, which follows a colon)
    if (c === "/" && d === "/" && src[i - 1] !== ":") {
      let j = i;
      while (j < n && src[j] !== "\n") j++;
      blank(i, j);
      i = j;
      continue;
    }
    if (c === "/" && d === "*") {
      let j = i + 2;
      while (j < n && !(src[j] === "*" && src[j + 1] === "/")) j++;
      j = Math.min(n, j + 2);
      blank(i, j);
      i = j;
      continue;
    }
    // regex literal: `/` where an expression may start, closed on the same line
    if (c === "/" && d !== "/" && d !== "*" && d !== ">" && regexMayStart(d)) {
      let j = i + 1;
      let inClass = false;
      let closed = false;
      while (j < n && src[j] !== "\n") {
        const ch = src[j];
        if (ch === "\\") { j += 2; continue; }
        if (ch === "[") inClass = true;
        else if (ch === "]") inClass = false;
        else if (ch === "/" && !inClass) { closed = true; break; }
        j++;
      }
      if (closed) {
        j++;
        while (j < n && /[a-z]/.test(src[j])) j++; // flags
        blank(i, j);
        i = j;
        continue;
      }
    }
    if (c === '"' || c === "'") {
      const end = quotedEnd(src, i);
      if (end > 0) {
        strings.push({ text: decodeEscapes(src.slice(i + 1, end - 1)), line, start: i, end });
        keep(i, end);
        i = end;
        continue;
      }
    }
    if (c === "`") {
      const t = templateAt(src, i);
      if (t) {
        strings.push({ text: decodeEscapes(t.raw), line, start: i, end: t.end });
        for (const e of t.exprs) {
          const offset = line + src.slice(i, e.from).split("\n").length - 1;
          for (const inner of lex(src.slice(e.from, e.to)).strings) {
            strings.push({ text: inner.text, line: offset + inner.line - 1 });
          }
        }
        keep(i, t.end);
        i = t.end;
        continue;
      }
    }
    if (c === "\n") line++;
    code += c;
    i++;
  }
  return { code, strings };
}

/**
 * Adjacent literals joined by `+` (`"ADR-" + "0763"`) as the one text they render.
 * Only top-level literals; a chain is reported when no single member already is.
 */
export function concatenations(src: string, strings: LexedString[]): LexedString[] {
  const top = strings.filter((s) => s.start !== undefined).sort((a, b) => a.start! - b.start!);
  const out: LexedString[] = [];
  let chain: LexedString[] = [];
  const flush = () => {
    if (chain.length > 1) out.push({ text: chain.map((s) => s.text).join(""), line: chain[0].line });
    chain = [];
  };
  for (const s of top) {
    const prev = chain[chain.length - 1];
    if (prev && /^\s*\+\s*$/.test(src.slice(prev.end!, s.start!))) chain.push(s);
    else { flush(); chain = [s]; }
  }
  flush();
  return out;
}

const normalise = (t: string) => decodeEntities(t).replace(/\s+/g, " ").trim();
const flagged = (t: string) => GERMAN.test(t) || ADR_ID.test(t);

/** `line: text` for every rendered German / ADR-id text in one source file. */
export function findings(text: string, opts: { tsx?: boolean } = { tsx: true }): string[] {
  const { code, strings } = lex(text);
  const out = new Map<string, true>();
  if (opts.tsx !== false) {
    // JSX text: after a tag or an expression, up to the next tag or expression.
    const jsxText = /[>}]([^<>{}]+)(?=[<{])/g;
    let m: RegExpExecArray | null;
    while ((m = jsxText.exec(code))) {
      const t = normalise(m[1]);
      if (t && flagged(t)) {
        const line = code.slice(0, m.index + 1 + m[1].search(/\S/)).split("\n").length;
        out.set(`${line}: ${t.slice(0, 100)}`, true);
      }
    }
  }
  for (const s of strings) {
    const t = normalise(s.text);
    if (t && flagged(t)) out.set(`${s.line}: ${t.slice(0, 100)}`, true);
  }
  for (const s of concatenations(text, strings)) {
    const t = normalise(s.text);
    if (!t || !flagged(t)) continue;
    // Reported only when the joined text adds something: a member that is
    // flagged on its own already carries the finding.
    const members = strings.filter((x) => x.start !== undefined && x.line >= s.line && t.includes(normalise(x.text)) && flagged(normalise(x.text)));
    if (members.length === 0) out.set(`${s.line}: ${t.slice(0, 100)}`, true);
  }
  return [...out.keys()];
}

describe("rendered console text is English and carries no ADR id", () => {
  const files = sourceFiles(SRC);

  it("positive control: the scan reaches the source tree", () => {
    expect(files.filter((f) => f.endsWith(".tsx")).length).toBeGreaterThan(100);
    expect(files.filter((f) => f.endsWith(".ts")).length).toBeGreaterThan(100);
  });

  it("positive control: every extraction path flags a planted sample", () => {
    // the samples of the earlier, line-based version
    expect(findings('<h2 className="x">🤖 Discord Bot Aktivierung</h2>')).toHaveLength(1);
    expect(findings('<p title="see ADR-0763">ok</p>')).toHaveLength(1);
    expect(findings("<p>Nur 2 Schritte bis der Bot einsatzbereit ist</p>")).toHaveLength(1);
    expect(findings("<span>Validiere Token...</span>")).toHaveLength(1);
    expect(findings("<p>Bot activated successfully!</p>")).toHaveLength(0);
    // multi-line JSX text
    expect(findings("<p>\n  Der Bot ist\n  einsatzbereit\n</p>")).toEqual(["2: Der Bot ist einsatzbereit"]);
    // text next to an expression (no '>' in front of it)
    expect(findings("<CardDescription>Week {report.week}: ADR-0206, 0205</CardDescription>"))
      .toEqual(["1: : ADR-0206, 0205"]);
    // a string inside {...}
    expect(findings('<Button>{busy ? "Speichern" : "Save"}</Button>')).toEqual(["1: Speichern"]);
    // a template literal
    expect(findings("const m = `Fehler beim Laden von ${name}`;")).toHaveLength(1);
    // toast(...) / setError(...)
    expect(findings('toast.error("Verbindung fehlgeschlagen");')).toHaveLength(1);
    expect(findings("setError('Bitte Token eingeben');")).toHaveLength(1);
    // a label table in a plain .ts module
    expect(findings('export const LABELS = { save: "Speichern" };', { tsx: false })).toHaveLength(1);
    expect(findings('export const HINT = "decided in ADR-2102";', { tsx: false })).toHaveLength(1);
  });

  it("positive control: what the third review round found unscanned", () => {
    // a `/` after a closing tag or an expression is text, not a regex literal
    expect(findings("<span>/ Einstellungen / Profil</span>")).toEqual(["1: / Einstellungen / Profil"]);
    expect(findings("<p>{a} / Fehler / {b}</p>")).toEqual(["1: / Fehler /"]);
    expect(findings("<span>/Einstellungen/</span>")).toHaveLength(1);
    // a nested template literal no longer cuts the outer one
    expect(findings('const m = `${ok ? `Fehler` : ""} beim Laden`;\nconst later = "Speichern";'))
      .toEqual(["1: Fehler", "2: Speichern"]);
    expect(findings("const m = `a ${`b ${`Bitte`}`} c`;")).toEqual(["1: Bitte"]);
    expect(findings("const m = `x ${fn({ a: `}` })} Einstellungen`;")).toHaveLength(1);
    // escapes and entities are resolved before matching
    expect(findings('const l = "L\\u00f6schen";')).toEqual(["1: Löschen"]);
    expect(findings('const l = "Schlie\\xdfen";')).toHaveLength(1);
    expect(findings("const l = 'Zur\\u{fc}ck';")).toHaveLength(1);
    expect(findings("<button>L&ouml;schen</button>")).toEqual(["1: Löschen"]);
    expect(findings("<button>L&#246;schen</button>")).toHaveLength(1);
    expect(findings("<button>L&#xF6;schen</button>")).toHaveLength(1);
    expect(findings('<p title="Hinzuf&uuml;gen">x</p>')).toHaveLength(1);
    // ADR ids in every spelling
    expect(findings('<p title="see adr-0763">ok</p>')).toHaveLength(1);
    expect(findings("<p>see ADR 0763</p>")).toHaveLength(1);
    expect(findings("<p>see Adr0763</p>")).toHaveLength(1);
    expect(findings('const hint = "ADR-" + "0763";', { tsx: false })).toEqual(["1: ADR-0763"]);
    expect(findings('const hint = "see ADR" +\n  "-0763 for why";', { tsx: false })).toHaveLength(1);
    expect(findings('const s = "Ein" + "stellungen";', { tsx: false })).toEqual(["1: Einstellungen"]);
    // a flagged member is reported once, not again as part of its chain
    expect(findings('const s = "Fehler: " + detail + "x";', { tsx: false })).toEqual(["1: Fehler:"]);
    expect(findings('const s = "Fehler" + " beim Laden";', { tsx: false })).toEqual(["1: Fehler"]);
  });

  it("negative control: the new rules do not flag English or code", () => {
    expect(findings('const s = "Save" + " changes";', { tsx: false })).toHaveLength(0);
    expect(findings('const s = "padr" + "0763";', { tsx: false })).toHaveLength(0);
    expect(findings("<p>Tom &amp; Jerry &lt;3 &#8212; fine</p>")).toHaveLength(0);
    expect(findings('const s = "line one\\nicht";', { tsx: false })).toHaveLength(0);
    expect(findings("const ok = items.filter((x) => /nicht|keine/.test(x));")).toHaveLength(0);
    expect(findings("if (a) { b(); }\n/Fehler/.test(s);")).toHaveLength(0);
    expect(findings("const m = `${a} / ${b}`;")).toHaveLength(0);
    expect(findings("<p>{used} / {total}</p>")).toHaveLength(0);
    expect(findings('const u = "quadrant-1234";', { tsx: false })).toHaveLength(0);
  });

  it("negative control: comments and detection regexes are not rendered text", () => {
    expect(findings("// Fehler: siehe ADR-0763\nconst a = 1;")).toHaveLength(0);
    expect(findings("/* Einstellungen\n * ADR-0763 */\nconst a = 1;")).toHaveLength(0);
    expect(findings("{/* ADR-0763: Speichern */}\n<p>Save</p>")).toHaveLength(0);
    expect(findings("const isGerman = /\\b(nicht|keine|wird)\\b|[äöüß]/i.test(text);")).toHaveLength(0);
    expect(findings("const s = text.replace(/[`*_~>#]+/g, '').replace(/\\s+/g, ' ');")).toHaveLength(0);
    expect(findings("<p>It's fine, don't worry</p>")).toHaveLength(0);
    expect(findings('<a href="https://example.com/x">link</a>')).toHaveLength(0);
    expect(findings("const ratio = a / b; const other = c / d;")).toHaveLength(0);
  });

  it("no source file renders German text or an ADR id", () => {
    const offenders: string[] = [];
    const usedExemptions = new Set<string>();
    for (const f of files) {
      const rel = relative(SRC, f).split("\\").join("/");
      const exempt = EXEMPT[rel] ?? {};
      for (const hit of findings(readFileSync(f, "utf8"), { tsx: f.endsWith(".tsx") })) {
        const text = hit.slice(hit.indexOf(": ") + 2);
        if (text in exempt) {
          usedExemptions.add(`${rel}::${text}`);
          continue;
        }
        offenders.push(`${rel}:${hit}`);
      }
    }
    expect(offenders).toEqual([]);
    const declared = Object.entries(EXEMPT).flatMap(([file, texts]) =>
      Object.keys(texts).map((t) => `${file}::${t}`));
    expect(declared.filter((d) => !usedExemptions.has(d))).toEqual([]);
  });
});
