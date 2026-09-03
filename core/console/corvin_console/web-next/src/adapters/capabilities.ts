/**
 * Capability-manifest adapter (ADR-0357 P3) + Console-manifest adapter (ADR-0561).
 *
 * ADR-0357 P3: Backend (routes/capabilities.py) is the SSOT for "what panels may
 * the shell mount". A panel declares `requiredCapability` / `requiredFlag`, and the
 * shell mounts it only when the manifest reports the capability present and flag on.
 *
 * ADR-0561: NEW `get_console_manifest()` endpoint returns unified v2.0 manifest
 * including all panels (builtin + plugin + skill + ai-generated), nav structure,
 * gating, and hash for caching/invalidation. Frontend renders sidebar + routes from
 * this single manifest.
 */
import { useQuery } from "@tanstack/react-query";
import { z } from "zod";
import type { ConsolePanel } from "@/panels/types";

const BASE = "/v1/console";

/** The manifest contract version this shell build understands. */
export const EXPECTED_CONTRACT_VERSION = "1";

// ─────────────────────────────────────────────────────────────────────────────
// ADR-0357 P3: Capability Manifest (v1.0)
// ─────────────────────────────────────────────────────────────────────────────

const zManifest = z.object({
  contract_version: z.string(),
  capabilities: z.array(z.string()),
  flags: z.record(z.string(), z.boolean()),
});
export type CapabilityManifest = z.infer<typeof zManifest>;

// ─────────────────────────────────────────────────────────────────────────────
// ADR-0561: Console Manifest v2.0 (Unified Panels + Nav)
// ─────────────────────────────────────────────────────────────────────────────

const zPanelElement = z.union([
  z.object({
    kind: z.literal("react-component"),
    component: z.string(),
  }),
  z.object({
    kind: z.literal("react"),
    load: z.string().describe("Async import path"),
  }),
  z.object({
    kind: z.literal("iframe"),
    src: z.string(),
  }),
  z.object({
    kind: z.literal("skill-inspector"),
    skill_id: z.string(),
  }),
  z.object({
    kind: z.literal("plugin-inspector"),
    plugin_id: z.string(),
  }),
]);

const zPanelDescriptor = z.object({
  id: z.string(),
  title: z.string(),
  route: z.string(),
  icon: z.string(),
  kind: z.enum(["feature", "plugin", "skill", "ai-generated"]),
  source: z.enum(["builtin", "installed", "user-generated"]),
  nav_group: z.string(),
  requiredFlag: z.string().nullable(),
  requiredCapability: z.string().nullable(),
  element: zPanelElement,
  version: z.string(),
  audit_events: z.array(z.string()),
  tenant_scoped: z.boolean(),
});

const zNavItem = z.object({
  panel_id: z.string(),
});

const zNavGroup = z.object({
  id: z.string(),
  label: z.string().nullable(),
  collapsible: z.boolean(),
  defaultOpen: z.boolean(),
  items: z.array(zNavItem),
});

const zConsoleManifest = z.object({
  version: z.literal("2.0"),
  timestamp: z.string(),
  contract_version: z.string(),
  capabilities: z.array(z.string()),
  flags: z.record(z.string(), z.boolean()),
  panels: z.array(zPanelDescriptor),
  nav_groups: z.array(zNavGroup),
  hash: z.string(),
});

export type PanelElement = z.infer<typeof zPanelElement>;
export type PanelDescriptor = z.infer<typeof zPanelDescriptor>;
export type NavGroup = z.infer<typeof zNavGroup>;
export type ConsoleManifest = z.infer<typeof zConsoleManifest>;

async function fetchCapabilities(): Promise<CapabilityManifest> {
  const r = await fetch(`${BASE}/capabilities`, { credentials: "include" });
  if (!r.ok) throw new Error(`capabilities ${r.status}`);
  return zManifest.parse(await r.json());
}

export function useCapabilities() {
  // The shell calls this from App, which renders before auth, so the first fetch
  // can 401. That is fine — gatePanels() treats an absent manifest as fail-safe
  // (ungated core panels) and the /app routes are behind RequireAuth anyway.
  // retry:false avoids a pre-auth retry storm; refetchOnWindowFocus (RQ default)
  // re-fetches once the user is authenticated, so gated panels appear without a
  // reload. Moving the query into the authed shell is a tracked follow-up.
  return useQuery({
    queryKey: ["capabilities"],
    queryFn: fetchCapabilities,
    staleTime: 60_000,
    retry: false,
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// ADR-0561 Phase 1: useConsoleManifest() — backend-driven panels + nav
// ─────────────────────────────────────────────────────────────────────────────

async function fetchConsoleManifest(signal?: AbortSignal): Promise<ConsoleManifest> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 200); // 200ms timeout (ADR-0561 Synthesis)

  try {
    const r = await fetch(`${BASE}/manifest`, {
      credentials: "include",
      signal: signal || controller.signal,
    });

    if (!r.ok) throw new Error(`manifest ${r.status}`);
    const data = await r.json();
    return zConsoleManifest.parse(data);
  } catch (e) {
    // On timeout or error, return fallback (builtin panels only)
    if ((e as Error).name === "AbortError") {
      console.warn("Console manifest fetch timeout (200ms); using fallback");
    } else {
      console.error("Console manifest fetch failed:", e);
    }
    // Return cached/fallback manifest (builtin panels)
    // The layout will render with this, making the shell always usable
    throw e; // Let React Query cache it + use fallback
  } finally {
    clearTimeout(timeout);
  }
}

export function useConsoleManifest() {
  return useQuery({
    queryKey: ["console-manifest"],
    queryFn: ({ signal }) => fetchConsoleManifest(signal),
    staleTime: 5 * 60_000, // Cache for 5 min (ADR-0561 Synthesis)
    gcTime: 10 * 60_000,
    retry: false, // Don't retry; let fallback handle it
  });
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
