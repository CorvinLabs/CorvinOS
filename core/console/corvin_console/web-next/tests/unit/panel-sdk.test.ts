/** Proof for the P4 panel host↔panel protocol + SDK handshake (ADR-0362). */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { isHostToPanel, isPanelToHost, PANEL_PROTOCOL_VERSION } from "@/panels/protocol";
import { connectToHost, PanelSession } from "@/panel-sdk/index";

describe("protocol guards", () => {
  it("classifies host and panel envelopes by prefix", () => {
    expect(isHostToPanel({ type: "corvin:host:hello" })).toBe(true);
    expect(isHostToPanel({ type: "corvin:panel:ready" })).toBe(false);
    expect(isPanelToHost({ type: "corvin:panel:ready" })).toBe(true);
    expect(isPanelToHost({ type: "corvin:host:hello" })).toBe(false);
    expect(isHostToPanel(null)).toBe(false);
    expect(isHostToPanel({ nope: 1 })).toBe(false);
  });
});

describe("PanelSession messages", () => {
  it("navigate + reportHeight send correctly typed envelopes to the host origin", () => {
    const post = vi.fn();
    const s = new PanelSession(
      { baseUrl: "/v1/console", tenantId: "_default", theme: "light", contractVersion: "1" },
      { postMessage: post } as unknown as Window,
      "https://console.example",
    );
    s.navigate("/app/dashboard");
    s.reportHeight(800);
    expect(post).toHaveBeenNthCalledWith(1,
      { type: "corvin:panel:navigate", to: "/app/dashboard" }, "https://console.example");
    expect(post).toHaveBeenNthCalledWith(2,
      { type: "corvin:panel:resize", height: 800 }, "https://console.example");
  });
});

describe("connectToHost handshake", () => {
  let origParent: Window;
  beforeEach(() => { origParent = window.parent; });
  afterEach(() => {
    Object.defineProperty(window, "parent", { value: origParent, configurable: true });
    vi.restoreAllMocks();
  });

  it("announces ready then resolves on host:hello with the ctx", async () => {
    const post = vi.fn();
    const fakeHost = { postMessage: post } as unknown as Window;
    Object.defineProperty(window, "parent", { value: fakeHost, configurable: true });

    const p = connectToHost({ timeoutMs: 1000 });
    // the SDK must have announced readiness with "*"
    expect(post).toHaveBeenCalledWith(
      { type: "corvin:panel:ready", protocolVersion: PANEL_PROTOCOL_VERSION }, "*");

    // simulate the host replying hello from its window
    const ctx = { baseUrl: "/v1/console", tenantId: "_default", theme: "dark" as const, contractVersion: "1" };
    window.dispatchEvent(new MessageEvent("message", {
      source: fakeHost,
      origin: "https://console.example",
      data: { type: "corvin:host:hello", protocolVersion: "1", ctx },
    }));

    const session = await p;
    expect(session.ctx.theme).toBe("dark");
    expect(session.ctx.baseUrl).toBe("/v1/console");
  });

  it("rejects when there is no host frame", async () => {
    Object.defineProperty(window, "parent", { value: window, configurable: true });
    await expect(connectToHost({ timeoutMs: 200 })).rejects.toThrow(/not embedded/);
  });
});
