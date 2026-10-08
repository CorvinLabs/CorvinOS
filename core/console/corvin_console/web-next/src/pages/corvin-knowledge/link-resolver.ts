/**
 * Wiki-style link resolution for the Knowledge Graph reader (ADR-2237).
 *
 * The corpus references documents mostly by bare id ("ADR-2206": ~18k occurrences versus
 * ~80 explicit links), so ids in running text become internal links. A reference is turned
 * into a link ONLY if the id resolves to exactly one node of the loaded graph — an unknown
 * or ambiguous id stays plain text, so the reader never shows a dead link.
 */

/** Prefix of every internal href; the reader intercepts it and navigates in place. */
export const NODE_HREF_PREFIX = "?node=";

export interface LinkTarget { id: string; label?: string; file?: string }

export interface LinkIndex {
  /** human id (ADR-2206, T-0035 ...) -> node key; absent when two nodes carry the same id */
  byLabel: Map<string, string>;
  /** node key (uid or id) -> itself, so a key typed in a wikilink resolves too */
  byKey: Map<string, string>;
  /** file basename -> node key; absent when two nodes share a basename */
  byFile: Map<string, string>;
}

export function buildLinkIndex(entities: LinkTarget[]): LinkIndex {
  const byLabel = new Map<string, string>();
  const byFile = new Map<string, string>();
  const byKey = new Map<string, string>();
  const ambiguousLabel = new Set<string>();
  const ambiguousFile = new Set<string>();
  for (const e of entities) {
    byKey.set(e.id, e.id);
    const label = e.label || e.id;
    if (byLabel.has(label) && byLabel.get(label) !== e.id) ambiguousLabel.add(label);
    byLabel.set(label, e.id);
    if (e.file) {
      if (byFile.has(e.file) && byFile.get(e.file) !== e.id) ambiguousFile.add(e.file);
      byFile.set(e.file, e.id);
    }
  }
  ambiguousLabel.forEach((l) => byLabel.delete(l));
  ambiguousFile.forEach((f) => byFile.delete(f));
  return { byLabel, byKey, byFile };
}

export function nodeHref(key: string): string {
  return `${NODE_HREF_PREFIX}${encodeURIComponent(key)}`;
}

/** The node key an internal href points at, or null for any other href. */
export function parseNodeHref(href: string): string | null {
  if (!href.startsWith(NODE_HREF_PREFIX)) return null;
  try {
    return decodeURIComponent(href.slice(NODE_HREF_PREFIX.length));
  } catch {
    return null;
  }
}

const ID_RE = /(?<![\w-])(?:(?:ADR|CONCEPT|PLAN|IDEA|REVIEW|NOTE)-\d{4}|T-\d{4}|E-\d{3}|I-\d{2})(?![\w-])/g;
// Spans that must not be touched: inline code, existing links/images, autolinks, bare URLs.
const PROTECTED_RE = /`[^`\n]*`|!?\[[^\]\n]*\]\([^)\n]*\)|<[^>\n]+>|https?:\/\/[^\s)>\]]+/g;
const WIKILINK_RE = /\[\[([^\]\n|]+)(?:\|([^\]\n]+))?\]\]/g;
const FENCE_RE = /^\s{0,3}(```|~~~)/;

// One pass over wikilinks AND ids: a resolved wikilink's output must not be scanned for ids again.
const TOKEN_RE = new RegExp(`${WIKILINK_RE.source}|${ID_RE.source}`, "g");

function linkifySegment(seg: string, index: LinkIndex, selfKey: string | null): string {
  return seg.replace(TOKEN_RE, (m, target?: string, text?: string) => {
    if (m.startsWith("[[")) {
      const t = (target ?? "").trim();
      const key = index.byLabel.get(t) ?? index.byKey.get(t);
      return key ? `[${(text ?? t).trim()}](${nodeHref(key)})` : m;
    }
    const key = index.byLabel.get(m);
    return key && key !== selfKey ? `[${m}](${nodeHref(key)})` : m;
  });
}

/** Rewrite relative `*.md` links to internal ones when the file basename is unique. */
function rewriteMdLinks(line: string, index: LinkIndex): string {
  return line.replace(/(?<!!)\[([^\]\n]+)\]\(([^)\s]+\.md)(#[^)\s]*)?\)/g, (m, text: string, path: string) => {
    if (/^[a-z][a-z0-9+.-]*:/i.test(path)) return m;
    const key = index.byFile.get(path.split("/").pop() ?? "");
    return key ? `[${text}](${nodeHref(key)})` : m;
  });
}

export function linkifyMarkdown(text: string, index: LinkIndex, selfKey: string | null = null): string {
  let fence: string | null = null;
  return text
    .split("\n")
    .map((line) => {
      const f = FENCE_RE.exec(line);
      if (f) {
        fence = fence === null ? f[1] : fence === f[1] ? null : fence;
        return line;
      }
      if (fence !== null) return line;
      const withMd = rewriteMdLinks(line, index);
      let out = "";
      let last = 0;
      for (const m of withMd.matchAll(PROTECTED_RE)) {
        out += linkifySegment(withMd.slice(last, m.index), index, selfKey) + m[0];
        last = (m.index ?? 0) + m[0].length;
      }
      return out + linkifySegment(withMd.slice(last), index, selfKey);
    })
    .join("\n");
}
