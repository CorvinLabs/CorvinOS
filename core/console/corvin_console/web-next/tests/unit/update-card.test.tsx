import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { UpdateCard } from "@/components/settings/UpdateCard";

const check = vi.fn();
vi.mock("@/lib/api", async (orig) => ({
  ...(await orig<object>()),
  checkUpdate: () => check(),
  getAutoUpdate: async () => ({ enabled: true, version: "1.0.0" }),
  setAutoUpdate: async () => ({ enabled: true, ok: true }),
  installUpdate: async () => ({ success: true, exit_code: null, final_message: "started" }),
}));

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><UpdateCard csrf="x" /></QueryClientProvider>);
}
const result = (last: object) => ({ has_update: false, current_version: "1.0.0", available_version: null, error: null,
  running: false, behind: 0, local_sha: "a", remote_sha: "a", last_update: last });

describe("UpdateCard trace", () => {
  it("renders phases, proofs and the last verified time for a proven run", async () => {
    check.mockResolvedValue(result({ at: 1700000000, status: "success", phase: "completed", trigger: "auto",
      version_before: "1", version_after: "2", detail: null, last_success_at: 1700000000, last_success_trigger: "auto",
      steps: Object.fromEntries(["triggered", "delivery", "ui_build", "update_script", "verification"].map((p) => [p, { status: "ok", at: 1, detail: null }])),
      checks: { delivered: true, ui_rebuilt: true, script_ok: true, verified: true, up_to_date: true } }));
    mount();
    expect((await screen.findByTestId("update-trace-headline")).textContent).toMatch(/Completed and verified \(auto-update\)/);
    expect(screen.getByTestId("phase-ui_build").getAttribute("data-state")).toBe("ok");
    expect(screen.getByTestId("last-success").textContent).toMatch(/\(auto-update\)/);
  });

  it("an unproven run is flagged and never shows as completed", async () => {
    check.mockResolvedValue(result({ at: 5, status: "unverified", phase: "failed", detail: "not proven: ui_build",
      version_before: "1", version_after: "1", last_success_at: null,
      steps: { ui_build: { status: "failed", at: 1, detail: null } },
      checks: { delivered: true, ui_rebuilt: false, script_ok: true, verified: true, up_to_date: true } }));
    mount();
    expect((await screen.findByTestId("update-trace-headline")).textContent).not.toMatch(/Completed/);
    expect(screen.getByTestId("update-checks").querySelector('[data-ok="false"]')?.textContent).toMatch(/UI rebuilt/);
    expect(screen.getByTestId("last-success").textContent).toMatch(/never/);
  });
});
