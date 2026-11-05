import { afterEach, describe, expect, it, vi } from "vitest";
import { ttsBlob } from "@/lib/api/memory";

const csrf403 = () =>
  new Response(JSON.stringify({ detail: "invalid CSRF token" }), { status: 403 });

afterEach(() => vi.unstubAllGlobals());

describe("ttsBlob stale CSRF token", () => {
  it("retries once with the token from whoami and succeeds", async () => {
    const seen: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (String(url).includes("/auth/whoami")) {
        return new Response(JSON.stringify({ csrf_token: "fresh" }), { status: 200 });
      }
      const tok = new Headers(init?.headers).get("X-CSRF-Token") ?? "";
      seen.push(tok);
      return tok === "fresh" ? new Response("audio", { status: 200 }) : csrf403();
    }));
    const blob = await ttsBlob("hi", "de", "stale");
    expect(seen).toEqual(["stale", "fresh"]);
    expect(await blob.text()).toBe("audio");
  });

  it("still rejects when the server keeps refusing (no bypass)", async () => {
    const f = vi.fn(async (url: string) =>
      String(url).includes("/auth/whoami")
        ? new Response(JSON.stringify({ csrf_token: "also-bad" }), { status: 200 })
        : csrf403());
    vi.stubGlobal("fetch", f);
    await expect(ttsBlob("hi", "de", "stale")).rejects.toMatchObject({ status: 403 });
    // original + exactly one retry + one whoami
    expect(f).toHaveBeenCalledTimes(3);
  });
});
