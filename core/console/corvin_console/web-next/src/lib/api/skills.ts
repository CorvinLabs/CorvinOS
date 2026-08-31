/**
 * api/skills — extracted from the former monolithic lib/api.ts.
 * Public surface is unchanged; re-exported via the ../api.ts barrel.
 */

import { api } from "./client";

// ── Forge tools + SkillForge skills ────────────────────────────────

export type PromoteTarget = "session" | "project" | "user";

export interface ForgeToolSummary {
  name: string;
  description: string;
  scope: string;
  scope_source: string;
  runtime: string;
  promoted: boolean;
  call_count: number;
  created_at: number | null;
  sha256: string;
  param_count: number;
  param_names: string[];
  required: string[];
  impl_path: string | null;
}

export interface ForgeToolListResponse {
  tenant_id: string;
  ts: number;
  count: number;
  tools: ForgeToolSummary[];
}

export async function listForgeTools(signal?: AbortSignal): Promise<ForgeToolListResponse> {
  return api<ForgeToolListResponse>("/tools", { signal });
}

export interface ForgeToolDetailResponse {
  name: string;
  scope_source: string;
  registry_path: string | null;
  entry: Record<string, unknown>;
  impl_preview: string | null;
}

export async function getForgeTool(
  name: string,
  signal?: AbortSignal,
): Promise<ForgeToolDetailResponse> {
  return api<ForgeToolDetailResponse>(`/tools/${encodeURIComponent(name)}`, { signal });
}

export async function promoteForgeTool(
  name: string,
  to: PromoteTarget,
  csrf: string,
  force = false,
): Promise<{ name: string; to: PromoteTarget; ok: true; promoted: true }> {
  return api(`/tools/${encodeURIComponent(name)}/promote`, {
    method: "POST",
    csrf,
    body: { to, force },
  });
}

export interface SkillSummary {
  name: string;
  scope: string;
  scope_source: string;
  type: string;
  description: string;
  created_at: number | null;
  grade_count: number;
  mean_score: number | null;
  sha256: string;
  skill_dir: string;
}

export interface SkillListResponse {
  tenant_id: string;
  ts: number;
  count: number;
  skills: SkillSummary[];
}

export async function listSkills(signal?: AbortSignal): Promise<SkillListResponse> {
  return api<SkillListResponse>("/skills", { signal });
}

export interface SkillDetailResponse {
  name: string;
  scope_source: string;
  skill_dir: string;
  meta: Record<string, unknown>;
  body_preview: string | null;
}

export async function getSkill(
  name: string,
  signal?: AbortSignal,
): Promise<SkillDetailResponse> {
  return api<SkillDetailResponse>(`/skills/${encodeURIComponent(name)}`, { signal });
}

export async function promoteSkill(
  name: string,
  to: PromoteTarget,
  csrf: string,
  force = false,
): Promise<{ name: string; to: PromoteTarget; ok: true; promoted: true }> {
  return api(`/skills/${encodeURIComponent(name)}/promote`, {
    method: "POST",
    csrf,
    body: { to, force },
  });
}


// ── ADR-0124 M5: Manual Skills ────────────────────────────────────────────────

export interface ManualSkill {
  name: string;
  scope: "user";
  origin: "manual";
  created_at: number | null;
  updated_at: number | null;
  sha256: string;
}

export interface ManualSkillListResponse {
  tenant_id: string;
  count: number;
  skills: ManualSkill[];
}

export async function listManualSkills(signal?: AbortSignal): Promise<ManualSkillListResponse> {
  return api<ManualSkillListResponse>("/skills/manual", { signal });
}

export async function createManualSkill(
  name: string,
  body_md: string,
  csrf: string,
): Promise<{ ok: boolean; name: string }> {
  return api("/skills/manual", { method: "POST", body: { name, body: body_md }, csrf });
}

export async function updateManualSkill(
  name: string,
  body_md: string,
  csrf: string,
): Promise<{ ok: boolean; name: string }> {
  return api(`/skills/manual/${encodeURIComponent(name)}`, {
    method: "PUT",
    body: { body: body_md },
    csrf,
  });
}

export async function deleteManualSkill(
  name: string,
  csrf: string,
): Promise<{ ok: boolean }> {
  return api(`/skills/manual/${encodeURIComponent(name)}`, { method: "DELETE", csrf });
}

// ── ADR-0124 M5b: Manual Tools ────────────────────────────────────────────────

