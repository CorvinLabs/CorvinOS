/**
 * The Projects page as the registry serves it: real page, real encodings, the network stubbed at the
 * boundary (msw). A project folder that is gone is shown as such, never dropped; the empty state names the
 * missing plugin when the catalogue is unavailable; nothing is sample data.
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "../fixtures/server";
import ProjectsPage from "@/pages/projects";
import { vi } from "vitest";

vi.mock("@/lib/auth", () => ({ useAuth: () => ({ session: { csrf_token: "test-csrf" } }) }));

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><MemoryRouter><ProjectsPage /></MemoryRouter></QueryClientProvider>);
}
afterEach(() => cleanup());

const templates = (available: boolean, reason?: string) => http.get("/v1/console/projects/templates", () =>
  HttpResponse.json({ available, reason, templates: [], features: [] }));

describe("Projects page", () => {
  it("lists projects with state, readable feature chips, what is off, template and folder", async () => {
    server.use(templates(true),
      http.get("/v1/console/projects", () => HttpResponse.json({
        projects_root: "/home/u/projects",
        projects: [
          { id: "plumbline", path: "/home/u/projects/plumbline", template: "video-engineering@1", state: "ok", title: "Plumbline",
            docs_mode: "nested", active: ["core", "kb", "videos"], features: { core: true, kb: true, videos: true, acs: false } },
          { id: "old", path: "/home/u/projects/old", template: "knowledge-only@1", state: "missing", reason: "project.yaml is missing or unreadable" },
        ],
      })));
    renderPage();
    const cards = await screen.findAllByTestId("project-card");
    expect(cards).toHaveLength(2);
    expect(cards[0]).toHaveAttribute("data-project-id", "old");          // the one needing attention first
    expect(within(cards[0]).getByTestId("project-state")).toHaveTextContent("Directory missing");
    expect(within(cards[0]).getByTestId("project-reason")).toHaveTextContent("project.yaml is missing");
    const good = cards[1];
    expect(within(good).getByTestId("project-title")).toHaveTextContent("Plumbline");
    expect(within(good).getByTestId("project-state")).toHaveTextContent("Ready");
    expect(within(good).getByTestId("project-features")).toHaveTextContent("Knowledge base");
    expect(within(good).getByTestId("project-features")).toHaveTextContent("Videos");
    expect(within(good).getByTestId("project-features")).not.toHaveTextContent(/core|Project$/);
    expect(within(good).getByTestId("project-off")).toHaveTextContent("Off: Agentic compute");
    expect(within(good).getByTestId("project-path")).toHaveTextContent("/home/u/projects/plumbline");
    expect(screen.getByTestId("projects-root")).toHaveTextContent("/home/u/projects");
    expect(screen.queryByTestId("projects-empty")).toBeNull();
  });

  it("shows the empty state, with the folder explanation, when there are no projects", async () => {
    server.use(templates(true), http.get("/v1/console/projects", () => HttpResponse.json({ projects_root: "/home/u/projects", projects: [] })));
    renderPage();
    expect(await screen.findByTestId("projects-empty-headline")).toHaveTextContent("No projects yet");
    expect(screen.getByTestId("projects-empty-body")).toHaveTextContent(/never deletes a file/);
    expect(screen.queryByRole("link", { name: /Marketplace/ })).toBeNull();      // nothing to install: no link
    expect(screen.queryAllByTestId("project-card")).toHaveLength(0);
  });

  it("names the missing plugin and links to the Marketplace when the catalogue is unavailable", async () => {
    server.use(templates(false, "the project templates plugin is absent for this tenant - enable it in the Marketplace"),
      http.get("/v1/console/projects", () => HttpResponse.json({ projects_root: "/p", projects: [] })));
    renderPage();
    expect(await screen.findByTestId("projects-empty-headline")).toHaveTextContent("Project templates are not enabled");
    expect(screen.getByTestId("projects-empty-body")).toHaveTextContent(/enable it in the Marketplace/);
    expect(screen.getByRole("link", { name: /Open the Marketplace/ })).toHaveAttribute("href", "/app/marketplace");
  });

  it("reports a load failure instead of an empty list", async () => {
    server.use(templates(true), http.get("/v1/console/projects", () => HttpResponse.json({ detail: "boom" }, { status: 500 })));
    renderPage();
    expect(await screen.findByTestId("projects-error")).toBeInTheDocument();
    expect(screen.queryByTestId("projects-empty")).toBeNull();
  });
});
