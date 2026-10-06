/**
 * ForgeBundlesPanel against the paths the backend serves
 * (routes/forge_bundle_routes.py: relative "/forge-bundles/..." under the
 * console's "/v1/console"). MSW runs with onUnhandledRequest: 'error', so a
 * wrong path fails the test instead of rendering an empty state. The payload
 * shapes asserted here are the ones the backend's pydantic models accept —
 * the previous UI sent FormData with an "artifacts" field the backend never read.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "../fixtures/server";

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({
    session: { tenant_id: "_default", csrf_token: "csrf-test", tier: "owner" },
    loading: false, refresh: vi.fn(), logout: vi.fn(),
  }),
}));

import ForgeBundlesPanel from "@/components/forge/ForgeBundlesPanel";

const B = "/v1/console/forge-bundles";
interface Seen { method: string; path: string; csrf: string | null; body: unknown }
const seen: Seen[] = [];

async function record(request: Request) {
  let body: unknown = null;
  try { body = await request.clone().json(); } catch { /* multipart or empty */ }
  seen.push({ method: request.method, path: new URL(request.url).pathname, csrf: request.headers.get("x-csrf-token"), body });
}

const QUEUE = {
  items: [{
    quarantine_id: "a".repeat(32), kind: "tool", tool_id: "csv.count", version: "0.2.0",
    bundle_id: "acme", bundle_version: "2.0.0", staged_at: "2026-10-06T12:00:00Z",
    runtime: "python", description: "count", impl_sha256: "0".repeat(64), origin_verified: false,
    requirements: ["numpy>=1.26"], secrets: ["openai_api_key"],
  }],
  count: 1,
};

function base(queue = QUEUE) {
  server.use(
    http.get(`${B}/exportable`, () => HttpResponse.json({
      skills: [{ id: "summarize", version: "1.0.0", description: "" }],
      tools: [{ id: "csv.count", description: "", runtime: "python" }],
      layers: [{ id: "acme.l34", version: "1.0.0", status: "accepted" }],
      plugins: [],
    })),
    http.get(`${B}/quarantine`, () => HttpResponse.json(queue)),
  );
}

function wrap() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={qc}><ForgeBundlesPanel /></QueryClientProvider>);
}

const realBlobUrls = { createObjectURL: URL.createObjectURL, revokeObjectURL: URL.revokeObjectURL };
afterEach(() => { cleanup(); seen.length = 0; Object.assign(URL, realBlobUrls); vi.restoreAllMocks(); });

