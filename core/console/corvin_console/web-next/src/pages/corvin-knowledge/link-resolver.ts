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

/**
 * `scope`/`uid` are set on the nodes of a joined view of several knowledge bases (projects/scope.ts `mergeGraphs`): there a node id
 * may be `<uid>@<project>` and a label `<project>/<id>`, because a duplicated project carries its original's uids and ids verbatim.
 */
export interface LinkTarget { id: string; label?: string; file?: string; scope?: string | null; uid?: string }

/** The same three lookups, restricted to ONE knowledge base and keyed by that base's own (unqualified) ids, uids and files. */
export interface ScopedLookups { byLabel: Map<string, string>; byKey: Map<string, string>; byFile: Map<string, string> }

export interface LinkIndex {
  /** human id (ADR-2206, T-0035 ...) -> node key; absent when two nodes carry the same id */
  byLabel: Map<string, string>;
  /** node key (uid or id) -> itself, so a key typed in a wikilink resolves too */
  byKey: Map<string, string>;
  /** file basename -> node key; absent when two nodes share a basename */
  byFile: Map<string, string>;
  /**
   * knowledge base (`""` = the bound one) -> its own lookups. A document is written against its own knowledge base, so in a joined
   * view a reference inside a copy's document resolves to the copy's node, never to the original's (REVIEW-0071 follow-up).
   */
  scoped?: Map<string, ScopedLookups>;
}

function setUnique(map: Map<string, string>, ambiguous: Set<string>, k: string, v: string) {
  if (map.has(k) && map.get(k) !== v) ambiguous.add(k);
  map.set(k, v);
}

function buildScoped(entities: LinkTarget[]): Map<string, ScopedLookups> | undefined {
  const out = new Map<string, ScopedLookups & { amb: { l: Set<string>; k: Set<string>; f: Set<string> } }>();
  for (const e of entities) {
    if (e.scope === undefined) continue;          // not a joined view
    const sk = e.scope ?? "";
    let s = out.get(sk);
    if (!s) { s = { byLabel: new Map(), byKey: new Map(), byFile: new Map(), amb: { l: new Set(), k: new Set(), f: new Set() } }; out.set(sk, s); }
    const label = e.label || e.id;
    const raw = e.scope && label.startsWith(`${e.scope}/`) ? label.slice(e.scope.length + 1) : label;
    setUnique(s.byLabel, s.amb.l, raw, e.id);
    setUnique(s.byKey, s.amb.k, e.uid ?? e.id, e.id);
    s.byKey.set(e.id, e.id);
    if (e.file) setUnique(s.byFile, s.amb.f, e.file, e.id);
  }
  if (out.size === 0) return undefined;
  const res = new Map<string, ScopedLookups>();
  out.forEach((s, k) => {
    s.amb.l.forEach((x) => s.byLabel.delete(x));
    s.amb.k.forEach((x) => s.byKey.delete(x));
    s.amb.f.forEach((x) => s.byFile.delete(x));
    res.set(k, { byLabel: s.byLabel, byKey: s.byKey, byFile: s.byFile });
  });
  return res;
}

/**
 * The lookups a document of `scope` resolves against: ONLY its own knowledge base. A project and Corvin both number from T-0001, so a
 * project's id never means another knowledge base's node, and an id that is missing from (or ambiguous in) the document's own base stays
 * plain text - the global lookups must not show through (REVIEW-0073 N10). What may cross is a qualified label (`alpha/T-0001`), which
 * names its knowledge base explicitly, in a wikilink.
 */
export function viewFor(index: LinkIndex, scope: string | null | undefined): LinkIndex {
  const own = scope === undefined ? undefined : index.scoped?.get(scope ?? "");
  if (!own) return index;
  const byLabel = new Map(own.byLabel);
  index.byLabel.forEach((v, k) => { if (k.includes("/") && !byLabel.has(k)) byLabel.set(k, v); });
  return { byLabel, byKey: own.byKey, byFile: own.byFile, scoped: index.scoped };
}

/**
 * The node a key names, as the knowledge base of `scope` names it. The server's link lists ("Links", "Linked from") carry raw uids of
 * the document's own knowledge base; in a joined view the same uid may belong to the original first, so it is translated here.
 */
export function resolveKey(index: LinkIndex, key: string, scope: string | null | undefined): string {
  return viewFor(index, scope).byKey.get(key) ?? key;
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
  return { byLabel, byKey, byFile, scoped: buildScoped(entities) };
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

/**
 * `scope` names the knowledge base the document belongs to (`null` = the bound one); leave it out outside a joined view. `selfKey`
 * is the document's own key as its knowledge base names it (its uid); a reference to itself is not turned into a link.
 */
export function linkifyMarkdown(text: string, base: LinkIndex, selfKey: string | null = null, scope?: string | null): string {
  const index = viewFor(base, scope);
  if (selfKey !== null) selfKey = index.byKey.get(selfKey) ?? selfKey;
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
