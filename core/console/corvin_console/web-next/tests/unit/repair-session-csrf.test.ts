/**
 * Auto-Repair is a POST: the backend (`routes/aco.py::apply_repair`, `require_csrf`)
 * answers 403 without the X-CSRF-Token header. `repairSession` used to take no token
 * and sent none, so every click on the audit panel's Auto-Repair button failed.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { repairSession } from "@/lib/api/sessions";

afterEach(() => vi.unstubAllGlobals());

describe("repairSession", () => {
  it("sends the CSRF token and the dry_run flag on the POST", async () => {
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify({ ok: true, sid: "s1", dry_run: false }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    await repairSession("s1", "tok-123", true);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toContain("/chat/sessions/s1/aco/repair");
    expect(init.method).toBe("POST");
    expect((init.headers as Record<string, string>)["X-CSRF-Token"]).toBe("tok-123");
    expect(JSON.parse(init.body as string)).toEqual({ dry_run: true });
  });
});
