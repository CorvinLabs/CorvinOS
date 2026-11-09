/**
 * useSettingsStream — subscribes to the backend SSE stream and invalidates
 * React-Query caches whenever settings files change on disk.
 *
 * Mount once inside AppLayout (authenticated shell) so a single EventSource
 * connection covers the whole session. All settings-related pages benefit
 * automatically without needing their own polling logic.
 *
 * Domain → QueryKey mapping
 * ─────────────────────────
 *   tenant, dialectic, relay, branding, data_policy  →  ["settings"]
 *   ldd                                              →  ["settings"], ["ldd"]
 *   engines                                          →  ["engines"]
 *   bridge.<channel>                                 →  ["bridges","list"],
 *                                                       ["bridges","<channel>"],
 *                                                       ["bridge-setup","<channel>"]
 */
import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";

const SSE_URL = "/v1/console/settings/stream";

const DOMAIN_KEYS: Record<string, string[][]> = {
  tenant:          [["settings"]],
  ldd:             [["settings"], ["ldd"]],
  quality_layers:  [["quality-layers"]],
  dialectic:       [["settings"]],
  relay:           [["settings"]],
  branding:        [["settings"]],
  data_policy:     [["settings"]],
  engines:         [["engines"]],
};

function resolveKeys(domain: string): string[][] {
  if (domain.startsWith("bridge.")) {
    const ch = domain.slice("bridge.".length);
    return [
      ["bridges", "list"],
      ["bridges", ch],
      ["bridge-setup", ch],
    ];
  }
  return DOMAIN_KEYS[domain] ?? [];
}

export function useSettingsStream() {
  const qc = useQueryClient();

  useEffect(() => {
    const es = new EventSource(SSE_URL, { withCredentials: true });
    // The stream lives 10 s and then ends; EventSource reconnects. A change made while it was down never arrives as an event, so every
    // reconnect (not the first open) re-reads all settings queries.
    let opened = false;
    es.onopen = () => {
      if (opened) {
        for (const keys of new Set(Object.values(DOMAIN_KEYS).flat().map((k) => JSON.stringify(k)))) {
          qc.invalidateQueries({ queryKey: JSON.parse(keys) as string[] });
        }
      }
      opened = true;
    };

    es.addEventListener("settings.changed", (e: MessageEvent) => {
      try {
        const { domains } = JSON.parse(e.data as string) as { domains: string[] };
        const seen = new Set<string>();
        for (const domain of domains) {
          for (const queryKey of resolveKeys(domain)) {
            const key = JSON.stringify(queryKey);
            if (!seen.has(key)) {
              seen.add(key);
              qc.invalidateQueries({ queryKey });
            }
          }
        }
      } catch {
        // malformed event — ignore
      }
    });

    // EventSource handles reconnection automatically on error.
    // No manual retry logic needed.
    es.onerror = () => undefined;

    return () => es.close();
  }, [qc]);
}
