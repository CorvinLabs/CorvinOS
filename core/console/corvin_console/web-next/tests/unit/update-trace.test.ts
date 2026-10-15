import { describe, expect, it } from "vitest";
import { checkRows, headline, lastSuccessText, traceRows } from "@/lib/update-trace";
import type { LastUpdate } from "@/lib/api/settings";

const base: LastUpdate = { at: 1, status: "success", version_before: "1", version_after: "1", detail: null };

describe("update trace model", () => {
  it("shows all five phases, pending until reported", () => {
    const rows = traceRows({ ...base, status: "running", steps: { triggered: { status: "ok", at: 1, detail: null },
      delivery: { status: "running", at: 2, detail: null } } });
    expect(rows.map((r) => [r.phase, r.state])).toEqual([
      ["triggered", "ok"], ["delivery", "running"], ["ui_build", "pending"],
      ["update_script", "pending"], ["verification", "pending"]]);
  });

  it("only a proven run reads as completed", () => {
    expect(headline({ ...base, status: "success" }).tone).toBe("ok");
    expect(headline({ ...base, status: "unverified" }).tone).toBe("warn");
    expect(headline({ ...base, status: "unverified" }).text).not.toMatch(/Completed/);
    expect(headline({ ...base, status: "failed", failed_phase: "ui_build" }).text).toBe("Failed - UI rebuild");
    expect(headline({ ...base, status: "running", phase: "ui_build" }).text).toBe("Update running - UI rebuild");
    expect(headline(null).tone).toBe("info");
  });

  it("lists each proof and marks the unmet one", () => {
    const rows = checkRows({ ...base, checks: { delivered: true, ui_rebuilt: false, script_ok: true, verified: true, up_to_date: true } });
    expect(rows.find((r) => r.key === "ui_rebuilt")?.ok).toBe(false);
    expect(rows.filter((r) => r.ok)).toHaveLength(4);
    expect(checkRows({ ...base, checks: null })).toEqual([]);
  });

  it("last success is 'never' without a verified run, and names its trigger", () => {
    expect(lastSuccessText({ ...base, last_success_at: null })).toBe("never");
    expect(lastSuccessText(null)).toBe("never");
    expect(lastSuccessText({ ...base, last_success_at: 1700000000, last_success_trigger: "auto", last_success_version: "2.0" }))
      .toMatch(/\(auto-update\), v2\.0$/);
  });
});
