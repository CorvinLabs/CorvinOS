/**
 * Panel data requests (protocol.ts "Data requests", PanelHost).
 *
 * A sandboxed panel has an opaque origin and cannot call the console API. It
 * asks its host with `corvin:panel:fetch`; the host reads with its OWN session,
 * and only a path that is (1) well-formed, (2) on the console-wide
 * PANEL_READABLE_PATHS and (3) declared in the panel's manifest. These tests
 * pin the allowlist - including what is refused - and the size limit.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { http, HttpResponse } from "msw";
import { server } from "../fixtures/server";
import PanelHost, { PANEL_SANDBOX, generatedPanelId, readPanelData } from "@/panels/PanelHost";
import {
  PANEL_DATA_MAX_BYTES,
  PANEL_DATA_MAX_DECLARED,
  PANEL_DATA_MAX_IN_FLIGHT,
  PANEL_READABLE_PATHS,
  decidePanelDataRequest,
  declaredDataPathsFromHtml,
  grantablePanelPaths,
  isPanelFetch,
  isPanelToHost,
  normalisePanelDataPath,
} from "@/panels/protocol";

afterEach(() => cleanup());

const BASE = "/v1/console";
const DECLARED = ["/sessions", "/vibe-engineering/traces"];

describe("normalisePanelDataPath", () => {
  it("positive control: a plain API path, with and without a query", () => {
    expect(normalisePanelDataPath("/sessions")).toEqual({ pathname: "/sessions", search: "" });
    expect(normalisePanelDataPath("/vibe-engineering/traces?limit=50&x=a,b"))
      .toEqual({ pathname: "/vibe-engineering/traces", search: "?limit=50&x=a,b" });
  });

  it.each([
    ["an absolute URL", "https://evil.example/sessions"],
    ["a protocol-relative URL", "//evil.example/sessions"],
    ["no leading slash", "sessions"],
    ["dot segments", "/sessions/../settings"],
    ["a single dot segment", "/./sessions"],
    ["encoded dot segments", "/sessions/%2e%2e/settings"],
    ["an encoded slash", "/sessions%2fsettings"],
    ["a backslash", "/sessions\\settings"],
    ["a doubled slash", "/sessions//settings"],
    ["a trailing slash", "/sessions/"],
    ["a fragment", "/sessions#x"],
    ["a control character", "/sessions\n/settings"],
    ["a space", "/sessions settings"],
    ["a second question mark", "/sessions?a=1?b=2"],
    ["an encoded query", "/sessions?next=%2Fsettings"],
    ["a tenant parameter", "/sessions?tenant_id=other"],
    ["a tenant parameter in capitals", "/sessions?limit=1&Tenant_ID=other"],
    ["the short tenant key", "/sessions?tid=other"],
    ["a path parameter with a dot", "/panels/x/index.html"],
    ["an empty string", ""],
    ["an over-long path", "/" + "a".repeat(600)],
  ])("refuses %s", (_name, path) => {
    expect(normalisePanelDataPath(path)).toBeNull();
  });

  it.each([null, undefined, 7, {}, ["/sessions"]])("refuses the non-string %s", (value) => {
    expect(normalisePanelDataPath(value)).toBeNull();
  });
});

describe("decidePanelDataRequest (the allowlist)", () => {
  it("positive control: a declared, readable path is allowed with the host's base", () => {
    expect(decidePanelDataRequest("/sessions", DECLARED, BASE))
      .toEqual({ ok: true, url: "/v1/console/sessions", pathname: "/sessions" });
    expect(decidePanelDataRequest("/vibe-engineering/traces?limit=5", DECLARED, BASE))
      .toMatchObject({ ok: true, url: "/v1/console/vibe-engineering/traces?limit=5" });
  });

  it("refuses a readable path the panel did not declare", () => {
    expect(PANEL_READABLE_PATHS).toContain("/cost/summary"); // positive control
    expect(decidePanelDataRequest("/cost/summary", DECLARED, BASE))
      .toEqual({ ok: false, reason: "path is not declared by this panel" });
    expect(decidePanelDataRequest("/sessions", [], BASE)).toMatchObject({ ok: false });
    expect(decidePanelDataRequest("/sessions", null, BASE)).toMatchObject({ ok: false });
  });

  it.each([
    "/settings", "/settings/engine", "/auth/session", "/api-keys", "/license", "/panels",
    "/vibe-engineering/audit", "/vibe-engineering/config", "/cost/guardrails", "/chat/sessions",
    "/licensing/a2a/crl", "/sessions/abc", "/vibe-engineering/traces/1",
  ])("refuses %s even when the panel declares it", (path) => {
    expect(decidePanelDataRequest(path, [path, ...DECLARED], BASE))
      .toEqual({ ok: false, reason: "path is not readable by panels" });
  });

  it("refuses a malformed path before looking at any list", () => {
    expect(decidePanelDataRequest("/sessions/../settings", ["/sessions/../settings"], BASE))
      .toEqual({ ok: false, reason: "not a valid data path" });
  });

  it("no readable path is a settings, auth, key, licence or write surface", () => {
    for (const p of PANEL_READABLE_PATHS) {
      expect(normalisePanelDataPath(p)).toEqual({ pathname: p, search: "" });
      expect(p).not.toMatch(/setting|auth|key|token|secret|licen[cs]|credential|config|audit|admin|panel/i);
    }
    expect(new Set(PANEL_READABLE_PATHS).size).toBe(PANEL_READABLE_PATHS.length);
  });

  it("a declaration is reduced to readable paths, without queries, capped", () => {
    expect(grantablePanelPaths(["/sessions", "/sessions", "/settings", "/sessions?x=1", 7, "../x"]))
      .toEqual(["/sessions"]);
    expect(grantablePanelPaths(null)).toEqual([]);
    const many = [...PANEL_READABLE_PATHS, ...PANEL_READABLE_PATHS];
    expect(grantablePanelPaths(many).length).toBeLessThanOrEqual(PANEL_DATA_MAX_DECLARED);
  });
});

describe("declaredDataPathsFromHtml", () => {
  it("reads the meta tag, either attribute order, either quote", () => {
    expect(declaredDataPathsFromHtml('<meta name="corvin-panel-data" content="/sessions /cost/summary">'))
      .toEqual(["/sessions", "/cost/summary"]);
    expect(declaredDataPathsFromHtml("<META content='/sessions,/talent/score' NAME='corvin-panel-data'/>"))
      .toEqual(["/sessions", "/talent/score"]);
  });

  it("ignores other meta tags and text that only looks like one", () => {
    expect(declaredDataPathsFromHtml('<meta name="viewport" content="/sessions">')).toEqual([]);
    expect(declaredDataPathsFromHtml("<p>corvin-panel-data /sessions</p>")).toEqual([]);
    expect(declaredDataPathsFromHtml("")).toEqual([]);
  });
});

describe("the fetch envelope", () => {
  it("is a panel-to-host message with a bounded id", () => {
    const msg = { type: "corvin:panel:fetch", id: "1", path: "/sessions" };
    expect(isPanelToHost(msg)).toBe(true);
    expect(isPanelFetch(msg)).toBe(true);
    expect(isPanelFetch({ ...msg, id: "" })).toBe(false);
    expect(isPanelFetch({ ...msg, id: "x".repeat(65) })).toBe(false);
    expect(isPanelFetch({ ...msg, id: 1 })).toBe(false);
    expect(isPanelFetch({ ...msg, path: 1 })).toBe(false);
    expect(isPanelFetch({ type: "corvin:panel:ready", id: "1", path: "/sessions" })).toBe(false);
  });
});

describe("readPanelData (what the host does with a request)", () => {
  it("positive control: reads a declared path with the host's own credentials, GET only", async () => {
    const fetchImpl = vi.fn(async () => HttpResponse.json({ sessions: [1, 2] }));
    const out = await readPanelData("/sessions?limit=2", DECLARED, BASE, fetchImpl as unknown as typeof fetch);
    expect(out).toEqual({ ok: true, status: 200, data: { sessions: [1, 2] } });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    const [url, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/v1/console/sessions?limit=2");
    expect(init).toMatchObject({ method: "GET", credentials: "include", redirect: "error" });
    expect(init.body).toBeUndefined();
  });

  it.each([
    "/settings", "/cost/summary", "https://evil.example/x", "/sessions/../settings",
    "/sessions?tenant_id=other", "//evil.example",
  ])("never calls the API for %s", async (path) => {
    const fetchImpl = vi.fn(async () => HttpResponse.json({}));
    const out = await readPanelData(path, DECLARED, BASE, fetchImpl as unknown as typeof fetch);
    expect(out).toMatchObject({ ok: false, status: 403 });
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("does not forward the body of a failed call", async () => {
    const fetchImpl = vi.fn(async () => HttpResponse.json({ detail: "secret stack trace" }, { status: 500 }));
    const out = await readPanelData("/sessions", DECLARED, BASE, fetchImpl as unknown as typeof fetch);
    expect(out).toEqual({ ok: false, status: 500, error: "HTTP 500" });
    expect(JSON.stringify(out)).not.toContain("secret");
  });

  it("refuses a response over the size limit, by header and by actual length", async () => {
    const big = JSON.stringify({ pad: "x".repeat(PANEL_DATA_MAX_BYTES) });
    const byLength = vi.fn(async () => new Response(big, { headers: { "content-type": "application/json" } }));
    expect(await readPanelData("/sessions", DECLARED, BASE, byLength as unknown as typeof fetch))
      .toMatchObject({ ok: false, status: 413 });
    const byHeader = vi.fn(async () => new Response("{}", {
      headers: { "content-length": String(PANEL_DATA_MAX_BYTES + 1) } }));
    expect(await readPanelData("/sessions", DECLARED, BASE, byHeader as unknown as typeof fetch))
      .toMatchObject({ ok: false, status: 413 });
    // multi-byte text is measured in bytes, not characters
    const umlauts = JSON.stringify("ä".repeat(PANEL_DATA_MAX_BYTES / 2 + 10));
    expect(umlauts.length).toBeLessThan(PANEL_DATA_MAX_BYTES);
    const multi = vi.fn(async () => new Response(umlauts));
    expect(await readPanelData("/sessions", DECLARED, BASE, multi as unknown as typeof fetch))
      .toMatchObject({ ok: false, status: 413 });
    // just under the limit passes
    const fits = JSON.stringify({ pad: "x".repeat(PANEL_DATA_MAX_BYTES - 100) });
    const okFetch = vi.fn(async () => new Response(fits));
    expect(await readPanelData("/sessions", DECLARED, BASE, okFetch as unknown as typeof fetch))
      .toMatchObject({ ok: true, status: 200 });
  });

  it("refuses a non-JSON body and survives a network failure", async () => {
    const html = vi.fn(async () => new Response("<html>login</html>"));
    expect(await readPanelData("/sessions", DECLARED, BASE, html as unknown as typeof fetch))
      .toEqual({ ok: false, status: 415, error: "response is not JSON" });
    const down = vi.fn(async () => { throw new TypeError("network"); });
    expect(await readPanelData("/sessions", DECLARED, BASE, down as unknown as typeof fetch))
      .toEqual({ ok: false, status: 0, error: "request failed" });
  });
});

describe("generatedPanelId", () => {
  it("names only a generated panel's own index.html under the API base", () => {
    expect(generatedPanelId("/v1/console/panels/demo/index.html", BASE)).toBe("demo");
    expect(generatedPanelId("/v1/console/panels/demo/index.html?corvinTheme=dark", BASE)).toBe("demo");
    for (const src of [
      "https://panels.example.com/v1/console/panels/demo/index.html",
      "/v1/console/panels/demo/other.html", "/v1/console/panels/../x/index.html",
      "/v1/console/panels/Demo/index.html", "/external-panels/x/index.html", undefined,
    ]) {
      expect(generatedPanelId(src, BASE)).toBeNull();
    }
  });
});

// happy-dom does not load iframe pages: give the frame a stand-in window.
function stubFrameWindow(iframe: HTMLIFrameElement): Window {
  const win = { postMessage: () => {} } as unknown as Window;
  Object.defineProperty(iframe, "contentWindow", { configurable: true, get: () => win });
  return win;
}

function mount(props: Partial<React.ComponentProps<typeof PanelHost>>) {
  const { container } = render(
    <MemoryRouter>
      <PanelHost sandbox={PANEL_SANDBOX} theme="dark" {...props} />
    </MemoryRouter>,
  );
  const iframe = container.querySelector("iframe") as HTMLIFrameElement;
  const win = stubFrameWindow(iframe);
  const post = vi.spyOn(win, "postMessage");
  const send = (data: unknown, source: unknown = win) =>
    window.dispatchEvent(new MessageEvent("message", { source: source as Window, origin: "null", data }));
  const replies = () =>
    post.mock.calls.map(([m]) => m as { type: string; id?: string; ok?: boolean; status?: number })
      .filter((m) => m.type === "corvin:host:data");
  return { iframe, post, send, replies };
}

describe("PanelHost answers corvin:panel:fetch", () => {
  it("a generated panel: manifest paths are granted, announced in hello, and served", async () => {
    let manifestCalls = 0;
    const seen: string[] = [];
    server.use(
      http.get("/v1/console/panels/demo/manifest", () => {
        manifestCalls += 1;
        // "/settings" is declared by the panel but is not readable: never granted.
        return HttpResponse.json({ id: "demo", data_paths: ["/sessions", "/settings"] });
      }),
      http.get("/v1/console/sessions", ({ request }) => {
        seen.push(new URL(request.url).search);
        return HttpResponse.json({ sessions: [{ id: "s1" }] });
      }),
    );
    const { iframe, post, send, replies } = mount({ src: "/v1/console/panels/demo/index.html" });
    expect(iframe.getAttribute("sandbox")).toBe("allow-scripts"); // the sandbox is not widened

    // Asked before the manifest arrived: the request waits, it is not refused.
    send({ type: "corvin:panel:fetch", id: "a", path: "/sessions?limit=1" });
    await waitFor(() => expect(replies().some((r) => r.id === "a")).toBe(true));
    expect(replies().find((r) => r.id === "a"))
      .toEqual({ type: "corvin:host:data", id: "a", ok: true, status: 200, data: { sessions: [{ id: "s1" }] } });
    expect(seen).toEqual(["?limit=1"]);
    expect(manifestCalls).toBe(1);

    send({ type: "corvin:panel:fetch", id: "b", path: "/settings" });
    send({ type: "corvin:panel:fetch", id: "c", path: "/cost/summary" });
    await waitFor(() => expect(replies().filter((r) => r.id === "b" || r.id === "c")).toHaveLength(2));
    expect(replies().find((r) => r.id === "b")).toMatchObject({ ok: false, status: 403 });
    expect(replies().find((r) => r.id === "c")).toMatchObject({ ok: false, status: 403 });
    expect(seen).toEqual(["?limit=1"]); // nothing else reached the API

    send({ type: "corvin:panel:ready", protocolVersion: "1" });
    const hello = post.mock.calls.map(([m]) => m as { type: string; ctx?: { dataPaths?: string[] } })
      .find((m) => m.type === "corvin:host:hello");
    expect(hello?.ctx?.dataPaths).toEqual(["/sessions"]);
    // every reply went to the frame with the only targetOrigin an opaque frame has
    for (const [m, target] of post.mock.calls) {
      if ((m as { type: string }).type === "corvin:host:data") expect(target).toBe("*");
    }
  });

  it("a generated panel without a manifest, and an external panel, get nothing", async () => {
    server.use(http.get("/v1/console/panels/gone/manifest", () =>
      HttpResponse.json({ detail: "panel not found" }, { status: 404 })));
    const gone = mount({ src: "/v1/console/panels/gone/index.html" });
    gone.send({ type: "corvin:panel:fetch", id: "1", path: "/sessions" });
    await waitFor(() => expect(gone.replies()).toHaveLength(1));
    expect(gone.replies()[0]).toMatchObject({ ok: false, status: 403 });
    cleanup();

    // No manifest request at all for an external src (msw fails the test on one).
    const ext = mount({ src: "https://panels.example.com/widget/index.html" });
    ext.send({ type: "corvin:panel:fetch", id: "1", path: "/sessions" });
    await waitFor(() => expect(ext.replies()).toHaveLength(1));
    expect(ext.replies()[0]).toMatchObject({ ok: false, status: 403 });
  });

  it("an in-editor preview declares its paths in the document itself", async () => {
    server.use(http.get("/v1/console/cost/summary", () => HttpResponse.json({ today_spend: "1" })));
    const html = '<meta name="corvin-panel-data" content="/cost/summary /api-keys"><h1>x</h1>';
    const { send, replies } = mount({ srcDoc: html });
    send({ type: "corvin:panel:fetch", id: "1", path: "/cost/summary" });
    send({ type: "corvin:panel:fetch", id: "2", path: "/api-keys" });
    await waitFor(() => expect(replies()).toHaveLength(2));
    expect(replies().find((r) => r.id === "1")).toMatchObject({ ok: true, data: { today_spend: "1" } });
    expect(replies().find((r) => r.id === "2")).toMatchObject({ ok: false, status: 403 });
  });

  it("ignores a fetch from a frame it did not mount, and a malformed envelope", async () => {
    const { send, replies, post } = mount({ srcDoc: "<p>x</p>", dataPaths: ["/sessions"] });
    send({ type: "corvin:panel:fetch", id: "1", path: "/sessions" }, window);
    send({ type: "corvin:panel:fetch", path: "/sessions" });
    send({ type: "corvin:panel:fetch", id: "x".repeat(100), path: "/sessions" });
    await new Promise((r) => setTimeout(r, 50));
    expect(replies()).toHaveLength(0);
    expect(post.mock.calls.filter(([m]) => (m as { type: string }).type === "corvin:host:data")).toHaveLength(0);
  });

  it("bounds the requests in flight", async () => {
    let release: () => void = () => {};
    const gate = new Promise<void>((r) => { release = r; });
    server.use(http.get("/v1/console/sessions", async () => {
      await gate;
      return HttpResponse.json({ ok: 1 });
    }));
    const { send, replies } = mount({ srcDoc: "<p>x</p>", dataPaths: ["/sessions"] });
    for (let i = 0; i < PANEL_DATA_MAX_IN_FLIGHT + 2; i++) {
      send({ type: "corvin:panel:fetch", id: `r${i}`, path: "/sessions" });
    }
    await waitFor(() => expect(replies().filter((r) => r.status === 429)).toHaveLength(2));
    release();
    await waitFor(() => expect(replies()).toHaveLength(PANEL_DATA_MAX_IN_FLIGHT + 2));
    expect(replies().filter((r) => r.ok === true)).toHaveLength(PANEL_DATA_MAX_IN_FLIGHT);
  });
});