export interface ManualTool {
  name: string;
  description: string;
  origin: "manual";
  sha256: string;
  runtime: "python";
  scope: "user";
  created_at: number;
  updated_at: number;
}

export interface ManualToolListResponse {
  tenant_id: string;
  count: number;
  tools: ManualTool[];
}

export async function listManualTools(signal?: AbortSignal): Promise<ManualToolListResponse> {
  return api<ManualToolListResponse>("/tools/manual", { signal });
}

export async function createManualTool(
  name: string,
  description: string,
  impl: string,
  csrf: string,
): Promise<{ ok: boolean; name: string }> {
  return api("/tools/manual", {
    method: "POST",
    body: { name, description, impl, input_schema: {} },
    csrf,
  });
}

export async function deleteManualTool(
  name: string,
  csrf: string,
): Promise<{ ok: boolean }> {
  return api(`/tools/manual/${encodeURIComponent(name)}`, { method: "DELETE", csrf });
}

export async function previewManualTool(
  name: string,
  inputs: Record<string, unknown>,
  csrf: string,
): Promise<{ ok: boolean; exit_code: number; stdout: string; stderr: string }> {
  return api("/tools/preview", { method: "POST", body: { name, inputs }, csrf });
}


// ── Skill-Creator (ADR-0405) ──────────────────────────────────────────────────
//
// Generated skills live in the tenant SkillForge registry. `injectable` is the
// load-bearing field: a registered skill with no grade sits below
// skill_inject's eligibility gate and is never injected into a turn.

export interface GeneratedSkillSummary {
  name: string;
  type: string;
  description: string;
  scope: string;
  created_by: string;
  n_grades: number;
  mean_score: number;
  injectable: boolean;
}

export interface GeneratedSkillDetail extends GeneratedSkillSummary {
  sha256: string;
  grades: Array<{ run_id?: string; score: number; ts?: number; notes?: string }>;
  body: string;
}

export interface GeneratedSkillList {
  tenant_id: string;
  count: number;
  injectable_count: number;
  skills: GeneratedSkillSummary[];
}

export async function listGeneratedSkills(
  signal?: AbortSignal,
): Promise<GeneratedSkillList> {
  return api<GeneratedSkillList>("/skill-creator/skills", { signal });
}

export async function getGeneratedSkill(
  name: string,
  signal?: AbortSignal,
): Promise<GeneratedSkillDetail> {
  return api<GeneratedSkillDetail>(
    `/skill-creator/skills/${encodeURIComponent(name)}`,
    { signal },
  );
}

export async function deleteGeneratedSkill(
  name: string,
  csrf: string,
): Promise<{ ok: boolean; name: string }> {
  return api(`/skill-creator/skills/${encodeURIComponent(name)}`, {
    method: "DELETE",
    csrf,
  });
}

export interface SkillGenerationAccepted {
  status: string;
  run_id: string;
  engine: string;
  base_skill: string | null;
  message: string;
}

/** Start a run. Passing `baseSkill` refines that skill in place. */
export async function startSkillGeneration(
  userRequest: string,
  baseSkill?: string | null,
): Promise<SkillGenerationAccepted> {
  return api<SkillGenerationAccepted>("/skill-creator/generate", {
    method: "POST",
    body: {
      user_request: userRequest,
      async: true,
      ...(baseSkill ? { base_skill: baseSkill } : {}),
    },
    // A generation run takes minutes on the engine, but this call only
    // ACCEPTS the run and returns a run_id — the default timeout is fine.
  });
}

export interface SkillReviewFinding {
  dimension: string;
  summary: string;
  verdict: string;
}

export interface SkillRunSkill {
  name: string;
  purpose: string;
  scope: string;
  quality: number;
  iterations: number;
  dependencies: string[];
  findings?: SkillReviewFinding[];
  injectable?: boolean;
  registry_path?: string;
}

export interface SkillRunStatus {
  run_id: string;
  status: "pending" | "running" | "success" | "failed";
  phase: string;
  progress: number;
  message: string;
  engine: string;
  phases: string[];
  error: string | null;
  base_skill: string | null;
  skill?: SkillRunSkill | null;
}

export async function getSkillRunStatus(
  runId: string,
  signal?: AbortSignal,
): Promise<SkillRunStatus> {
  return api<SkillRunStatus>(
    `/skill-creator/status/${encodeURIComponent(runId)}`,
    { signal },
  );
}
