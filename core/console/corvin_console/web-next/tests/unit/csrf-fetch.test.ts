import { afterEach, describe, expect, it, vi } from "vitest";
import { installCsrfFetch, setCurrentCsrf, shouldAttachCsrf } from "@/lib/csrf-fetch";

const ORIGIN = "http://127.0.0.1:8765";

describe("shouldAttachCsrf", () => {
  it("attaches to same-origin mutating API calls only", () => {
    expect(shouldAttachCsrf("/v1/console/forge/skills/x/enable", "POST", ORIGIN)).toBe(true);
    expect(shouldAttachCsrf(`${ORIGIN}/v1/console/datahub/1`, "delete", ORIGIN)).toBe(true);
    expect(shouldAttachCsrf("/v1/console/profile", "GET", ORIGIN)).toBe(false);
    expect(shouldAttachCsrf("https://evil.example/v1/console/x", "POST", ORIGIN)).toBe(false);
    expect(shouldAttachCsrf("/console/assets/x.js", "POST", ORIGIN)).toBe(false);
  });
});

describe("installCsrfFetch", () => {
  afterEach(() => setCurrentCsrf(null));

  it("adds the token to raw fetch() mutations and never overrides an explicit one", async () => {
    const seen: Headers[] = [];
    Object.defineProperty(window, "fetch", {
      configurable: true,
      writable: true,
      value: vi.fn(async (_i: RequestInfo | URL, init?: RequestInit) => {
        seen.push(new Headers(init?.headers));
        return new Response("{}");
      }),
    });
    installCsrfFetch();
    setCurrentCsrf("tok-1");

    await window.fetch("/v1/console/forge/tools/t/enable", { method: "POST" });
    await window.fetch("/v1/console/profile", { method: "PUT", headers: { "X-CSRF-Token": "explicit" } });
    await window.fetch("/v1/console/voice/status");
    await window.fetch("https://other.example/v1/console/x", { method: "POST" });

    expect(seen[0].get("X-CSRF-Token")).toBe("tok-1");
    expect(seen[1].get("X-CSRF-Token")).toBe("explicit");
    expect(seen[2].get("X-CSRF-Token")).toBeNull();
    expect(seen[3].get("X-CSRF-Token")).toBeNull();
  });
});
