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
  /** iframe src (external panel entry). Must be same-origin or an allowlisted origin. */
  src: string;
  /** iframe sandbox tokens, e.g. "allow-scripts allow-forms". NEVER allow-same-origin
   *  for a community panel — that would defeat the sandbox. */
  sandbox: string;
  theme?: "light" | "dark";
  tenantId?: string;
  contractVersion?: string;
  /** API base handed to the panel; same-origin. */
  baseUrl?: string;
}

export function makeHostContext(p: PanelHostProps): PanelHostContext {
  return {
    baseUrl: p.baseUrl ?? "/v1/console",
    tenantId: p.tenantId ?? "_default",
    theme: p.theme ?? "light",
    contractVersion: p.contractVersion ?? "1",
  };
}

export default function PanelHost(props: PanelHostProps) {
  const { src, sandbox } = props;
  const ref = useRef<HTMLIFrameElement>(null);
  const navigate = useNavigate();
  const [height, setHeight] = useState<number>(600);
  const ctx = useMemo(() => makeHostContext(props), [
    props.baseUrl, props.tenantId, props.theme, props.contractVersion,
  ]);

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
          // Same-origin panel → "/"; a cross-origin allowlisted panel would need
          // its exact origin here. targetOrigin is never "*": that would leak the
          // host context to whatever origin the frame navigated to.
          win.postMessage(hello, new URL(src, window.location.href).origin);
          break;
        }
        case "corvin:panel:navigate":
          // Whitelisted action: navigate only WITHIN the Console SPA.
          if (typeof msg.to === "string" && msg.to.startsWith("/")) navigate(msg.to);
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
  }, [ctx, src, navigate]);

  return (
    <iframe
      ref={ref}
      src={src}
      sandbox={sandbox}
      title="Console panel"
      style={{ width: "100%", height, border: "none" }}
    />
  );
}
