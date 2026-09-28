/**
 * gateNavGroups (layout.tsx) — the `requiredTier` gate.
 *
 * "skill-manager" is linked from the sidebar for owner/admin sessions only,
 * mirroring routes/skill_manager.py (install/uninstall answer 403 below those
 * tiers). The tier gate must not inherit the capability gate's
 * missing-manifest fail-safe: an unknown tier hides the entry.
 */
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { gateNavGroups } from "@/components/layout";

const Icon = () => null;
const groups = () => [
  {
    id: "build",
    items: [
      { to: "/app/forge", label: "Forge", icon: Icon },
      { to: "/app/skill-manager", label: "Skill Packages", icon: Icon, requiredTier: ["owner", "admin"] as const },
    ],
  },
];
const routes = (out: ReturnType<typeof gateNavGroups>) => out.flatMap((g) => g.items.map((it) => it.to));
const manifest = { capabilities: [], flags: {} } as unknown as Parameters<typeof gateNavGroups>[1];

describe("gateNavGroups requiredTier", () => {
  it.each(["owner", "admin"])("shows the entry to %s", (tier) => {
    expect(routes(gateNavGroups(groups(), manifest, tier))).toEqual(["/app/forge", "/app/skill-manager"]);
  });

  it.each([["member"], ["viewer"], [null], [undefined]])("hides it from tier %s", (tier) => {
    expect(routes(gateNavGroups(groups(), manifest, tier))).toEqual(["/app/forge"]);
  });

  it("hides it on an unknown tier even while the manifest is still loading", () => {
    expect(routes(gateNavGroups(groups(), undefined, undefined))).toEqual(["/app/forge"]);
    expect(routes(gateNavGroups(groups(), undefined, "owner"))).toEqual(["/app/forge", "/app/skill-manager"]);
  });

  it("the real NAV_GROUPS entry for skill-manager carries the owner/admin gate", () => {
    const here = dirname(fileURLToPath(import.meta.url));
    const src = readFileSync(resolve(here, "../../src/components/layout.tsx"), "utf8");
    const line = src.split("\n").find((l) => l.includes('to: "/app/skill-manager"'));
    expect(line).toBeDefined();
    expect(line).toMatch(/requiredTier:\s*\["owner",\s*"admin"\]/);
  });
});
