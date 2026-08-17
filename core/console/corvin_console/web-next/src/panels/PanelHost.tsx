/**
 * PanelHost (ADR-0362 P4) — mounts an EXTERNAL panel (iframe / web-component) and
 * bridges it to the Console via the typed postMessage protocol (protocol.ts).
 *
 * Security posture: an external panel is untrusted. The iframe kind is the primary,
 * safe path — it runs sandboxed, with no direct DOM/cookie access to the Console,
 * and talks to the host only through postMessage envelopes the host validates. The
 * host honours a small whitelist of panel actions (navigate within the Console,
 * resize) and nothing else — a panel cannot read arbitrary host state or drive the
 * app. See makeHostContext() for exactly what the host hands over (PII-free, no
 * secret: the panel calls the same-origin API with the browser's own credentials).
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  PANEL_PROTOCOL_VERSION,
  isPanelToHost,
  type HostToPanel,
  type PanelHostContext,
} from "./protocol";

export interface PanelHostProps {
  /** iframe src (external panel entry). Must be same-origin or an allowlisted origin.
   *  Mutually exclusive with srcDoc; srcDoc wins if both are given. */
  src?: string;
  /** Inline HTML for the panel, used by FrontendForge's live preview (ADR-0364 P6)
   *  so an operator sees the panel they are editing without saving it first. A
   *  srcDoc iframe is origin "null" (fully sandboxed) — the safe default for
   *  unsaved, in-editor code. */
  srcDoc?: string;
  /** iframe sandbox tokens, e.g. "allow-scripts allow-forms". NEVER allow-same-origin
   *  for a community panel — that would defeat the sandbox. */
  sandbox: string;
  theme?: "light" | "dark";
  tenantId?: string;
  contractVersion?: string;
  /** API base handed to the panel; same-origin. */
  baseUrl?: string;
}

/** True iff `to` is a safe SPA-internal navigation target. Rejects protocol-relative
 *  ("//host") and backslash ("/\\host") forms the browser resolves to an external
 *  origin — a hostile panel must not be able to drive a top-level redirect. */
export function isSafeInternalNavTarget(to: unknown): to is string {
  return (
    typeof to === "string" &&
    to.startsWith("/") &&
    !to.startsWith("//") &&
    !to.startsWith("/\\")
  );
}

/** The Console's effective theme, read from the `dark` class the theme-toggle sets
 *  on <html>, and kept in sync reactively so a theme switch reaches the panel. This
 *  is why a panel must NOT be handed a hardcoded "light": the iframe has no access
 *  to the Console's theme otherwise, so it rendered light-on-dark (mismatched). */
export function useConsoleTheme(): "light" | "dark" {
  const read = () =>
    typeof document !== "undefined" &&
    document.documentElement.classList.contains("dark")
      ? "dark"
      : "light";
  const [theme, setTheme] = useState<"light" | "dark">(read);
  useEffect(() => {
    const obs = new MutationObserver(() => setTheme(read()));
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    return () => obs.disconnect();
  }, []);
  return theme;
}

export function makeHostContext(
  p: PanelHostProps,
  effectiveTheme?: "light" | "dark",
): PanelHostContext {
  return {
    baseUrl: p.baseUrl ?? "/v1/console",
    tenantId: p.tenantId ?? "_default",
    // explicit prop wins (FrontendForge preview), else the live Console theme.
    theme: p.theme ?? effectiveTheme ?? "light",
    contractVersion: p.contractVersion ?? "1",
  };
}

export default function PanelHost(props: PanelHostProps) {
  const { src, sandbox } = props;
  const ref = useRef<HTMLIFrameElement>(null);
  const navigate = useNavigate();
  const [height, setHeight] = useState<number>(600);
  const consoleTheme = useConsoleTheme();
  const ctx = useMemo(() => makeHostContext(props, consoleTheme), [
    props.baseUrl, props.tenantId, props.theme, props.contractVersion, consoleTheme,
  ]);

  // Push theme changes to an already-connected panel (it received the initial
  // theme in host:hello; this keeps it in sync when the operator toggles).
  useEffect(() => {
    const win = ref.current?.contentWindow;
    if (!win) return;
    const targetOrigin = props.srcDoc != null
      ? "*"
      : new URL(src ?? "/", window.location.href).origin;
    win.postMessage({ type: "corvin:host:theme", theme: ctx.theme }, targetOrigin);
  }, [ctx.theme, src, props.srcDoc]);

  useEffect(() => {
    function onMessage(ev: MessageEvent) {
      // Only accept messages from the iframe WE mounted — never a random frame.
      const win = ref.current?.contentWindow;
      if (!win || ev.source !== win) return;
      const msg = ev.data;
      if (!isPanelToHost(msg)) return;

      switch (msg.type) {
        case "corvin:panel:ready": {
          const hello: HostToPanel = {
            type: "corvin:host:hello",
            protocolVersion: PANEL_PROTOCOL_VERSION,
            ctx,
          };
          // A srcDoc iframe is origin "null" (FrontendForge live preview) — there
          // is no specific origin to target, and the ctx carries no secret, so "*"
          // is acceptable there. For a real src panel, target its EXACT origin,
          // never "*", so the host context can't leak to a navigated-away frame.
          const targetOrigin = props.srcDoc != null
            ? "*"
            : new URL(src ?? "/", window.location.href).origin;
          win.postMessage(hello, targetOrigin);
          break;
        }
        case "corvin:panel:navigate":
          // Whitelisted action: navigate only WITHIN the Console SPA.
          if (isSafeInternalNavTarget(msg.to)) navigate(msg.to);
          break;
        case "corvin:panel:resize":
          if (typeof msg.height === "number" && msg.height > 0 && msg.height < 20000) {
            setHeight(msg.height);
          }
          break;
      }
    }
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [ctx, src, props.srcDoc, navigate]);

  return (
    <iframe
      ref={ref}
      {...(props.srcDoc != null ? { srcDoc: props.srcDoc } : { src })}
      sandbox={sandbox}
      title="Console panel"
      style={{ width: "100%", height, border: "none" }}
    />
  );
}
