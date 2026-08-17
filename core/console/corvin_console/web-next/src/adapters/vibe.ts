/**
 * Vibe-Engineering adapter (ADR-0353 P0) — the SINGLE fetch site for the Context
 * Pipeline domain. Every panel/page consumes these zod-validated react-query hooks;
 * no component calls the backend directly. This is the reference domain that proves
 * the P0 adapter pattern before it is rolled out across the other console domains.
 *
 * Endpoints: GET /traces · GET /explain/{hash} · GET /prompt/{turn} ·
 *            GET /forged/{turn} · GET /pipeline · PUT /pipeline
 */
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";

const BASE = "/v1/console/vibe-engineering";

// ── low-level typed fetch (credentials + zod validation in ONE place) ──────────
async function getJSON<T>(path: string, schema: z.ZodType<T>): Promise<T> {
  const r = await fetch(`${BASE}${path}`, { credentials: "include" });
  if (!r.ok) throw new Error(`HTTP ${r.status} on ${path}`);
  return schema.parse(await r.json());
}

async function getCsrf(): Promise<string> {
  const r = await fetch("/v1/console/auth/whoami", { credentials: "include" });
  const d = await r.json().catch(() => ({}));
  return typeof d?.csrf_token === "string" ? d.csrf_token : "";
}

// ── schemas (the contract; drift becomes a zod parse error, not a runtime bug) ──
const zSource = z.object({ id: z.string(), score: z.number().nullable().optional() });
const zStage = z.object({
  stage: z.string(),
  status: z.string(),
  duration_ms: z.number().nullable().optional(),
  confidence_tier: z.string().optional(),
  sources: z.array(zSource).optional(),
  reason: z.string().optional(),
});
const zTurn = z.object({
  turn_id: z.string(),
  ts: z.number().optional(),
  hash: z.string().optional(),
  degraded: z.string().nullable().optional(),
  top_score: z.number().optional(),
  brief_sha256: z.string().nullable().optional(),
  stages: z.array(zStage),
});
const zTraces = z.object({
  tenant_id: z.string(),
  available: z.boolean(),
  sessions: z.array(z.object({ session: z.string(), turns: z.array(zTurn) })),
});
const zExplain = z.object({
  found: z.boolean(), brief_sha256: z.string().optional(),
  text: z.string().optional(), reason: z.string().optional(),
});
const zSection = z.object({
  kind: z.string(), label: z.string(),
  items: z.array(z.string()).optional(), text: z.string().optional(),
});
const zAssembly = z.object({
  found: z.boolean(),
  sections: z.array(zSection).optional(),
  cel_text: z.string().optional(),
  final_prompt: z.string().optional(),
  forged_tools: z.array(z.string()).optional(),
  forged_skills: z.array(z.string()).optional(),
  reason: z.string().optional(),
});
const zForged = z.object({
  found: z.boolean(),
  tools: z.array(z.object({
    name: z.string(), description: z.string().optional(),
    code: z.string(), deterministic: z.boolean().optional(),
  })).default([]),
  skills: z.array(z.object({ skill_id: z.string(), body: z.string() })).default([]),
});
const zPaletteStage = z.object({
  id: z.string(), requires: z.array(z.string()), effect: z.string(), trust: z.string(),
});
const zPipeline = z.object({
  available: z.boolean(),
  current: z.array(z.object({ stage: z.string(), config: z.record(z.unknown()).optional() })),
  palette: z.array(zPaletteStage),
  default: z.array(z.string()),
  active_enabled: z.boolean().optional(),
});

export type Traces = z.infer<typeof zTraces>;
export type Turn = z.infer<typeof zTurn>;
export type Stage = z.infer<typeof zStage>;
export type Assembly = z.infer<typeof zAssembly>;
export type Forged = z.infer<typeof zForged>;
export type Pipeline = z.infer<typeof zPipeline>;

// ── react-query hooks (what components use) ────────────────────────────────────
export function useTraces(limit = 50) {
  return useQuery({
    queryKey: ["vibe", "traces", limit],
    queryFn: () => getJSON(`/traces?limit=${limit}`, zTraces),
    refetchInterval: 15_000,
  });
}
export function useBrief(sha: string | null | undefined) {
  return useQuery({
    queryKey: ["vibe", "brief", sha],
    enabled: !!sha,
    queryFn: () => getJSON(`/explain/${sha}`, zExplain),
  });
}
export function useAssembly(turnId: string | null | undefined) {
  return useQuery({
    queryKey: ["vibe", "prompt", turnId],
    enabled: !!turnId,
    queryFn: () => getJSON(`/prompt/${turnId}`, zAssembly),
  });
}
export function useForged(turnId: string | null | undefined) {
  return useQuery({
    queryKey: ["vibe", "forged", turnId],
    enabled: !!turnId,
    queryFn: () => getJSON(`/forged/${turnId}`, zForged),
  });
}
export function usePipeline() {
  return useQuery({
    queryKey: ["vibe", "pipeline"],
    queryFn: () => getJSON("/pipeline", zPipeline),
  });
}
export function useSavePipeline() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (pipeline: Array<{ stage: string; config?: Record<string, unknown> }>) => {
      const csrf = await getCsrf();
      const r = await fetch(`${BASE}/pipeline`, {
        method: "PUT",
        credentials: "include",
        headers: { "content-type": "application/json", "x-csrf-token": csrf },
        body: JSON.stringify({ pipeline }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d?.detail || `HTTP ${r.status}`);
      return d;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["vibe", "pipeline"] }),
  });
}
