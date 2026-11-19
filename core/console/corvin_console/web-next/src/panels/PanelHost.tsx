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
 * secret).
 *
 * Generated and manifest panels are mounted with PANEL_SANDBOX ("allow-scripts"
 * only). "allow-scripts allow-same-origin" on a same-origin document is no sandbox
 * at all: the frame can reach window.parent, read the console DOM and call the API
 * with the operator's session. Consequence, stated: such a panel runs with an
 * opaque origin, so it CANNOT call the console API with the operator's cookies and
 * has no storage. It asks the host for data instead (`corvin:panel:fetch`): the host
 * performs a GET with its own session, only for a path that is on the console-wide
 * PANEL_READABLE_PATHS AND declared in that panel's manifest, and forwards at most
 * PANEL_DATA_MAX_BYTES of JSON (protocol.ts, "Data requests"). The sandbox is not
 * widened for it.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { safeNavTarget } from "@/lib/safe-url";
import {
  PANEL_DATA_MAX_BYTES,
  PANEL_DATA_MAX_IN_FLIGHT,
  PANEL_DATA_MAX_PER_MINUTE,
  PANEL_PROTOCOL_VERSION,
  decidePanelDataRequest,
  declaredDataPathsFromHtml,
  grantablePanelPaths,
  isPanelFetch,
  isPanelToHost,
  type HostToPanel,
  type PanelHostContext,
} from "./protocol";

/** Sandbox tokens for every panel the console did not author (generated panels,
 *  backend-manifest iframes). Never add allow-same-origin here. */
export const PANEL_SANDBOX = "allow-scripts";

/** True when the frame keeps its real origin; without allow-same-origin a sandboxed
 *  frame has an opaque ("null") origin, whatever its src. */
export function sandboxKeepsOrigin(sandbox: string): boolean {
  return sandbox.split(/\s+/).includes("allow-same-origin");
}

/** The targetOrigin for a message to a panel frame (see PanelHost). Exported for
 *  the unit test; the URL is resolved against the console's own location. */
export function panelTargetOrigin(p: { src?: string; srcDoc?: string; sandbox: string }): string {
  if (p.srcDoc != null || !sandboxKeepsOrigin(p.sandbox)) return "*";
  try {
    return new URL(p.src ?? "/", window.location.href).origin;
  } catch {
    // An unparsable src cannot be addressed: never fall back to "*" for a frame
    // that keeps its origin. The console's own origin simply never matches it.
    return window.location.origin;
  }
}

/** The context a hello may carry for *targetOrigin*. A "*" message is delivered to
 *  whatever document the frame holds when it arrives (a sandboxed frame can still
 *  navigate itself), so it must not name the tenant; theme, API base and contract
 *  version are the same for every install. */
export function contextForTarget(ctx: PanelHostContext, targetOrigin: string): PanelHostContext {
  if (targetOrigin !== "*") return ctx;
  return {
    baseUrl: ctx.baseUrl, theme: ctx.theme, contractVersion: ctx.contractVersion,
    ...(ctx.dataPaths ? { dataPaths: ctx.dataPaths } : {}),
  };
}

/** The id of a generated panel when *src* is its own index.html under *baseUrl*
 *  (`<baseUrl>/panels/<id>/index.html`), else null. Only such a panel has a manifest
 *  on this console; an external https panel gets no data access. */
