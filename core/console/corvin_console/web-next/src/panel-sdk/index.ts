/**
 * @corvin/panel-sdk (ADR-0362 P4) — the client library an EXTERNAL Console panel
 * imports to talk to the host. It is the panel-side of the postMessage protocol
 * (../panels/protocol.ts, the shared SSOT).
 *
 * Usage inside a panel (iframe):
 *
 *   import { connectToHost } from "@corvin/panel-sdk";
 *   const host = await connectToHost();
 *   // host.ctx.baseUrl, host.ctx.theme, host.ctx.tenantId
 *   host.reportHeight(document.body.scrollHeight);
 *   host.onThemeChange((t) => applyTheme(t));
 *   host.navigate("/app/dashboard");
 *
 * The panel never receives a session token — it calls `${host.ctx.baseUrl}/…` with
 * the browser's own same-origin credentials. It only ever gains the small, typed
 * surface below; the host validates and whitelists everything it does.
 */
import {
  PANEL_PROTOCOL_VERSION,
  isHostToPanel,
  type PanelHostContext,
  type PanelToHost,
} from "../panels/protocol";

export type { PanelHostContext } from "../panels/protocol";
export { PANEL_PROTOCOL_VERSION } from "../panels/protocol";

/** A live connection to the Console host. */
export class PanelSession {
  constructor(
    readonly ctx: PanelHostContext,
    private readonly host: Window,
    private readonly hostOrigin: string,
  ) {}

  private send(msg: PanelToHost): void {
    // targetOrigin is the host's exact origin (from the hello), never "*": a
    // steady-state message must not leak to whatever the parent navigated to.
    this.host.postMessage(msg, this.hostOrigin);
  }

  /** Ask the Console to navigate within its SPA (host validates it starts with "/"). */
  navigate(to: string): void {
    this.send({ type: "corvin:panel:navigate", to });
  }

  /** Report the panel's content height so the host can size the iframe. */
  reportHeight(height: number): void {
    this.send({ type: "corvin:panel:resize", height });
  }

  /** Subscribe to host theme changes. Returns an unsubscribe fn. */
  onThemeChange(cb: (theme: "light" | "dark") => void): () => void {
    const onMessage = (ev: MessageEvent) => {
      if (ev.source !== this.host) return;
      const m = ev.data;
      if (isHostToPanel(m) && m.type === "corvin:host:theme") cb(m.theme);
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }
}

/** Perform the handshake with the Console host. Resolves once the host replies
 *  `host:hello`; rejects after `timeoutMs` (default 5s) if no host is present. */
export function connectToHost(opts?: { timeoutMs?: number }): Promise<PanelSession> {
  return new Promise((resolve, reject) => {
    const host = window.parent;
    if (!host || host === window) {
      reject(new Error("no host frame — panel is not embedded in the Console"));
      return;
    }
    const timeout = setTimeout(() => {
      cleanup();
      reject(new Error("Console host handshake timed out"));
    }, opts?.timeoutMs ?? 5000);

    function onMessage(ev: MessageEvent) {
      if (ev.source !== host) return;
      const m = ev.data;
      if (isHostToPanel(m) && m.type === "corvin:host:hello") {
        cleanup();
        resolve(new PanelSession(m.ctx, host, ev.origin));
      }
    }
    function cleanup() {
      clearTimeout(timeout);
      window.removeEventListener("message", onMessage);
    }

    window.addEventListener("message", onMessage);
    // Announce readiness. targetOrigin "*" is acceptable ONLY here: this message
    // carries no secret, and the panel does not yet know the host origin.
    host.postMessage(
      { type: "corvin:panel:ready", protocolVersion: PANEL_PROTOCOL_VERSION },
      "*",
    );
  });
}
