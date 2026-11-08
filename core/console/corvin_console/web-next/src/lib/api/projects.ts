/**
 * Projects API - routes/projects.py (core/projects, ADR-2315).
 *
 * The tenant is always the session's. A project's files belong to the user; the only
 * destructive call here removes a registry entry, never a file.
 */
import { api } from "./client";

export type ProjectState = "ok" | "missing" | "invalid";

export interface ProjectSummary {
  id: string;
  path: string;
  template: string;
  added_at?: string;
  state: ProjectState;
  /** Why the project is not `ok` (missing directory, unreadable project.yaml). */
  reason?: string;
  title?: string;
  docs_mode?: "nested" | "plain";
  /** Every feature the template offers, on or off. */
  features?: Record<string, boolean>;
  /** The features that are on. */
  active?: string[];
  /** docs/kb exists. */
  kb?: boolean;
}

export interface ProjectList {
  projects_root: string;
  projects: ProjectSummary[];
}

export interface PluginNeed {
  plugin: string;
  state: "absent" | "installed" | "enabled";
}

export interface TemplateFeature {
  id: string;
  title: string;
  description: string;
  requires: string[];
  suggests: string[];
  plugins: PluginNeed[];
}

export interface TemplateInfo {
  id: string;
  version: number;
  title: string;
  description: string;
  features: string[];
  offered: string[];
  docs_mode: "nested" | "plain";
}

export interface TemplateCatalogue {
  available: boolean;
  /** Present when the catalogue cannot be used (for example the template plugin is not enabled for the tenant). */
  reason?: string;
  projects_root?: string;
  templates: TemplateInfo[];
  features: TemplateFeature[];
}

export function listProjects(signal?: AbortSignal): Promise<ProjectList> {
  return api<ProjectList>("/projects", { signal });
}

export function listTemplates(signal?: AbortSignal): Promise<TemplateCatalogue> {
  return api<TemplateCatalogue>("/projects/templates", { signal });
}

export interface PlanFile { path: string; owner: string; kind: "seed" | "generated" | "local"; status: string }
export interface PlanAction { action: string; owner: string; status: string }
export interface PlanPlugin { plugin: string; state: PluginNeed["state"] }

/** The preview of a project: nothing is written until `createProject` gets its plan id. */
export interface ProjectPlan {
  plan_id: string;
  project_id: string;
  title: string;
  template: string;
  docs_mode: "nested" | "plain";
  location: string;
  features: string[];
  /** Features added because an active one requires them (the user should see them). */
  pulled: string[];
  files: PlanFile[];
  actions: PlanAction[];
  warnings: string[];
  conflicts: string[];
  plugins: PlanPlugin[];
  suggestions: { feature: string; suggests: string }[];
  ready: boolean;
  expires_in_s: number;
}

export interface PlanRequest {
  template: string;
  title: string;
  project_id?: string;
  location?: string;
  features?: string[];
  docs_mode?: "nested" | "plain";
}

export function planProject(body: PlanRequest, csrf: string): Promise<ProjectPlan> {
  return api<ProjectPlan>("/projects/plan", { method: "POST", body, csrf });
}

export function createProject(planId: string, csrf: string): Promise<{ project: ProjectSummary; written: string[]; actions: string[] }> {
  return api("/projects", { method: "POST", body: { plan_id: planId }, csrf });
}
