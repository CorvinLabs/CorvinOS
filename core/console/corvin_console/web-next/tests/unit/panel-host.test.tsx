/** Host-side dispatch proof for P5 (ADR-0363): PanelHost replies host:hello with
 *  the ctx when the embedded panel announces panel:ready. Closes the handshake
 *  loop with the P4 SDK-side test. */
import { describe, it, expect, vi } from "vitest";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import PanelHost from "@/panels/PanelHost";

// happy-dom >= 21 does not load iframe pages by default (the fix for
// GHSA-c8p4-j97h-3vg8), so a mounted iframe has no contentWindow. The host-side
// dispatch under test only needs a window identity to compare `ev.source`
// against and a postMessage to spy on, so give the frame a stand-in window.
function stubFrameWindow(iframe: HTMLIFrameElement): Window {
  const win = { postMessage: () => {} } as unknown as Window;
  Object.defineProperty(iframe, "contentWindow", { configurable: true, get: () => win });
  return win;
}

describe("PanelHost dispatch (host side)", () => {
  it("replies host:hello with ctx when the panel sends panel:ready", async () => {
    const { container } = render(
      <MemoryRouter>
        <PanelHost
          src="/external-panels/vibe-inspector/index.html"
          sandbox="allow-scripts allow-same-origin"
          theme="dark"
          tenantId="_default"
          contractVersion="1"
        />
      </MemoryRouter>,
    );
    const iframe = container.querySelector("iframe") as HTMLIFrameElement;
    expect(iframe).toBeTruthy();
    const win = stubFrameWindow(iframe);
    const post = vi.spyOn(win, "postMessage");

    window.dispatchEvent(new MessageEvent("message", {
      source: win,
      origin: "http://localhost",
      data: { type: "corvin:panel:ready", protocolVersion: "1" },
    }));

    // host must have replied host:hello carrying the ctx (theme threaded through)
    expect(post).toHaveBeenCalled();
    const [msg] = post.mock.calls[0];
    expect(msg).toMatchObject({
      type: "corvin:host:hello",
      protocolVersion: "1",
      ctx: { theme: "dark", tenantId: "_default", baseUrl: "/v1/console" },
    });
  });

  it("ignores a message from a frame it did not mount", () => {
    const { container } = render(
      <MemoryRouter>
        <PanelHost src="/external-panels/vibe-inspector/index.html" sandbox="allow-scripts" />
      </MemoryRouter>,
    );
    const iframe = container.querySelector("iframe") as HTMLIFrameElement;
    const post = vi.spyOn(stubFrameWindow(iframe), "postMessage");
    // message from a DIFFERENT source (not our iframe) → ignored
    window.dispatchEvent(new MessageEvent("message", {
      source: window,
      data: { type: "corvin:panel:ready", protocolVersion: "1" },
    }));
    expect(post).not.toHaveBeenCalled();
  });
});

