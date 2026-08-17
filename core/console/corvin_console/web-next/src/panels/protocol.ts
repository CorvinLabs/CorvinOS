/**
 * Panel host↔panel postMessage protocol (ADR-0362 P4) — the SSOT shared by the
 * host (PanelHost.tsx) and the client SDK (@corvin/panel-sdk).
 *
 * An external panel (iframe or web-component, e.g. a FrontendForge-built panel, a
 * community panel) is untrusted code. It never gets direct DOM/cookie access to the
 * Console; it talks to the host ONLY through these typed postMessage envelopes. The
 * host decides what context to hand over and what actions to honour.
 *
 * Handshake: panel loads → sends `panel:ready` → host replies `host:hello` with the
 * context → steady state. The host ignores any message from an origin/frame it did
 * not mount, and every message is a discriminated union so a malformed payload is a
 * type error at the boundary, not a runtime surprise.
 */

/** Bump when the envelope SHAPE changes in a way host or SDK must understand. */
export const PANEL_PROTOCOL_VERSION = "1";

/** Context the host hands an external panel. Deliberately minimal and PII-free:
 *  no cookies, no session token — the panel calls the same-origin API with the
 *  browser's own credentials, so it never needs to hold a secret. */
export interface PanelHostContext {
  /** API base the panel should call, e.g. "/v1/console". Same-origin. */
  baseUrl: string;
  /** Tenant the Console is scoped to (for display only; the API enforces it). */
  tenantId: string;
  /** Current Console theme, so the panel can match. */
  theme: "light" | "dark";
  /** Capability-manifest contract version (ADR-0357), for forward-compat. */
  contractVersion: string;
}

/** Host → Panel. */
export type HostToPanel =
  | { type: "corvin:host:hello"; protocolVersion: string; ctx: PanelHostContext }
  | { type: "corvin:host:theme"; theme: "light" | "dark" };

/** Panel → Host. */
export type PanelToHost =
  | { type: "corvin:panel:ready"; protocolVersion: string }
  | { type: "corvin:panel:navigate"; to: string }
  | { type: "corvin:panel:resize"; height: number };

export function isHostToPanel(m: unknown): m is HostToPanel {
  return (
    typeof m === "object" && m !== null &&
    typeof (m as { type?: unknown }).type === "string" &&
    (m as { type: string }).type.startsWith("corvin:host:")
  );
}

export function isPanelToHost(m: unknown): m is PanelToHost {
  return (
    typeof m === "object" && m !== null &&
    typeof (m as { type?: unknown }).type === "string" &&
    (m as { type: string }).type.startsWith("corvin:panel:")
  );
}
