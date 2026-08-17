/**
 * Capability-manifest adapter (ADR-0357 P3) — the SINGLE fetch site for the
 * versioned capability manifest the shell renders its nav/routes from.
 *
 * The backend (routes/capabilities.py) is the SSOT for "what panels may the shell
 * mount": a panel declares `requiredCapability` / `requiredFlag`, and the shell
 * mounts it only when the manifest reports the capability present and the flag on.
 * `contract_version` lets the shell refuse to gate against a manifest shape it does
 * not understand — instead of hiding everything, it degrades to the ungated core
 * panels so the Console never goes blank on a version skew.
 */
import { useQuery } from "@tanstack/react-query";
import { z } from "zod";
import type { ConsolePanel } from "@/panels/types";

const BASE = "/v1/console";

/** The manifest contract version this shell build understands. */
export const EXPECTED_CONTRACT_VERSION = "1";

const zManifest = z.object({
  contract_version: z.string(),
  capabilities: z.array(z.string()),
  flags: z.record(z.string(), z.boolean()),
});
export type CapabilityManifest = z.infer<typeof zManifest>;

async function fetchManifest(): Promise<CapabilityManifest> {
  const r = await fetch(`${BASE}/capabilities`, { credentials: "include" });
  if (!r.ok) throw new Error(`capabilities ${r.status}`);
  return zManifest.parse(await r.json());
}

export function useCapabilities() {
  return useQuery({ queryKey: ["capabilities"], queryFn: fetchManifest, staleTime: 60_000 });
}

/**
 * Gate the static panel registry against a capability manifest. Pure — unit-tested
 * without a browser.
 *
 * - No manifest yet (loading / fetch error): show the ungated panels only (a panel
 *   with neither gate is a core panel that must always render), so the shell is
 *   usable before/without the manifest.
 * - Manifest whose `contract_version` this build does not understand: same
 *   fail-safe — ungated core panels only, never a blank shell, never a gated panel
 *   mounted against a shape we can't trust.
 * - Understood manifest: a panel mounts iff its `requiredCapability` (if any) is in
 *   `capabilities` AND its `requiredFlag` (if any) is true in `flags`.
 */
export function gatePanels(
  panels: readonly ConsolePanel[],
  manifest: CapabilityManifest | null | undefined,
): ConsolePanel[] {
  const understood =
    manifest != null && manifest.contract_version === EXPECTED_CONTRACT_VERSION;

  if (!understood) {
    return panels.filter((p) => !p.requiredCapability && !p.requiredFlag);
  }

  const caps = new Set(manifest!.capabilities);
  const flags = manifest!.flags;
  return panels.filter((p) => {
    if (p.requiredCapability && !caps.has(p.requiredCapability)) return false;
    if (p.requiredFlag && !flags[p.requiredFlag]) return false;
    return true;
  });
}