import { PANEL_SANDBOX, sandboxKeepsOrigin, panelTargetOrigin, contextForTarget } from "@/panels/PanelHost";
describe("PanelHost sandbox (generated / manifest panels)", () => {
  it("PANEL_SANDBOX never keeps the console origin", () => {
    expect(PANEL_SANDBOX).toBe("allow-scripts");
    expect(sandboxKeepsOrigin(PANEL_SANDBOX)).toBe(false);
    expect(sandboxKeepsOrigin("allow-scripts allow-same-origin")).toBe(true);
  });

  it("answers an opaque-origin panel with targetOrigin '*' (a concrete origin never reaches it)", () => {
    const { container } = render(
      <MemoryRouter>
        <PanelHost src="/v1/console/panels/demo/index.html" sandbox={PANEL_SANDBOX} theme="dark" />
      </MemoryRouter>,
    );
    const iframe = container.querySelector("iframe") as HTMLIFrameElement;
    expect(iframe.getAttribute("sandbox")).toBe("allow-scripts");
    const win = stubFrameWindow(iframe);
    const post = vi.spyOn(win, "postMessage");
    window.dispatchEvent(new MessageEvent("message", {
      source: win,
      origin: "null",
      data: { type: "corvin:panel:ready", protocolVersion: "1" },
    }));
    expect(post).toHaveBeenCalled();
    const [msg, target] = post.mock.calls[0];
    expect(msg).toMatchObject({ type: "corvin:host:hello" });
    expect(target).toBe("*");
    // A "*" message reaches whatever document the frame holds: no tenant id in it.
    const ctx = (msg as { ctx: Record<string, unknown> }).ctx;
    // dataPaths: what this panel may request - nothing until its manifest says so.
    expect(ctx).toEqual({ baseUrl: "/v1/console", theme: "dark", contractVersion: "1", dataPaths: [] });
    expect("tenantId" in ctx).toBe(false);
  });

  it("an external https panel gets its OWN origin as targetOrigin, never '*'", () => {
    const { container } = render(
      <MemoryRouter>
        <PanelHost
          src="https://panels.example.com/widget/index.html"
          sandbox="allow-scripts allow-same-origin"
          tenantId="acme"
        />
      </MemoryRouter>,
    );
    const iframe = container.querySelector("iframe") as HTMLIFrameElement;
    const win = stubFrameWindow(iframe);
    const post = vi.spyOn(win, "postMessage");
    window.dispatchEvent(new MessageEvent("message", {
      source: win,
      origin: "https://panels.example.com",
      data: { type: "corvin:panel:ready", protocolVersion: "1" },
    }));
    const [msg, target] = post.mock.calls[0];
    expect(target).toBe("https://panels.example.com");
    expect((msg as { ctx: { tenantId?: string } }).ctx.tenantId).toBe("acme");
  });

  it("an external https panel WITHOUT allow-same-origin is opaque: '*' and no tenant id", () => {
    const { container } = render(
      <MemoryRouter>
        <PanelHost src="https://panels.example.com/widget/index.html" sandbox="allow-scripts" tenantId="acme" />
      </MemoryRouter>,
    );
    const iframe = container.querySelector("iframe") as HTMLIFrameElement;
    const win = stubFrameWindow(iframe);
    const post = vi.spyOn(win, "postMessage");
    window.dispatchEvent(new MessageEvent("message", {
      source: win,
      origin: "null",
      data: { type: "corvin:panel:ready", protocolVersion: "1" },
    }));
    const [msg, target] = post.mock.calls[0];
    expect(target).toBe("*");
    expect(JSON.stringify(msg)).not.toContain("acme");
  });

  it("panelTargetOrigin / contextForTarget", () => {
    expect(panelTargetOrigin({ srcDoc: "<p>x</p>", sandbox: "allow-scripts allow-same-origin" })).toBe("*");
    expect(panelTargetOrigin({ src: "/a/b.html", sandbox: "allow-scripts" })).toBe("*");
    expect(panelTargetOrigin({ src: "/a/b.html", sandbox: "allow-scripts allow-same-origin" }))
      .toBe(window.location.origin);
    expect(panelTargetOrigin({ src: "https://x.example:8443/p", sandbox: "allow-same-origin" }))
      .toBe("https://x.example:8443");
    // an unparsable src never degrades to "*"
    expect(panelTargetOrigin({ src: "http://[bad", sandbox: "allow-same-origin" })).toBe(window.location.origin);
    const full = { baseUrl: "/v1/console", tenantId: "acme", theme: "light" as const, contractVersion: "1" };
    expect(contextForTarget(full, "https://x.example")).toBe(full);
    expect(contextForTarget(full, "*")).toEqual({ baseUrl: "/v1/console", theme: "light", contractVersion: "1" });
  });

  it("keeps a concrete targetOrigin for a frame that keeps its origin", () => {
    const { container } = render(
      <MemoryRouter>
        <PanelHost src="/external-panels/x/index.html" sandbox="allow-scripts allow-same-origin" />
      </MemoryRouter>,
    );
    const iframe = container.querySelector("iframe") as HTMLIFrameElement;
    const win = stubFrameWindow(iframe);
    const post = vi.spyOn(win, "postMessage");
    window.dispatchEvent(new MessageEvent("message", {
      source: win,
      data: { type: "corvin:panel:ready", protocolVersion: "1" },
    }));
    expect(post.mock.calls[0][1]).toBe(window.location.origin);
  });
});

import { isSafeInternalNavTarget } from "@/panels/PanelHost";
describe("isSafeInternalNavTarget (P4/review hardening)", () => {
  it("accepts SPA-internal paths", () => {
    expect(isSafeInternalNavTarget("/app/dashboard")).toBe(true);
    expect(isSafeInternalNavTarget("/")).toBe(true);
  });
  it("rejects external / protocol-relative / backslash / non-string targets", () => {
    expect(isSafeInternalNavTarget("//evil.com")).toBe(false);
    expect(isSafeInternalNavTarget("/\\evil.com")).toBe(false);
    expect(isSafeInternalNavTarget("https://evil.com")).toBe(false);
    expect(isSafeInternalNavTarget("app/x")).toBe(false);
    expect(isSafeInternalNavTarget(42)).toBe(false);
    expect(isSafeInternalNavTarget(null)).toBe(false);
  });
});
