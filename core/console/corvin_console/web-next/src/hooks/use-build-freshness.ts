import * as React from "react";

/**
 * Detect that a NEW console bundle has been deployed and bring the tab onto it.
 *
 * Why this exists: three caches sit between an edit and the screen (esbuild
 * pre-bundle, dist/, the browser tab). A rebuild clears the first two, but the
 * open tab keeps running the bundle it loaded at navigation time — the operator
 * has to hard-refresh, and "the feature isn't showing" has repeatedly been that
 * missing keystroke rather than a backend fault.
 *
 * How: the SPA shell (`/console/`) is served `no-cache` (see
 * `_SPAStaticFiles`, core/console/corvin_console/app.py), so re-fetching it
 * always yields the CURRENT index.html. Its content-hashed entry bundle is the
 * build identity. When that hash stops matching the one this tab booted with,
 * a new build is live.
 *
 * Gated on the `console_auto_reload` feature flag — off on a normal install,
 * where an unattended reload would be a surprise, not a convenience.
 */

/** Entry-bundle hash the running tab booted with, read once from the DOM. */
function currentEntryHash(): string | null {
  const scripts = Array.from(
    document.querySelectorAll<HTMLScriptElement>('script[src*="/assets/"]'),
  );
  for (const s of scripts) {
    const m = s.src.match(/assets\/(index-[A-Za-z0-9_-]+\.js)/);
    if (m) return m[1];
  }
  return null;
}

async function deployedEntryHash(signal: AbortSignal): Promise<string | null> {
  // cache: "no-store" on top of the server's no-cache: a conditional request
  // answered 304 would hand back the tab's own stale copy of the shell.
  const r = await fetch(window.location.origin + "/console/", {
    cache: "no-store",
    signal,
    credentials: "include",
  });
  if (!r.ok) return null;
  const html = await r.text();
  const m = html.match(/assets\/(index-[A-Za-z0-9_-]+\.js)/);
  return m ? m[1] : null;
}

/** True while the operator is mid-input — reloading here would discard typing. */
function operatorIsTyping(): boolean {
  const el = document.activeElement as HTMLElement | null;
  if (!el) return false;
  const tag = el.tagName;
  if (tag === "TEXTAREA" || tag === "INPUT") {
    return Boolean((el as HTMLInputElement | HTMLTextAreaElement).value);
  }
  return el.isContentEditable && Boolean(el.textContent);
}

export interface BuildFreshness {
  /** A newer bundle is deployed than the one this tab is running. */
  stale: boolean;
  /** Reload onto the new bundle now. */
  reload: () => void;
}

export function useBuildFreshness(
  enabled: boolean,
  pollIntervalMs = 3000,
): BuildFreshness {
  const [stale, setStale] = React.useState(false);
  const bootHash = React.useRef<string | null>(null);
  if (bootHash.current === null) bootHash.current = currentEntryHash();

  React.useEffect(() => {
    // No boot hash means dev-server / unhashed serving — nothing to compare.
    if (!enabled || !bootHash.current) return;
    const ctrl = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    let cancelled = false;

    const tick = async () => {
      try {
        const live = await deployedEntryHash(ctrl.signal);
        if (!cancelled && live && live !== bootHash.current) {
          if (operatorIsTyping()) {
            setStale(true); // surface the banner, let them finish
          } else {
            window.location.reload();
            return;
          }
        }
      } catch {
        // Offline / host restarting mid-deploy — keep polling.
      }
      if (!cancelled) timer = setTimeout(tick, pollIntervalMs);
    };

    timer = setTimeout(tick, pollIntervalMs);
    return () => {
      cancelled = true;
      clearTimeout(timer);
      ctrl.abort();
    };
  }, [enabled, pollIntervalMs]);

  return { stale, reload: () => window.location.reload() };
}
