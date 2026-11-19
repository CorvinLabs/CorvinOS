import { describe, it, expect } from 'vitest';
import { safeHttpUrl, safeNavTarget, safePanelSrc } from '@/lib/safe-url';

/**
 * R2-C1 (adversarial review round 2, 2026-09-07).
 *
 * `pages/rag.tsx` rendered `<a href={item.source_url}>` with a value produced
 * by a REMOTE RAG provider — a `javascript:` href there is script execution in
 * the console origin on click. `safeHttpUrl` is the frontend half of the pair
 * (the backend halves are `routes/rag.py::_safe_source_url` and
 * `shared/rag_query_engine.py::safe_http_url`).
 */
describe('safeHttpUrl (R2-C1 href allowlist)', () => {
  it('rejects script-bearing schemes', () => {
    const hostile = [
      'javascript:alert(document.domain)',
      'JavaScript:alert(1)',
      '  javascript:alert(1)',
      'JAVASCRIPT:alert(1)',
      'vbscript:msgbox(1)',
      'data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==',
      'file:///etc/passwd',
      'blob:https://example.com/uuid',
    ];
    for (const value of hostile) {
      expect(safeHttpUrl(value), value).toBeNull();
    }
  });

  it('rejects control characters that browsers strip before scheme parsing', () => {
    // `java\nscript:` becomes `javascript:` once the browser strips \n.
    expect(safeHttpUrl('java\nscript:alert(1)')).toBeNull();
    expect(safeHttpUrl('java\tscript:alert(1)')).toBeNull();
    expect(safeHttpUrl('java\rscript:alert(1)')).toBeNull();
    expect(safeHttpUrl('http://exa\u0000mple.com/')).toBeNull();
  });

  it('rejects relative, protocol-relative and malformed values', () => {
    for (const value of ['/relative/path', '//evil.example/x', 'not a url', '', '   ', 'http://']) {
      expect(safeHttpUrl(value), value).toBeNull();
    }
  });

  it('rejects non-string input', () => {
    for (const value of [null, undefined, 42, {}, [], true]) {
      expect(safeHttpUrl(value)).toBeNull();
    }
  });

  it('rejects an absurdly long URL', () => {
    expect(safeHttpUrl(`https://example.com/${'a'.repeat(4000)}`)).toBeNull();
  });

  it('accepts absolute http(s) URLs verbatim', () => {
    const ok = [
      'http://example.com/a',
      'https://example.com/a?b=1#c',
      'https://sub.example.com:8443/x/y',
      'https://example.com',
    ];
    for (const value of ok) {
      expect(safeHttpUrl(value), value).toBe(value);
    }
  });

  it('trims surrounding whitespace but keeps the URL intact', () => {
    expect(safeHttpUrl('  https://example.com/a  ')).toBe('https://example.com/a');
  });
});

describe('safeNavTarget (R4-C7, 2026-09-07)', () => {
  // react-router 6.30.6 carries GHSA-wrjc-x8rr-h8h6: an open redirect via a
  // BACKSLASH in <Link>/useNavigate — the CVE-2025-68470 bypass — fixed only in
  // 7.18.0, a major migration. Four call sites navigated on a value they did not
  // author and only the panel one checked it; the worst parsed its target out of
  // the assistant's own LLM answer. These cases are what makes that advisory
  // unreachable without migrating the router, so they are load-bearing.

  it('rejects the backslash bypass in every form', () => {
    for (const bad of ['/\\evil.com', '\\\\evil.com', '/a/\\evil.com']) {
      expect(safeNavTarget(bad), bad).toBe(false);
    }
  });

  it('rejects the percent-encoded backslash, either case', () => {
    expect(safeNavTarget('/%5cevil.com')).toBe(false);
    expect(safeNavTarget('/%5Cevil.com')).toBe(false);
  });

  it('rejects protocol-relative targets', () => {
    expect(safeNavTarget('//evil.com')).toBe(false);
    expect(safeNavTarget('//evil.com/path')).toBe(false);
  });

  it('rejects control characters, which a browser strips before parsing', () => {
    for (const bad of ['/\tevil', '/\nevil', '/\revil', '/\u0000evil']) {
      expect(safeNavTarget(bad), JSON.stringify(bad)).toBe(false);
    }
  });

  it('rejects anything that is not an absolute in-app path', () => {
    for (const bad of ['https://evil.com', 'javascript:alert(1)', 'data:text/html,x',
                       'evil.com', 'app/x', '', ' /app/x', '/app/x ']) {
      expect(safeNavTarget(bad), JSON.stringify(bad)).toBe(false);
    }
  });

  it('rejects non-strings and absurdly long targets', () => {
    for (const bad of [42, null, undefined, {}, ['/a']]) {
      expect(safeNavTarget(bad), String(bad)).toBe(false);
    }
    expect(safeNavTarget(`/${'a'.repeat(4000)}`)).toBe(false);
  });

  it('accepts ordinary in-app routes', () => {
    for (const ok of ['/', '/app/dashboard', '/app/tasks?id=1', '/app/x#frag',
                      '/console/app/infinite-session']) {
      expect(safeNavTarget(ok), ok).toBe(true);
    }
  });
});

describe('safePanelSrc (manifest iframe src allowlist)', () => {
  it('accepts a generated-panel path and an https URL', () => {
    expect(safePanelSrc('/v1/console/panels/my-panel/index.html')).toBe('/v1/console/panels/my-panel/index.html');
    expect(safePanelSrc('https://example.com/panel.html')).toBe('https://example.com/panel.html');
  });
  it('rejects every other same-origin path — it would frame the console or its API', () => {
    expect(safePanelSrc('/console/app/settings')).toBeNull();
    expect(safePanelSrc('/v1/console/auth/whoami')).toBeNull();
    expect(safePanelSrc('/v1/console/panelsX/a/index.html')).toBeNull();
    expect(safePanelSrc('/external-panels/x/index.html')).toBeNull();
  });
  it('rejects path traversal out of the panels prefix', () => {
    expect(safePanelSrc('/v1/console/panels/../auth/whoami')).toBeNull();
    expect(safePanelSrc('/v1/console/panels/%2e%2e/auth/whoami')).toBeNull();
    expect(safePanelSrc('/v1/console/panels/a/./index.html')).toBeNull();
  });
  it('rejects http, script and data schemes, protocol-relative and non-strings', () => {
    expect(safePanelSrc('http://example.com/panel.html')).toBeNull();
    expect(safePanelSrc('javascript:alert(1)')).toBeNull();
    expect(safePanelSrc('data:text/html,<script>1</script>')).toBeNull();
    expect(safePanelSrc('blob:https://example.com/x')).toBeNull();
    expect(safePanelSrc('//evil.com/v1/console/panels/x')).toBeNull();
    expect(safePanelSrc('/\\evil.com')).toBeNull();
    expect(safePanelSrc('panel.html')).toBeNull();
    expect(safePanelSrc(undefined)).toBeNull();
  });
});
