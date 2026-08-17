/** Host-side dispatch proof for P5 (ADR-0363): PanelHost replies host:hello with
 *  the ctx when the embedded panel announces panel:ready. Closes the handshake
 *  loop with the P4 SDK-side test. */
import { describe, it, expect, vi } from "vitest";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import PanelHost from "@/panels/PanelHost";

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
    const win = iframe.contentWindow as Window;
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
    const post = vi.spyOn(iframe.contentWindow as Window, "postMessage");
    // message from a DIFFERENT source (not our iframe) → ignored
    window.dispatchEvent(new MessageEvent("message", {
      source: window,
      data: { type: "corvin:panel:ready", protocolVersion: "1" },
    }));
    expect(post).not.toHaveBeenCalled();
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
