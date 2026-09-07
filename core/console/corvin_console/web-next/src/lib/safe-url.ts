/**
 * http(s)-only href allowlist for values that arrive from a REMOTE source.
 *
 * R2-C1 (adversarial review round 2, 2026-09-07): the RAG panel rendered
 * `<a href={item.source_url}>` with a value produced by a remote RAG provider.
 * A hostile provider could return `javascript:...` / `data:text/html,...` /
 * `vbscript:...` and get script execution on click inside the console origin.
 *
 * The backend (routes/rag.py + shared/rag_query_engine.py) enforces the same
 * allowlist; this is the second half of the pair, so a value that reaches the
 * DOM through any other path is still safe. Fail-closed: anything that is not
 * an absolute http(s) URL with a host returns null and the caller renders
 * plain text instead of a link.
 */
export function safeHttpUrl(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const text = value.trim();
  if (!text || text.length > 2048) return null;
  // Control characters — including the \t\r\n a browser strips before scheme
  // parsing, which is how `java\nscript:` bypasses a naive prefix check.
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u001f\u007f]/.test(text)) return null;
  let url: URL;
  try {
    url = new URL(text);
  } catch {
    return null; // relative or malformed → not a link we render
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") return null;
  if (!url.hostname) return null;
  return text;
}