export function generatedPanelId(src: string | undefined, baseUrl: string): string | null {
  if (!src) return null;
  const path = src.split(/[?#]/, 1)[0];
  const prefix = `${baseUrl}/panels/`;
  if (!path.startsWith(prefix)) return null;
  const m = /^([a-z0-9][a-z0-9-]{0,48})\/index\.html$/.exec(path.slice(prefix.length));
  return m ? m[1] : null;
}

/** What the host answers a `corvin:panel:fetch` with (never throws). Exported for
 *  the unit test; `fetchImpl` is the host's own fetch - the request carries the
 *  HOST's session, so the tenant is the host's. */
export async function readPanelData(
  path: unknown,
  declared: readonly unknown[] | null | undefined,
  baseUrl: string,
  fetchImpl: typeof fetch = fetch,
): Promise<{ ok: true; status: number; data: unknown } | { ok: false; status: number; error: string }> {
  const decision = decidePanelDataRequest(path, declared, baseUrl);
  if ("reason" in decision) return { ok: false, status: 403, error: decision.reason };
  let res: Response;
  try {
    res = await fetchImpl(decision.url, {
      method: "GET",
      credentials: "include",
      redirect: "error", // an allowlisted path must not bounce somewhere else
      headers: { Accept: "application/json" },
    });
  } catch {
    return { ok: false, status: 0, error: "request failed" };
  }
  // The body of a failed call is not forwarded: an error page is not panel data.
  if (!res.ok) return { ok: false, status: res.status, error: `HTTP ${res.status}` };
  const declaredLength = Number(res.headers.get("content-length") ?? "0");
  if (declaredLength > PANEL_DATA_MAX_BYTES) {
    return { ok: false, status: 413, error: "response is larger than a panel may receive" };
  }
  let text: string;
  try {
    text = await res.text();
  } catch {
    return { ok: false, status: 0, error: "response could not be read" };
  }
  // UTF-8 length, not UTF-16 code units: the limit is in bytes.
  if (new TextEncoder().encode(text).length > PANEL_DATA_MAX_BYTES) {
    return { ok: false, status: 413, error: "response is larger than a panel may receive" };
  }
  try {
    return { ok: true, status: res.status, data: JSON.parse(text) as unknown };
  } catch {
    return { ok: false, status: 415, error: "response is not JSON" };
  }
}

/** How long a data request waits for a generated panel's manifest. */
const MANIFEST_WAIT_MS = 5000;

/** Declared data paths of a mounted panel: an explicit prop, the meta tag of an
 *  inline (srcDoc) document, or - for a generated panel - its manifest on the
 *  server. `null` while a manifest is still loading; `[]` when there is none. */
function usePanelDataPaths(p: {
  src?: string; srcDoc?: string; baseUrl: string; dataPaths?: readonly string[];
}): string[] | null {
  const { src, srcDoc, baseUrl, dataPaths } = p;
  const panelId = dataPaths || srcDoc != null ? null : generatedPanelId(src, baseUrl);
  const [fromManifest, setFromManifest] = useState<{ id: string; paths: string[] } | null>(null);
  useEffect(() => {
    if (!panelId) return;
    let live = true;
    fetch(`${baseUrl}/panels/${encodeURIComponent(panelId)}/manifest`, { credentials: "include" })
      .then((r) => (r.ok ? r.json() : null))
      .then((body: unknown) => {
        const declared = (body as { data_paths?: unknown } | null)?.data_paths;
        if (live) setFromManifest({ id: panelId, paths: grantablePanelPaths(Array.isArray(declared) ? declared : []) });
      })
      .catch(() => { if (live) setFromManifest({ id: panelId, paths: [] }); });
    return () => { live = false; };
  }, [panelId, baseUrl]);
  return useMemo(() => {
    if (dataPaths) return grantablePanelPaths(dataPaths);
    if (srcDoc != null) return grantablePanelPaths(declaredDataPathsFromHtml(srcDoc));
    if (!panelId) return [];
    return fromManifest && fromManifest.id === panelId ? fromManifest.paths : null;
  }, [dataPaths, srcDoc, panelId, fromManifest]);
}

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
  /** Paths the panel declared for `corvin:panel:fetch`. When omitted they are read
   *  from the srcDoc's meta tag or, for a generated panel, from its manifest. Only
   *  paths on PANEL_READABLE_PATHS are ever granted. */
  dataPaths?: readonly string[];
}

/** True iff `to` is a safe SPA-internal navigation target — a hostile panel must
 *  not be able to drive a top-level redirect.
 *
 *  R4-C7 (2026-09-07): the implementation moved to `@/lib/safe-url` because three
 *  OTHER call sites navigate on a value they did not author and none of them found
 *  this one, sitting in a component module. It also gained control-character and
 *  percent-encoded-backslash handling there. Kept as a named re-export so the panel
 *  protocol still reads in terms of its own perimeter. */
export const isSafeInternalNavTarget = safeNavTarget;

/** The Console's effective theme, read from the `data-theme` attribute the
 *  theme-toggle sets on <html> (theme-toggle.tsx:
 *  `documentElement.setAttribute("data-theme", effective)`), kept in sync reactively
 *  so a theme switch reaches the panel. This is why a panel must NOT be handed a
 *  hardcoded "light": the iframe has no access to the Console's theme otherwise, so
 *  it rendered light-on-dark (mismatched). NB: the Console uses a data-attribute,
 *  NOT a `dark` class — reading the class gave "light" forever. */
export function useConsoleTheme(): "light" | "dark" {
  const read = (): "light" | "dark" =>
    typeof document !== "undefined" &&
    document.documentElement.getAttribute("data-theme") === "dark"
      ? "dark"
      : "light";
  const [theme, setTheme] = useState<"light" | "dark">(read);
  useEffect(() => {
    const obs = new MutationObserver(() => setTheme(read()));
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
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
  const { src, srcDoc, sandbox, baseUrl, tenantId, theme, contractVersion } = props;
  const ref = useRef<HTMLIFrameElement>(null);
  const navigate = useNavigate();
  const [height, setHeight] = useState<number>(600);
  const consoleTheme = useConsoleTheme();
  const apiBase = baseUrl ?? "/v1/console";
  const grantedPaths = usePanelDataPaths({ src, srcDoc, baseUrl: apiBase, dataPaths: props.dataPaths });
  const ctx = useMemo(
    () => ({
      ...makeHostContext({ sandbox, baseUrl, tenantId, theme, contractVersion }, consoleTheme),
      dataPaths: grantedPaths ?? [],
    }),
    [sandbox, baseUrl, tenantId, theme, contractVersion, consoleTheme, grantedPaths],
  );
  // Live refs for the message handler (it is bound once per frame).
  const grantedRef = useRef(grantedPaths);
  grantedRef.current = grantedPaths;
  const inFlight = useRef(0);
  const recent = useRef<number[]>([]);
  // Keep a live ref so the panel:ready handler answers host:hello with the CURRENT
  // theme — panel:ready can arrive before useConsoleTheme has read data-theme, so a
  // closed-over ctx would send stale "light". The ref is always current.
  const ctxRef = useRef(ctx);
  ctxRef.current = ctx;

  // targetOrigin per panel type:
  //  - a frame that KEEPS its origin (sandbox with allow-same-origin): the origin of
  //    its src, same-origin or an external https panel alike - the message is then
  //    delivered only while that origin's document is in the frame;
  //  - an opaque-origin frame (srcDoc, or any src sandboxed without
  //    allow-same-origin): only "*" reaches it, a concrete targetOrigin never matches
  //    origin "null" and the message is dropped silently.
  const targetOriginFor = useCallback(
    (): string => panelTargetOrigin({ src, srcDoc, sandbox }),
    [srcDoc, src, sandbox],
  );

  // Push theme changes to an already-connected panel (initial theme rides in
  // host:hello; this covers the operator toggling AND the case where the panel
  // connected before the theme was resolved).
  useEffect(() => {
    const win = ref.current?.contentWindow;
    if (!win) return;
    win.postMessage({ type: "corvin:host:theme", theme: ctx.theme }, targetOriginFor());
  }, [ctx.theme, targetOriginFor]);

  useEffect(() => {
    function onMessage(ev: MessageEvent) {
      // Only accept messages from the iframe WE mounted — never a random frame.
      const win = ref.current?.contentWindow;
      if (!win || ev.source !== win) return;
      const msg = ev.data;
      if (!isPanelToHost(msg)) return;

      switch (msg.type) {
        case "corvin:panel:ready": {
          // ctxRef.current, NOT the closed-over ctx: answer with the CURRENT theme
          // even if panel:ready arrived before the theme resolved. targetOrigin is
          // "*" only for an opaque-origin frame, where it is the only option - and
          // that hello goes out WITHOUT the tenant id (contextForTarget).
          const target = targetOriginFor();
          const hello: HostToPanel = {
            type: "corvin:host:hello",
            protocolVersion: PANEL_PROTOCOL_VERSION,
            ctx: contextForTarget(ctxRef.current, target),
          };
          win.postMessage(hello, target);
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
        case "corvin:panel:fetch": {
          // A read on the panel's behalf, with the HOST's session: only a path the
          // panel declared and the console lets panels read (readPanelData).
          if (!isPanelFetch(msg)) break;
          const id = msg.id;
          const target = targetOriginFor();
          const reply = (r: Awaited<ReturnType<typeof readPanelData>>) =>
            win.postMessage({ type: "corvin:host:data", id, ...r } as HostToPanel, target);
          const now = Date.now();
          recent.current = recent.current.filter((t) => now - t < 60_000);
          if (inFlight.current >= PANEL_DATA_MAX_IN_FLIGHT || recent.current.length >= PANEL_DATA_MAX_PER_MINUTE) {
            reply({ ok: false, status: 429, error: "too many data requests" });
            break;
          }
          recent.current.push(now);
          inFlight.current += 1;
          void (async () => {
            // A generated panel's manifest is fetched when the frame mounts; a
            // request that arrives first waits for it instead of being refused.
            const deadline = Date.now() + MANIFEST_WAIT_MS;
            while (grantedRef.current === null && Date.now() < deadline) {
              await new Promise((r) => setTimeout(r, 25));
            }
            const granted = grantedRef.current;
            if (granted === null) return { ok: false as const, status: 503, error: "panel manifest is not available" };
            return readPanelData(msg.path, granted, ctxRef.current.baseUrl);
          })()
            .then(reply)
            .finally(() => { inFlight.current -= 1; });
          break;
        }
      }
    }
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
    // ctx read via ctxRef → listener need not rebind on theme change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [src, props.srcDoc, sandbox, navigate]);

  // Pass the theme as a URL query param on the src — the ONLY reliable channel for
  // a sandboxed iframe: postMessage races the handshake, and window.parent.document
  // is blocked cross-origin even with allow-same-origin. The panel reads
  // location.search at load. ctx.theme drives it, so a toggle re-renders → new src →
  // the panel reloads in the new theme (host:theme still updates it live in between).
  const srcWithTheme =
    src != null
      ? src + (src.includes("?") ? "&" : "?") + "corvinTheme=" + ctx.theme
      : undefined;

  return (
    <iframe
      ref={ref}
      {...(props.srcDoc != null ? { srcDoc: props.srcDoc } : { src: srcWithTheme })}
      sandbox={sandbox}
      title="Console panel"
      onLoad={() => {
        // Belt-and-suspenders: if the panel was ready before we could answer,
        // push the current theme once the frame has loaded.
        const win = ref.current?.contentWindow;
        if (win) win.postMessage({ type: "corvin:host:theme", theme: ctxRef.current.theme }, targetOriginFor());
      }}
      style={{ width: "100%", height, border: "none" }}
    />
  );
}
