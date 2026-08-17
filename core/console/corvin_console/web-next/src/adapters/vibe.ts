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
// Observability rule: a trace panel must NEVER blank out on backend drift. The inner
// object schemas stay STRICT (so consumers keep clean non-null types), but each is
// wrapped in `z.preprocess()` that normalises the backend's legitimate nulls (a
// still-running or errored stage emits stage=null / status=null) to placeholders
// BEFORE validation. A single null degrades to a placeholder for ONE field instead of
// throwing and wiping the whole page. (Regression "context pipeline läd nicht":
// backend shipped a stage with stage=null and strict z.string() sank the entire render.)
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

// Observability rule: a trace panel must NEVER blank out on backend drift. The schemas
// above stay STRICT (consumers keep clean non-null types), and `scrubTraces` normalises
// the backend's legitimate nulls (a still-running or errored stage emits stage=null /
// status=null) to placeholders BEFORE the strict parse. A single null degrades to a
// placeholder for ONE field instead of throwing and wiping the whole page. (Regression
// "context pipeline läd nicht": backend shipped a stage with stage=null and strict
// z.string() sank the entire render.)
// Drop every key whose value is null so an `.optional()` field (confidence_tier,
// reason, hash, top_score, ts, …) is treated as absent rather than tripping strict
// z.string()/z.number(). Fields the schema declares `.nullable()` still accept null,
// so dropping their null is equally fine (undefined ⊆ nullable-optional).
function dropNulls(o: any): void {
  if (!o || typeof o !== "object") return;
  for (const k of Object.keys(o)) if (o[k] === null) delete o[k];
}
function scrubTraces(raw: unknown): unknown {
  const r = raw as any;
  if (!r || typeof r !== "object" || !Array.isArray(r.sessions)) return raw;
  for (const sg of r.sessions) {
    for (const t of sg?.turns ?? []) {
      dropNulls(t);
      if (t.turn_id == null) t.turn_id = "?";
      if (!Array.isArray(t.stages)) t.stages = [];
      for (const s of t.stages) {
        dropNulls(s);
        if (s.stage == null) s.stage = "?";
        if (s.status == null) s.status = "unknown";
        for (const src of s.sources ?? []) {
          dropNulls(src);
          if (src.id == null) src.id = "?";
        }
      }
    }
  }
  return raw;
}
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
    queryFn: async () => {
      const r = await fetch(`${BASE}/traces?limit=${limit}`, { credentials: "include" });
      if (!r.ok) throw new Error(`HTTP ${r.status} on /traces`);
      return zTraces.parse(scrubTraces(await r.json()));
    },
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
// ── CEL stage grades (G3) ───────────────────────────────────────────────────────
const zGrades = z.object({
  available: z.boolean(),
  grades: z.record(z.object({
    n_grades: z.number(), mean_score: z.number(), promoting: z.number().optional(),
  })),
});
export type StageGrades = z.infer<typeof zGrades>;

export function useStageGrades() {
  return useQuery({
    queryKey: ["vibe", "grades"],
    queryFn: () => getJSON("/grades", zGrades),
    staleTime: 10_000,
  });
}

export function useGradeStage() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ stage, score, notes }: { stage: string; score: number; notes?: string }) => {
      const csrf = await getCsrf();
      const r = await fetch(`${BASE}/grades/${encodeURIComponent(stage)}`, {
        method: "POST",
        credentials: "include",
        headers: { "content-type": "application/json", "x-csrf-token": csrf },
        body: JSON.stringify({ score, notes: notes ?? "" }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d?.detail || `HTTP ${r.status}`);
      return d;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["vibe", "grades"] }),
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
