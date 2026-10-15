/**
 * Presentation model for the update trace. Kept out of the component so the
 * status -> label mapping is testable: a wrong mapping (an unproven run shown
 * as "done") is invisible in a screenshot until a user hits that case.
 */
import type { LastUpdate, UpdateChecks, UpdatePhase, UpdateStep } from "@/lib/api/settings";

export const PHASE_ORDER: UpdatePhase[] = ["triggered", "delivery", "ui_build", "update_script", "verification"];

export const PHASE_LABEL: Record<UpdatePhase, string> = {
  triggered: "Triggered",
  delivery: "Delivery",
  ui_build: "UI rebuild",
  update_script: "Update script",
  verification: "Verification",
};

export type RowState = "pending" | "running" | "ok" | "failed" | "skipped";

export interface TraceRow {
  phase: UpdatePhase;
  label: string;
  state: RowState;
  detail: string | null;
}

export function traceRows(last: LastUpdate | null): TraceRow[] {
  const steps = last?.steps ?? {};
  return PHASE_ORDER.map((phase) => {
    const s: UpdateStep | undefined = steps[phase];
    return { phase, label: PHASE_LABEL[phase], state: s ? s.status : "pending", detail: s?.detail ?? null };
  });
}

export const CHECK_LABEL: Record<keyof UpdateChecks, string> = {
  delivered: "Update delivered to this install",
  ui_rebuilt: "UI rebuilt",
  script_ok: "Update script finished",
  verified: "Verified over the wire",
  up_to_date: "Installed version is current",
};

export function checkRows(last: LastUpdate | null): { key: keyof UpdateChecks; label: string; ok: boolean }[] {
  const checks = last?.checks;
  if (!checks) return [];
  return (Object.keys(CHECK_LABEL) as (keyof UpdateChecks)[]).map((key) => ({
    key, label: CHECK_LABEL[key], ok: checks[key] === true,
  }));
}

/** One-line headline for a run. Only a fully proven run reads as completed. */
export function headline(last: LastUpdate | null): { text: string; tone: "ok" | "warn" | "bad" | "info" } {
  if (!last || !last.status) return { text: "No update has run yet", tone: "info" };
  switch (last.status) {
    case "running": {
      const phase = last.phase && last.phase in PHASE_LABEL ? PHASE_LABEL[last.phase as UpdatePhase] : "Triggered";
      return { text: `Update running - ${phase}`, tone: "info" };
    }
    case "success":
      return { text: "Completed and verified", tone: "ok" };
    case "unverified":
      return { text: "Finished, but not everything could be proven", tone: "warn" };
    case "interrupted":
      return { text: "Interrupted before it finished", tone: "warn" };
    default:
      return { text: `Failed${last.failed_phase ? ` - ${PHASE_LABEL[last.failed_phase as UpdatePhase] ?? last.failed_phase}` : ""}`, tone: "bad" };
  }
}

export function lastSuccessText(last: LastUpdate | null): string {
  if (!last?.last_success_at) return "never";
  const kind = last.last_success_trigger === "auto" ? " (auto-update)" : last.last_success_trigger === "manual" ? " (manual)" : "";
  const ver = last.last_success_version ? `, v${last.last_success_version}` : "";
  return `${new Date(last.last_success_at * 1000).toLocaleString("en-US")}${kind}${ver}`;
}
