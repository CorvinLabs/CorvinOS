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
