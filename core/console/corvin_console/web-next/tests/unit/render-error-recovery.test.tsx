/**
 * PLAN-0969 WP-3: a page that crashes while rendering reloads itself ONCE and reports a
 * content-free signature; a second crash within the guard window shows the error card.
 *
 * Drives the real RouteErrorBoundary with a child that throws, real sessionStorage, and a
 * spied fetch / location.reload. The report body must never carry the error message, a chat
 * id or the user agent: those are exactly what the backend refuses (tests/sandbox/
 * test_client_error_report.py proves that side over HTTP).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import * as React from "react";
import { RouteErrorBoundary } from "@/components/error-boundary";
import {
  errorCode,
  routePattern,
  osFamily,
  shouldAutoReload,
  RELOAD_GUARD_MS,
} from "@/lib/render-error-recovery";

function Boom({ message }: { message: string }): React.ReactElement {
  throw new Error(message);
}

const REACT_185 =
  "Minified React error #185; visit https://reactjs.org/docs/error-decoder.html?invariant=185 for the full message";

describe("signature helpers", () => {
  it("maps a minified React invariant to its number", () => {
    expect(errorCode(new Error(REACT_185))).toBe("react-185");
  });
  it("falls back to the error class, never the message", () => {
    expect(errorCode(new TypeError("cannot read secret of undefined"))).toBe("TypeError");
  });
  it("keeps only the first two path segments", () => {
    expect(routePattern("/console/app/chat/YKAe0sFHktvYaIEa6iSFAA")).toBe("/app/chat");
    expect(routePattern("/app/chat/YKAe0sFHktvYaIEa6iSFAA")).toBe("/app/chat");
    expect(routePattern("/app/Settings")).toBe("/app/settings");
    expect(routePattern("/")).toBe("/root");
  });
  it("reduces the platform to a closed family", () => {
    expect(osFamily("Win32", "")).toBe("windows");
    expect(osFamily("MacIntel", "")).toBe("macos");
    expect(osFamily("Linux x86_64", "Mozilla/5.0 (X11; Linux x86_64)")).toBe("linux");
    expect(osFamily("Linux armv8l", "Mozilla/5.0 (Linux; Android 14)")).toBe("android");
    expect(osFamily("iPhone", "")).toBe("ios");
    expect(osFamily("", "")).toBe("other");
  });
  it("allows one reload per route inside the guard window", () => {
    sessionStorage.clear();
    expect(shouldAutoReload("/app/chat", 1_000)).toBe(true);
    expect(shouldAutoReload("/app/chat", 1_000 + RELOAD_GUARD_MS - 1)).toBe(false);
    expect(shouldAutoReload("/app/other", 1_000)).toBe(true);
    expect(shouldAutoReload("/app/chat", 1_000 + RELOAD_GUARD_MS + 1)).toBe(true);
  });
});

describe("RouteErrorBoundary recovery", () => {
  let reload: ReturnType<typeof vi.fn>;
  let fetchSpy: ReturnType<typeof vi.fn>;
  const realLocation = window.location;

  beforeEach(() => {
    sessionStorage.clear();
    reload = vi.fn();
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { ...realLocation, pathname: "/console/app/chat/YKAe0sFHktvYaIEa6iSFAA", reload },
    });
    fetchSpy = vi.fn(() => Promise.resolve(new Response('{"recorded":true}', { status: 200 })));
    vi.stubGlobal("fetch", fetchSpy);
    vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    Object.defineProperty(window, "location", { configurable: true, value: realLocation });
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("first crash: reloads once, reports a content-free signature, no error card", () => {
    render(
      <RouteErrorBoundary label="/app/chat/YKAe0sFHktvYaIEa6iSFAA">
        <Boom message={REACT_185} />
      </RouteErrorBoundary>,
    );
    expect(reload).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(/Something went wrong/)).toBeNull();
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/v1/console/aco/client-error");
    expect(init.method).toBe("POST");
    const body = JSON.parse(String(init.body));
    expect(body).toEqual({
      code: "react-185",
      route: "/app/chat",
      build: expect.stringMatching(/^[A-Za-z0-9_-]{1,32}$/),
      os_family: expect.stringMatching(/^(windows|macos|linux|android|ios|other)$/),
      auto_reloaded: false,
    });
    const raw = String(init.body);
    expect(raw).not.toContain("YKAe0sFHktvYaIEa6iSFAA");
    expect(raw).not.toContain("Minified");
  });

  it("second crash inside the window: shows the card, reports auto_reloaded, no reload", () => {
    shouldAutoReload("/app/chat", Date.now()); // the reload that already happened
    render(
      <RouteErrorBoundary label="/app/chat/YKAe0sFHktvYaIEa6iSFAA">
        <Boom message={REACT_185} />
      </RouteErrorBoundary>,
    );
    expect(reload).not.toHaveBeenCalled();
    expect(screen.getByText(/Something went wrong on this page/)).toBeTruthy();
    const body = JSON.parse(String((fetchSpy.mock.calls[0] as [string, RequestInit])[1].body));
    expect(body.auto_reloaded).toBe(true);
  });

  it("a failing report never blocks the reload", () => {
    fetchSpy.mockImplementation(() => Promise.reject(new Error("offline")));
    render(
      <RouteErrorBoundary label="/app/chat">
        <Boom message="x" />
      </RouteErrorBoundary>,
    );
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("chunk-load errors are still left to the outer boundary", () => {
    expect(RouteErrorBoundary.getDerivedStateFromError(
      new Error("Failed to fetch dynamically imported module: /x.js"))).toBeNull();
  });
});
