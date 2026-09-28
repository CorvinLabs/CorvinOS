/**
 * pages/admin/skill-manager.tsx (the mounted "skill-manager" panel) uploads
 * over XMLHttpRequest for progress. Its load handler used to `throw` on any
 * non-200 inside an async event listener: nothing caught it (the surrounding
 * try/catch had long returned), so it became an unhandled rejection and
 * `uploading` stayed true forever — the form stayed disabled, the button read
 * "Uploading 100%". Since the install route is owner/admin + CSRF guarded, a
 * 403 and a 400 (bad ZIP, signature mismatch) are ordinary answers; they must
 * surface the backend's `detail` and re-enable the form.
 */
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "../fixtures/server";
import { installCsrfFetch, setCurrentCsrf } from "@/lib/csrf-fetch";
import { SkillManager } from "@/pages/admin/skill-manager";

const INSTALLED = "/v1/console/skills-manager/skills/installed";
const INSTALL = "/v1/console/skills-manager/skills/install";

beforeAll(() => {
  installCsrfFetch();
  setCurrentCsrf("csrf-test");
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const zip = () => new File([new Uint8Array([80, 75, 3, 4])], "pkg.zip", { type: "application/zip" });

function fillAndSubmit(container: HTMLElement) {
  fireEvent.change(screen.getByPlaceholderText("e.g., my-awesome-skill"), { target: { value: "demo" } });
  fireEvent.change(screen.getByPlaceholderText("e.g., 1.0.0"), { target: { value: "1.0.0" } });
  fireEvent.change(container.querySelector('input[type="file"]') as HTMLInputElement, { target: { files: [zip()] } });
  fireEvent.click(screen.getByRole("button", { name: /Install Skill/ }));
}

describe("admin skill manager upload", () => {
  it.each([
    [403, "owner or admin required"],
    [400, "File must be ZIP"],
  ])("a %i answer shows the backend detail and re-enables the form", async (status, detail) => {
    let csrf: string | null = null;
    server.use(
      http.get(INSTALLED, () => HttpResponse.json({ skills: [], total: 0 })),
      http.post(INSTALL, ({ request }) => {
        csrf = request.headers.get("x-csrf-token");
        return HttpResponse.json({ detail }, { status });
      }),
    );
    const unhandled = vi.fn();
    process.on("unhandledRejection", unhandled);
    try {
      const { container } = render(<SkillManager />);
      await screen.findByText("No skills installed yet.");
      fillAndSubmit(container);
      expect(await screen.findByText(detail)).toBeInTheDocument();
      // Form usable again: the inputs are enabled and the button no longer
      // reads "Uploading …".
      await waitFor(() =>
        expect(screen.getByPlaceholderText("e.g., my-awesome-skill")).not.toBeDisabled(),
      );
      expect(screen.queryByText(/Uploading/)).toBeNull();
      expect(csrf).toBe("csrf-test");
      expect(unhandled).not.toHaveBeenCalled();
    } finally {
      process.off("unhandledRejection", unhandled);
    }
  });

  it("a non-JSON error body falls back to the HTTP status", async () => {
    server.use(
      http.get(INSTALLED, () => HttpResponse.json({ skills: [], total: 0 })),
      http.post(INSTALL, () => new HttpResponse("upstream exploded", { status: 502 })),
    );
    const { container } = render(<SkillManager />);
    await screen.findByText("No skills installed yet.");
    fillAndSubmit(container);
    expect(await screen.findByText("Upload failed: HTTP 502")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("e.g., my-awesome-skill")).not.toBeDisabled();
  });

  it("a refused uninstall shows the backend detail instead of nothing", async () => {
    server.use(
      http.get(INSTALLED, () => HttpResponse.json({
        skills: [{ skill_id: "demo", version: "1.0.0", boot_layer: "installed", verified: true }], total: 1,
      })),
      http.delete("/v1/console/skills-manager/skills/uninstall/:id/:ver", () =>
        HttpResponse.json({ detail: "owner or admin required" }, { status: 403 })),
    );
    vi.stubGlobal("confirm", () => true);
    render(<SkillManager />);
    await screen.findByText("demo");
    fireEvent.click(screen.getByRole("button", { name: "Uninstall" }));
    expect(await screen.findByText("owner or admin required")).toBeInTheDocument();
  });
});
