/**
 * A sibling FILE silently shadows a page DIRECTORY.
 *
 * `import("@/pages/foo")` resolves `src/pages/foo.tsx` BEFORE
 * `src/pages/foo/index.tsx` — with no warning from vite, tsc or eslint. The
 * route then compiles, bundles, mounts and renders the OLD (or a different)
 * page, which presents exactly like a stale bundle.
 *
 * It has happened twice on the same route: 2026-08-27 (`pages/vibe-engineering.tsx`
 * shadowed the ADR-0400 dashboard until ADR-0431 deleted it) and 2026-09-17
 * (95ecc2b6 re-added the file; /app/vibe-engineering crashed on nine 404s).
 * This test fails on the third.
 */
import { describe, it, expect } from "vitest";
import { existsSync, readdirSync, statSync } from "node:fs";
import { join, resolve } from "node:path";

const PAGES = resolve(__dirname, "../../src/pages");

// Known pairs, exempt WITH a reason (same pattern as NAV_EXEMPT in
// panel-nav-wiring.test.ts). Add here only when neither side is imported by
// any route — a wired route must never be on this list.
const SHADOW_EXEMPT: Record<string, string> = {
  marketplace:
    "pages/marketplace.tsx (ADR-0682 skill discovery) and pages/marketplace/ " +
    "(plugin list) are BOTH unreferenced — lazy-pages.ts routes /app/marketplace-hub " +
    "to pages/marketplace-hub.tsx. Dead pair since 2026-09-16; resolve when one is wired.",
};

describe("src/pages: no file shadows a page directory", () => {
  const dirs = readdirSync(PAGES).filter((n) => statSync(join(PAGES, n)).isDirectory());

  it("has at least one page directory to check (positive control)", () => {
    expect(dirs.length).toBeGreaterThan(0);
    expect(dirs).toContain("vibe-engineering");
  });

  it("every exemption names a directory that still exists", () => {
    for (const name of Object.keys(SHADOW_EXEMPT)) expect(dirs).toContain(name);
  });

  it.each(dirs)("src/pages/%s/ has no sibling file of the same name", (name) => {
    if (SHADOW_EXEMPT[name]) return;
    const shadows = ["tsx", "ts", "jsx", "js"]
      .map((ext) => `${name}.${ext}`)
      .filter((f) => existsSync(join(PAGES, f)));
    expect(shadows, `delete ${shadows.join(", ")} — it wins over ${name}/index.tsx`).toEqual([]);
  });
});
