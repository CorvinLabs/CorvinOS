/**
 * Session expiry must not freeze the console (2026-09-23 regression).
 *
 * After ABSOLUTE_TIMEOUT_S (8 h) whoami answers 401 while react-query still
 * holds the previous whoami in `data`. AuthProvider used to read `data` only:
 * status stayed "authenticated", nothing renewed, nothing redirected, every
 * panel kept polling into 401s. Now a 401 on whoami is "expired": the session
 * is re-opened in place (fetch to local-login) and every query refetches; if
 * that is refused, status becomes "anonymous" so RequireAuth redirects.
 */
import { afterEach, describe, expect, it } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "../fixtures/server";
import { AuthProvider, useAuth } from "@/lib/auth";

const WHO = { tenant_id: "_default", tier: "owner", csrf_token: "c1", sid_fingerprint: "f" };

const history: string[] = [];
function Probe() {
  const { status } = useAuth();
  history.push(status);
  const data = useQuery({ queryKey: ["panel"], queryFn: () => fetch("/v1/console/panel-data").then((r) => r.json()) });
  return <div><span data-testid="status">{status}</span><span data-testid="value">{data.data?.v ?? "-"}</span></div>;
}

function renderIt(qc: QueryClient) {
  return render(<QueryClientProvider client={qc}><AuthProvider><Probe /></AuthProvider></QueryClientProvider>);
}
afterEach(() => cleanup());

describe("session expiry", () => {
  it("re-opens the session in place and refetches everything", async () => {
    let alive = true; let logins = 0; let v = 1;
    server.use(
      http.get("/v1/console/auth/whoami", () => alive ? HttpResponse.json(WHO) : new HttpResponse(null, { status: 401 })),
      http.get("/v1/console/auth/local-login", () => { logins++; alive = true; return new HttpResponse(null, { status: 302, headers: { Location: "/console/" } }); }),
      http.get("/v1/console/panel-data", () => HttpResponse.json({ v })),
    );
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    renderIt(qc);
    await waitFor(() => expect(screen.getByTestId("status").textContent).toBe("authenticated"));
    await waitFor(() => expect(screen.getByTestId("value").textContent).toBe("1"));

    history.length = 0;
    alive = false; v = 2;                     // the 8 h expiry, and fresh data meanwhile
    await act(async () => { await qc.invalidateQueries({ queryKey: ["auth"] }); });
    await waitFor(() => expect(logins).toBe(1));
    await waitFor(() => expect(screen.getByTestId("value").textContent).toBe("2"));
    expect(screen.getByTestId("status").textContent).toBe("authenticated"); // never bounced to login
    // Not even for one render: a single "anonymous" makes RequireAuth navigate
    // to the login page, which reloads the tab (the E2E caught exactly this).
    expect(history).not.toContain("anonymous");
  });

  it("falls back to anonymous (→ login page) when renewal is refused", async () => {
    server.use(
      http.get("/v1/console/auth/whoami", () => new HttpResponse(null, { status: 401 })),
      http.get("/v1/console/auth/local-login", () => new HttpResponse(null, { status: 403 })),
      http.get("/v1/console/panel-data", () => HttpResponse.json({ v: 1 })),
    );
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    qc.setQueryData(["auth", "whoami"], WHO);  // stale cached whoami — the trap
    renderIt(qc);
    // In the app a panel's 401 triggers this (setOn401Handler → invalidate auth).
    await act(async () => { await qc.invalidateQueries({ queryKey: ["auth"] }); });
    await waitFor(() => expect(screen.getByTestId("status").textContent).toBe("anonymous"));
  });
});
