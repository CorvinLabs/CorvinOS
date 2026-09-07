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

/**
 * Same-origin, in-app navigation targets that arrive from a source the console
 * does not author: a server response body, an LLM-produced action block, a
 * panel postMessage, a chat event.
 *
 * R4-C7 (adversarial review round 4, 2026-09-07): four call sites navigated on a
 * value they did not author and only one of them checked it. The worst was
 * `components/assistant/ConsoleAssistant.tsx`, which parses an `_actions` array
 * out of the assistant's own answer and called `navigate(action.path)` — so a
 * prompt-injected reply could steer the operator's console anywhere.
 *
 * This matters more than it normally would because react-router 6.30.6 carries
 * GHSA-wrjc-x8rr-h8h6 (open redirect via BACKSLASH in `<Link>` / `useNavigate`,
 * the CVE-2025-68470 bypass), fixed only in 7.18.0 — a major migration.
 * Rejecting the shapes here closes the reachable half without that migration.
 * The second advisory on that version, GHSA-337j-9hxr-rhxg (arbitrary
 * constructor injection via `deserializeErrors()` during SSR hydration), has no
 * subject here: this console is a pure SPA and does no SSR.
 *
 * Fail-closed. A target must be an absolute in-app path and nothing else:
 *
 *   accepted  "/console/app/tasks"   "/x?y=1#z"
 *   rejected  "//evil.com"           protocol-relative
 *             "/\\evil.com"          the backslash bypass — a browser reads \ as /
 *             "/%5cevil.com"         the same, percent-encoded
 *             "/\tx"                 control chars are stripped before parsing
 *             "https://evil.com"     absolute, off-origin
 *             "javascript:alert(1)"  not a path at all
 *             "evil.com"  ""         relative / empty
 */
export function safeNavTarget(to: unknown): to is string {
  if (typeof to !== "string") return false;
  if (!to || to.length > 2048) return false;
  if (to !== to.trim()) return false; // leading/trailing space is never a real route
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u001f\u007f]/.test(to)) return false;
  if (to.includes("\\")) return false; // the GHSA-wrjc-x8rr-h8h6 shape
  if (/%5c/i.test(to)) return false; //  …and its percent-encoded form
  if (!to.startsWith("/")) return false; // absolute in-app path only
  if (to.startsWith("//")) return false; // protocol-relative
  return true;
}
