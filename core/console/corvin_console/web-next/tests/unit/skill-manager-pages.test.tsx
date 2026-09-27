/**
 * pages/skills/** and ConsolePluginUploadModal against the routes the backend
 * serves (routes/skill_manager.py, routes/plugin_upload.py). They used to call
 * /v1/skills/{upload,uploads,installed,install,status,available}, which no
 * router serves; MSW's onUnhandledRequest:'error' fails the test if any such
 * path is requested again. Every mutating call must carry X-CSRF-Token.
 */
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { http, HttpResponse } from "msw";
import { server } from "../fixtures/server";
import { installCsrfFetch, setCurrentCsrf } from "@/lib/csrf-fetch";
import { SkillManagerProvider } from "@/pages/skills/SkillManagerContext";
import { InstalledSkillsTab } from "@/pages/skills/tabs/InstalledSkillsTab";
import { SkillUploadTab } from "@/pages/skills/tabs/SkillUploadTab";
import { UploadTab } from "@/pages/skills/tabs/UploadTab";
import { InstallationProgress } from "@/pages/skills/components/InstallationProgress";
import { ConsolePluginUploadModal } from "@/components/ConsolePluginUploadModal";

interface Seen { method: string; path: string; csrf: string | null; form: Record<string, string> }
const seen: Seen[] = [];
async function record(request: Request) {
  const form: Record<string, string> = {};
  if ((request.headers.get("content-type") || "").includes("multipart/form-data")) {
    const fd = await request.clone().formData();
    fd.forEach((v, k) => { form[k] = typeof v === "string" ? v : `file:${(v as File).name}`; });
  }
  seen.push({ method: request.method, path: decodeURIComponent(new URL(request.url).pathname), csrf: request.headers.get("x-csrf-token"), form });
}

beforeAll(() => {
  installCsrfFetch();
  setCurrentCsrf("csrf-test");
});
afterEach(() => { cleanup(); seen.length = 0; vi.unstubAllGlobals(); });

const withProvider = (el: React.ReactElement) =>
  render(<MemoryRouter><SkillManagerProvider>{el}<InstallationProgress /></SkillManagerProvider></MemoryRouter>);

const zip = () => new File([new Uint8Array([80, 75, 3, 4])], "pkg.zip", { type: "application/zip" });

describe("Installed skills tab", () => {
  it("lists from the skills-manager route and uninstalls via DELETE with CSRF", async () => {
    let skills = [{ skill_id: "demo", version: "1.0.0", boot_layer: "installed", verified: true }];
    server.use(
      http.get("/v1/console/skills-manager/skills/installed", () => HttpResponse.json({ skills, total: skills.length })),
      http.delete("/v1/console/skills-manager/skills/uninstall/:id/:ver", async ({ request }) => {
        await record(request);
        skills = [];
        return HttpResponse.json({ success: true, message: "Uninstalled demo@1.0.0" });
      }),
    );
    vi.stubGlobal("confirm", () => true);
    withProvider(<InstalledSkillsTab canUninstall />);
    expect(await screen.findByText("demo")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Uninstall/ }));
    await screen.findByText("No skills installed yet.");
    expect(seen).toEqual([
      { method: "DELETE", path: "/v1/console/skills-manager/skills/uninstall/demo/1.0.0", csrf: "csrf-test", form: {} },
    ]);
  });
});

describe("Skill upload tab", () => {
  it("installs a ZIP with skill_id + version as multipart, with CSRF, and shows the server's refusal", async () => {
    server.use(
      http.post("/v1/console/skills-manager/skills/install", async ({ request }) => {
        await record(request);
        return HttpResponse.json({ detail: "signature mismatch" }, { status: 400 });
      }),
    );
    const { container } = withProvider(<SkillUploadTab />);
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [zip()] } });
    fireEvent.change(screen.getByPlaceholderText("my-skill"), { target: { value: "demo" } });
    fireEvent.change(screen.getByPlaceholderText("1.0.0"), { target: { value: "2.0.0" } });
    fireEvent.click(screen.getByRole("button", { name: /Upload & Install/ }));
    await screen.findByText(/demo: signature mismatch/);
    expect(seen).toEqual([
      { method: "POST", path: "/v1/console/skills-manager/skills/install", csrf: "csrf-test",
        form: { file: "file:pkg.zip", skill_id: "demo", version: "2.0.0" } },
    ]);
  });
});

describe("Plugin upload staging", () => {
  it("modal POSTs to /plugin-uploads with CSRF and renders a structured 400 detail readably", async () => {
    server.use(
      http.post("/v1/console/plugin-uploads", async ({ request }) => {
        await record(request);
        return HttpResponse.json(
          { detail: { message: "invalid plugin package", validation_errors: ["plugin.json missing"] } },
          { status: 400 },
        );
      }),
    );
    const { container } = render(<ConsolePluginUploadModal isOpen onClose={() => {}} onUploadComplete={() => {}} />);
    fireEvent.change(container.querySelector('input[type="file"]') as HTMLInputElement, { target: { files: [zip()] } });
    fireEvent.click(screen.getByRole("button", { name: "Upload" }));
    await screen.findByText("invalid plugin package: plugin.json missing");
    expect(seen[0]).toMatchObject({ method: "POST", path: "/v1/console/plugin-uploads", csrf: "csrf-test", form: { file: "file:pkg.zip" } });
  });

  it("lists staged uploads and approves through /plugin-uploads/{id}/approve with CSRF", async () => {
    let uploads = [{ upload_id: "abc123", file_name: "pkg.zip", file_size: 2048, upload_timestamp: "t", status: "pending_approval", validation_errors: [], manifest: {} }];
    server.use(
      http.get("/v1/console/plugin-uploads", () => HttpResponse.json({ uploads, count: uploads.length })),
      http.post("/v1/console/plugin-uploads/:id/approve", async ({ request }) => {
        await record(request);
        uploads = [];
        return HttpResponse.json({ status: "approved", upload_id: "abc123", activated: false });
      }),
    );
    render(<UploadTab />);
    expect(await screen.findByText("pkg.zip")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(seen).toHaveLength(1));
    expect(seen[0]).toMatchObject({ method: "POST", path: "/v1/console/plugin-uploads/abc123/approve", csrf: "csrf-test" });
    await screen.findByText("No pending uploads");
  });
});
