/**
 * AI-panel adapter (ADR-0366) — the single fetch site for the operator's
 * AI-generated Console panels.
 *
 * CorvinOS is an AI OS: the operator asks the KI (in chat) to build a panel; the KI
 * generates the HTML and installs it via POST /v1/console/panels. The shell reads
 * GET /v1/console/panels here and mounts each one dynamically — as a sandboxed iframe
 * (through the same PanelHost as every other panel) and as a nav entry — so a panel
 * the KI just created appears without a rebuild.
 */
import { useQuery } from "@tanstack/react-query";
import { z } from "zod";

const zPanel = z.object({
  id: z.string(),
  title: z.string(),
  nav_group: z.string().default("build"),
  icon: z.string().default("Sparkles"),
});
export type AiPanel = z.infer<typeof zPanel>;

const zList = z.object({ panels: z.array(zPanel) });

async function fetchPanels(): Promise<AiPanel[]> {
  const r = await fetch("/v1/console/panels", { credentials: "include" });
  if (!r.ok) throw new Error(`panels ${r.status}`);
  return zList.parse(await r.json()).panels;
}

/** The AI-generated panels for the current tenant. Fires in the authed shell; a
 *  failure/loading state resolves to [] so the shell never breaks on it. */
export function useAiPanels() {
  return useQuery({
    queryKey: ["ai-panels"],
    queryFn: fetchPanels,
    staleTime: 30_000,
    retry: false,
  });
}

/** Same-origin URL that serves a generated panel's HTML (for the iframe src). */
export function aiPanelSrc(id: string): string {
  return `/v1/console/panels/${encodeURIComponent(id)}/index.html`;
}
