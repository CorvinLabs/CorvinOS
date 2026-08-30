import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { PANELS } from "@/panels/registry";

/**
 * A panel needs TWO registrations to be reachable, and only one of them is
 * type-checked.
 *
 *   1. PANELS (src/panels/registry.tsx) — panelRoutes() mounts /app/<route>
 *   2. NAV_GROUPS (src/components/layout.tsx) — draws the sidebar entry
 *
 * ConsolePanel.nav looks like it drives the sidebar. It does not: NAV_GROUPS is
 * a separate hand-maintained list. Registering only #1 mounts a route nothing
 * links to, which presents as "the console still shows the old build" — and did,
 * for the four Brain Engineering panels.
 *
 * NAV_GROUPS is a module-private literal, so this asserts against the source
 * text rather than importing it. Coarse, but it fails for exactly the reason
 * that matters and needs no production code reshaped to be testable.
 */

const here = dirname(fileURLToPath(import.meta.url));
const layoutSrc = readFileSync(
  resolve(here, "../../src/components/layout.tsx"),
  "utf8",
);

/** Panels intentionally absent from the sidebar (reached from another panel,
 *  or a detail route). Add here WITH a reason rather than deleting the test. */
const NAV_EXEMPT = new Set<string>([
  // Reached from the Settings page (settings.tsx navigates to it), not the sidebar.
  "settings/github",
  // Backend routes are absent — /api/console/audit/* and /api/console/releases/*
  // both 404 on the live host (verified 2026-08-26), and the working audit
  // surface is the separate /app/compliance panel. A sidebar entry here would
  // link to a page that can only render its error state. Remove the exemption
  // once the routes exist.
  "audit",
  "releases",
  // Folded into the unified "Plugins & Extensions" hub (/app/plugin-center),
  // which renders these three as tabs. The standalone routes stay mounted for
  // deep-link stability but are intentionally no longer in the sidebar.
  "extensions",
  "mcp-plugins",
  "plugins",
]);

describe("panel wiring: registry route <-> sidebar nav", () => {
  const navigable = PANELS.filter((p) => p.nav && !NAV_EXEMPT.has(p.id));

  it("has panels with nav metadata to check", () => {
    expect(navigable.length).toBeGreaterThan(0);
  });

  it.each(navigable.map((p) => [p.id, p.route] as const))(
    "panel %s is linked from NAV_GROUPS at /app/%s",
    (_id, route) => {
      expect(layoutSrc).toContain(`/app/${route}`);
    },
  );

  it("every NAV_GROUPS /app/ target resolves to a mounted panel or a core route", () => {
    // Core routes live directly in App.tsx, not in the panel registry.
    const CORE_ROUTES = new Set([
      "chat", "dashboard", "settings", "license", "audit-log",
      "personas", "workflows",
    ]);
    const routes = new Set(PANELS.map((p) => p.route));
    const linked = [
      ...layoutSrc.matchAll(/to:\s*"\/app\/([a-z0-9-]+)"/g),
    ].map((m) => m[1]);

    const dangling = linked.filter(
      (r) => !routes.has(r) && !CORE_ROUTES.has(r),
    );
    expect(dangling).toEqual([]);
  });
});

/**
 * A gated panel needs a THIRD registration, in the backend.
 *
 *   3. GATED_FLAGS (core/console/corvin_console/routes/capabilities.py)
 *
 * The capability manifest only carries the flags listed there. gateNavGroups()
 * hides an item whose `requiredFlag` is falsy in `manifest.flags`, and a key the
 * manifest never carries is `undefined` — so a requiredFlag missing from
 * GATED_FLAGS hides its panel FOREVER, no matter how the operator sets the flag.
 * That is exactly how the Marketplace panel shipped invisible: registered in
 * PANELS and in NAV_GROUPS, absent from GATED_FLAGS (and from the flag registry).
 */
const capabilitiesSrc = readFileSync(
  resolve(here, "../../../routes/capabilities.py"),
  "utf8",
);
const flagRegistrySrc = readFileSync(
  resolve(here, "../../../../corvin_core/feature_flags.py"),
  "utf8",
);

describe("panel wiring: requiredFlag <-> backend capability manifest", () => {
  const gatedBlock = capabilitiesSrc.match(
    /GATED_FLAGS:\s*tuple\[str, \.\.\.\]\s*=\s*\(([\s\S]*?)\n\)/,
  );
  const gatedFlags = new Set(
    [...(gatedBlock?.[1] ?? "").matchAll(/"([a-z0-9_]+)"/g)].map((m) => m[1]),
  );
  const registryFlags = new Set(
    [...flagRegistrySrc.matchAll(/id="([a-z0-9_]+)"/g)].map((m) => m[1]),
  );

  const registrySrc = readFileSync(
    resolve(here, "../../src/panels/registry.tsx"),
    "utf8",
  );
  const declared = new Set([
    ...[...registrySrc.matchAll(/requiredFlag:\s*"([a-z0-9_]+)"/g)].map((m) => m[1]),
    ...[...layoutSrc.matchAll(/requiredFlag:\s*"([a-z0-9_]+)"/g)].map((m) => m[1]),
  ]);

  it("parsed GATED_FLAGS out of capabilities.py", () => {
    expect(gatedFlags.size).toBeGreaterThan(0);
  });

  it("parsed the feature-flag registry", () => {
    expect(registryFlags.size).toBeGreaterThan(0);
  });

  it.each([...declared])(
    "requiredFlag %s is listed in GATED_FLAGS",
    (flag) => {
      expect([...gatedFlags]).toContain(flag);
    },
  );

  it.each([...declared])(
    "requiredFlag %s is a registered feature flag",
    (flag) => {
      expect([...registryFlags]).toContain(flag);
    },
  );
});
