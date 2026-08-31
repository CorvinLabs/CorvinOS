/** Unit proof for the P3 panel-gating logic (ADR-0357). Pure function, no browser. */
import { describe, it, expect } from "vitest";
import { gatePanels, EXPECTED_CONTRACT_VERSION, type CapabilityManifest } from "@/adapters/capabilities";
import type { ConsolePanel } from "@/panels/types";

const panel = (id: string, extra: Partial<ConsolePanel> = {}): ConsolePanel => ({
  id, route: id, nav: { label: id, icon: "" },
  element: { kind: "iframe", src: "", sandbox: "" },
  contractVersion: "1", ...extra,
});
const core = panel("dashboard");
const capGated = panel("vibe", { requiredCapability: "vibe-engineering" });
const flagGated = panel("beta", { requiredFlag: "vibe_engineering_active" });
const both = panel("both", { requiredCapability: "vibe-engineering", requiredFlag: "vibe_engineering_active" });
const ALL = [core, capGated, flagGated, both];
const manifest = (over: Partial<CapabilityManifest> = {}): CapabilityManifest => ({
  contract_version: EXPECTED_CONTRACT_VERSION,
  capabilities: ["dashboard", "vibe-engineering"],
  flags: { vibe_engineering_active: true }, ...over,
});
describe("gatePanels", () => {
  it("no manifest → only ungated core panels", () => {
    expect(gatePanels(ALL, undefined).map((p) => p.id)).toEqual(["dashboard"]);
  });
  it("unknown contract version → fail-safe to ungated core panels", () => {
    expect(gatePanels(ALL, manifest({ contract_version: "999" })).map((p) => p.id)).toEqual(["dashboard"]);
  });
  it("understood manifest → gates by capability AND flag", () => {
    expect(gatePanels(ALL, manifest()).map((p) => p.id)).toEqual(["dashboard", "vibe", "beta", "both"]);
  });
  it("missing capability hides the cap-gated panel", () => {
    const ids = gatePanels(ALL, manifest({ capabilities: ["dashboard"] })).map((p) => p.id);
    expect(ids).toContain("dashboard");
    expect(ids).not.toContain("vibe");
    expect(ids).not.toContain("both");
  });
  it("false flag hides the flag-gated panel", () => {
    const ids = gatePanels(ALL, manifest({ flags: { vibe_engineering_active: false } })).map((p) => p.id);
    expect(ids).toEqual(["dashboard", "vibe"]);
  });
});
