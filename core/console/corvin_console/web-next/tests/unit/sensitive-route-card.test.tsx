/**
 * Sensitive-data card (T-0222, ADR-2268) — the real component against MSW.
 * With no admissible provider it shows the honest empty state and no control; with one it
 * saves through the CSRF-carrying PUT; a 422 shows fixed copy, never the server's sentence.
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

import { SensitiveRouteCard } from "@/pages/models/components/sensitive-route-card";

const ELIGIBLE = {
  provider: "acme_llm", label: "Acme LLM", locality: "eu_cloud", network_egress: "external",
  region: "", resource: "", admitted_classes: ["INTERNAL", "CONFIDENTIAL"],
};

function routeBody(over: Record<string, unknown> = {}) {
  return { enabled: false, provider: "", model_id: "", eligible_providers: [ELIGIBLE], ...over };
}

function renderCard() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <SensitiveRouteCard />
    </QueryClientProvider>,
  );
}

afterEach(() => cleanup());

describe("Sensitive data card", () => {
  it("shows the honest empty state and no save control when nothing is admissible", async () => {
    server.use(http.get("/v1/console/settings/engine/sensitive-route",
      () => HttpResponse.json(routeBody({ eligible_providers: [] }))));
    renderCard();
    expect(await screen.findByTestId("sensitive-route-empty")).toBeTruthy();
    expect(screen.queryByTestId("sensitive-route-save")).toBeNull();
    expect(screen.queryByRole("combobox")).toBeNull();
  });

  it("lists only declared providers and saves through the CSRF-carrying PUT", async () => {
    let body: unknown = null;
    let csrf: string | null = null;
    server.use(
      http.get("/v1/console/settings/engine/sensitive-route", () => HttpResponse.json(routeBody())),
      http.put("/v1/console/settings/engine/sensitive-route", async ({ request }) => {
        body = await request.json();
        csrf = request.headers.get("x-csrf-token");
        return HttpResponse.json(routeBody({ enabled: true, provider: "acme_llm", model_id: "vendor/Model-1" }));
      }),
    );
    renderCard();
    const enable = await screen.findByLabelText(/Run sensitive turns/);
    fireEvent.click(enable);
    fireEvent.change(screen.getByLabelText("Provider"), { target: { value: "acme_llm" } });
    fireEvent.change(screen.getByLabelText("Model id"), { target: { value: "vendor/Model-1" } });
    fireEvent.click(screen.getByTestId("sensitive-route-save"));
    await waitFor(() => expect(body).not.toBeNull());
    expect(body).toEqual({ enabled: true, provider: "acme_llm", model_id: "vendor/Model-1" });
    expect(csrf).toBe("csrf-test");
  });

  it("refuses to save an illegal model id without sending anything", async () => {
    let calls = 0;
    server.use(
      http.get("/v1/console/settings/engine/sensitive-route", () => HttpResponse.json(routeBody())),
      http.put("/v1/console/settings/engine/sensitive-route", () => { calls += 1; return HttpResponse.json({}); }),
    );
    renderCard();
    fireEvent.click(await screen.findByLabelText(/Run sensitive turns/));
    fireEvent.change(screen.getByLabelText("Provider"), { target: { value: "acme_llm" } });
    fireEvent.change(screen.getByLabelText("Model id"), { target: { value: "vendor/Model;rm" } });
    expect((screen.getByTestId("sensitive-route-save") as HTMLButtonElement).disabled).toBe(true);
    expect(calls).toBe(0);
  });

  it("shows fixed copy on a 422, never the server's sentence", async () => {
    server.use(
      http.get("/v1/console/settings/engine/sensitive-route", () => HttpResponse.json(routeBody())),
      http.put("/v1/console/settings/engine/sensitive-route", () =>
        HttpResponse.json({ detail: { code: "target_not_eligible", message: "SECRET-SERVER-SENTENCE" } }, { status: 422 })),
    );
    renderCard();
    fireEvent.click(await screen.findByLabelText(/Run sensitive turns/));
    fireEvent.change(screen.getByLabelText("Provider"), { target: { value: "acme_llm" } });
    fireEvent.change(screen.getByLabelText("Model id"), { target: { value: "vendor/Model-1" } });
    fireEvent.click(screen.getByTestId("sensitive-route-save"));
    expect(await screen.findByText(/not admissible/)).toBeTruthy();
    expect(screen.queryByText(/SECRET-SERVER-SENTENCE/)).toBeNull();
  });
});