describe("ForgeBundlesPanel", () => {
  it("exports a JSON selection with real per-artifact versions and the CSRF header", async () => {
    base();
    server.use(http.post(`${B}/export`, async ({ request }) => {
      await record(request);
      return new HttpResponse(new Blob(["PK"]), { headers: { "Content-Type": "application/zip" } });
    }));
    const createObjectURL = vi.fn(() => "blob:x");
    Object.assign(URL, { createObjectURL, revokeObjectURL: vi.fn() });
    // A real click would navigate jsdom to blob:x and break every later relative fetch.
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});

    wrap();
    fireEvent.click(await screen.findByLabelText("select skill summarize"));
    fireEvent.click(screen.getByLabelText("select tool csv.count"));
    fireEvent.click(screen.getByLabelText("select layer acme.l34"));
    fireEvent.change(screen.getByLabelText("Bundle id"), { target: { value: "acme" } });
    fireEvent.change(screen.getByLabelText("Bundle version"), { target: { value: "2.0.0" } });
    fireEvent.click(screen.getByRole("button", { name: /Export 3 artifacts/ }));

    await screen.findByText("Bundle downloaded.");
    const call = seen.find((s) => s.path === `${B}/export`)!;
    expect(call.csrf).toBe("csrf-test");
    expect(call.body).toEqual({
      bundle_id: "acme", bundle_version: "2.0.0",
      selections: [
        { kind: "skill", id: "summarize", version: "1.0.0" },
        { kind: "tool", id: "csv.count", version: "2.0.0" },
        { kind: "layer", id: "acme.l34", version: "1.0.0" },
      ],
    });
    expect(createObjectURL).toHaveBeenCalled();
    expect(click).toHaveBeenCalledOnce();
  });

  it("validates first, shows unverified origin, then imports and lists outcomes", async () => {
    base({ items: [], count: 0 });
    server.use(
      http.post(`${B}/validate`, async ({ request }) => {
        await record(request);
        return HttpResponse.json({
          valid: true, bundle_id: "acme", bundle_version: "2.0.0", description: null, created_at: null,
          artifacts: [{ kind: "tool", id: "csv.count", version: "0.2.0", file_count: 2, requires: [] }],
          total_uncompressed_bytes: 10, origin_verified: false,
        });
      }),
      http.post(`${B}/import`, async ({ request }) => {
        await record(request);
        return HttpResponse.json({
          bundle_id: "acme", bundle_version: "2.0.0", artifact_count: 1, failed_count: 0,
          outcomes: [{ kind: "tool", id: "csv.count", version: "0.2.0", status: "quarantined", detail: "a".repeat(32) }],
          origin_verified: false,
        });
      }),
    );
    wrap();
    const file = new File(["PK"], "acme.zip", { type: "application/zip" });
    fireEvent.change(screen.getByLabelText("bundle file"), { target: { files: [file] } });

    expect(await screen.findByText("Unverified origin")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Import 1 artifact/ }));
    expect(await screen.findByText("Awaiting review below")).toBeInTheDocument();
    expect(seen.map((s) => s.path)).toEqual([`${B}/validate`, `${B}/import`]);
    expect(seen.every((s) => s.csrf === "csrf-test")).toBe(true);
  });

  it("shows the rejecting stage and offers no import button", async () => {
    base({ items: [], count: 0 });
    server.use(http.post(`${B}/validate`, () => HttpResponse.json({
      valid: false, stage: "secrets", reason: "credential-shaped content (aws_access_key)", origin_verified: false,
    })));
    wrap();
    fireEvent.change(screen.getByLabelText("bundle file"), { target: { files: [new File(["x"], "b.zip")] } });
    expect(await screen.findByText(/Rejected at stage “secrets”/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Import/ })).toBeNull();
  });

  it("accepts a quarantined tool through the accept route", async () => {
    base();
    server.use(http.post(`${B}/quarantine/${"a".repeat(32)}/accept`, async ({ request }) => {
      await record(request);
      return HttpResponse.json({ status: "accepted", tool_id: "csv.count" });
    }));
    wrap();
    fireEvent.click(await screen.findByRole("button", { name: /Accept/ }));
    await waitFor(() => expect(seen.some((s) => s.path.endsWith("/accept"))).toBe(true));
    expect(seen.find((s) => s.path.endsWith("/accept"))!.csrf).toBe("csrf-test");
  });

  it("C1: a late validation answer for an earlier file never replaces the current preview", async () => {
    base({ items: [], count: 0 });
    let releaseA: () => void = () => {};
    server.use(http.post(`${B}/validate`, async ({ request }) => {
      const form = await request.formData();
      const name = (form.get("file") as File).name;
      if (name === "a.zip") await new Promise<void>((r) => { releaseA = r; });
      return HttpResponse.json({
        valid: true, bundle_id: name === "a.zip" ? "bundle-a" : "bundle-b", bundle_version: "1.0.0",
        description: null, created_at: null, artifacts: [], total_uncompressed_bytes: 1, origin_verified: false,
      });
    }));
    wrap();
    const input = screen.getByLabelText("bundle file");
    fireEvent.change(input, { target: { files: [new File(["a"], "a.zip")] } });
    fireEvent.change(input, { target: { files: [new File(["b"], "b.zip")] } });
    expect(await screen.findByText("bundle-b@1.0.0")).toBeInTheDocument();
    releaseA();
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByText("bundle-a@1.0.0")).toBeNull();
    expect(screen.getByText("bundle-b@1.0.0")).toBeInTheDocument();
  });

  it("shows what landed when the import stops mid-way (503 with outcomes)", async () => {
    base({ items: [], count: 0 });
    server.use(
      http.post(`${B}/validate`, () => HttpResponse.json({
        valid: true, bundle_id: "acme", bundle_version: "2.0.0", description: null, created_at: null,
        artifacts: [{ kind: "tool", id: "csv.count", version: "0.2.0", file_count: 2, requires: [] },
                    { kind: "layer", id: "acme.l34", version: "1.0.0", file_count: 1, requires: [] }],
        total_uncompressed_bytes: 10, origin_verified: false,
      })),
      http.post(`${B}/import`, () => HttpResponse.json({ detail: {
        message: "audit chain unavailable; the import stopped — see which artifacts landed",
        bundle_id: "acme", bundle_version: "2.0.0", artifact_count: 2, failed_count: 0, origin_verified: false,
        outcomes: [{ kind: "tool", id: "csv.count", version: "0.2.0", status: "quarantined", detail: "a".repeat(32) },
                   { kind: "layer", id: "acme.l34", version: "1.0.0", status: "not_attempted", detail: "" }],
      } }, { status: 503 })),
    );
    wrap();
    fireEvent.change(screen.getByLabelText("bundle file"), { target: { files: [new File(["x"], "b.zip")] } });
    fireEvent.click(await screen.findByRole("button", { name: /Import 2 artifacts/ }));
    expect(await screen.findByText("Not attempted")).toBeInTheDocument();
    expect(screen.getByText("Awaiting review below")).toBeInTheDocument();
    expect(screen.getByText(/Import stopped/)).toBeInTheDocument();
  });

  it("shows the packages and secrets an imported tool would get before Accept", async () => {
    base();
    wrap();
    expect(await screen.findByText("Installs packages: numpy>=1.26")).toBeInTheDocument();
    expect(screen.getByText("Requests secrets: openai_api_key")).toBeInTheDocument();
  });
});
