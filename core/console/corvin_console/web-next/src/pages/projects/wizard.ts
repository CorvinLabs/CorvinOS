/**
 * The new-project wizard's decisions, kept pure so they are unit-tested: which features a template starts
 * with, what a step may advance past, and how the server's error becomes a sentence the user can act on.
 */
import type { Access, DataClass, DefaultStorage, PlanRequest, ProjectPlan, TemplateCatalogue, TemplateInfo } from "@/lib/api/projects";
import { ApiError } from "@/lib/api/client";
import { featureLabel } from "./encodings";

export type Step = 1 | 2 | 3 | 4;
export type Place = "managed" | "root" | "custom";

export interface WizardState {
  step: Step;
  templateId: string | null;
  title: string;
  /** Optional id; empty = the server derives it from the title. */
  projectId: string;
  /** Where the folder goes: the Corvin workspace, the projects folder (only when one is set), or a folder the user names. */
  place: Place;
  /** Only read when `place` is "custom". */
  location: string;
  features: string[];
  docsMode: "nested" | "plain";
  /** Starts from the template's default (a role proposes, the user decides). */
  classification: DataClass;
  access: Access;
}

export const INITIAL: WizardState = { step: 1, templateId: null, title: "", projectId: "", place: "managed", location: "", features: [],
  docsMode: "nested", classification: "internal", access: "read" };

/** The wizard starts at the tenant's default place; the projects folder only while one is set. */
export function initialPlace(defaultStorage: DefaultStorage | undefined, rootSet: boolean): Place {
  return defaultStorage === "root" && rootSet ? "root" : "managed";
}

export function templateById(cat: TemplateCatalogue | undefined, id: string | null): TemplateInfo | undefined {
  return cat?.templates.find((t) => t.id === id);
}

/** Choosing a template starts from its default features and its default docs mode. */
export function chooseTemplate(s: WizardState, cat: TemplateCatalogue | undefined, id: string): WizardState {
  const t = templateById(cat, id);
  return { ...s, templateId: id, features: t ? [...t.features] : [], docsMode: t?.docs_mode ?? "nested",
    classification: t?.classification ?? "internal", access: t?.assistant_access ?? "read" };
}

/** A feature the user toggles: switching on keeps the list requires-closed in the UI; the server confirms. */
export function toggleFeature(s: WizardState, cat: TemplateCatalogue | undefined, id: string): WizardState {
  if (id === "core") return s;                                     // core is always on
  const on = s.features.includes(id);
  if (on) return { ...s, features: s.features.filter((f) => f !== id) };
  const need = requiredClosure(cat, id).filter((f) => !s.features.includes(f));
  return { ...s, features: [...s.features, ...need, id] };   // the requirements first, then the feature
}

/** The features `id` pulls in (transitively), in order, without `id` itself. */
export function requiredClosure(cat: TemplateCatalogue | undefined, id: string, seen = new Set<string>()): string[] {
  if (!cat || seen.has(id)) return [];
  seen.add(id);
  const f = cat.features.find((x) => x.id === id);
  if (!f) return [];
  const out: string[] = [];
  for (const r of f.requires) {
    for (const x of requiredClosure(cat, r, seen)) if (!out.includes(x)) out.push(x);
    if (!out.includes(r)) out.push(r);
  }
  return out;
}

/** Features the template offers, minus core (never a choice). */
export function choosable(t: TemplateInfo): string[] {
  return t.offered.filter((f) => f !== "core");
}

export function canAdvance(s: WizardState, plan: ProjectPlan | undefined): boolean {
  switch (s.step) {
    case 1: return s.templateId !== null;
    case 2: return s.title.trim().length > 0 && s.title.trim().length <= 120 && (s.place !== "custom" || s.location.trim().length > 0);
    case 3: return s.features.length > 0;
    case 4: return !!plan && plan.ready;
  }
}

export function planRequest(s: WizardState): PlanRequest {
  return {
    template: s.templateId ?? "",
    title: s.title.trim(),
    ...(s.projectId.trim() ? { project_id: s.projectId.trim() } : {}),
    ...(s.place === "custom" ? (s.location.trim() ? { location: s.location.trim() } : {}) : { storage: s.place }),
    features: s.features,
    docs_mode: s.docsMode,
    classification: s.classification,
    assistant_access: s.access,
  };
}

/** The server's refusal as one sentence; an unknown error never shows a raw status code.
 * `context` picks the fallback: the wizard creates a project, every other caller changes one that exists. A failure without an
 * answer (network, timeout, console restarting) never claims "nothing was written": the request may have been applied. */
export function explain(err: unknown, context: "wizard" | "action" = "action"): string {
  if (err instanceof ApiError) {
    // ApiError.detail is the whole response body: { detail: <string | { message, reason_class } | [{ msg }, ...]> }
    const body = err.detail as { detail?: unknown } | undefined;
    const inner = body && typeof body === "object" && "detail" in body ? body.detail : body;
    if (Array.isArray(inner)) {                     // FastAPI validation errors: one line per field
      const msgs = inner
        .map((x) => (x && typeof x === "object" && typeof (x as { msg?: unknown }).msg === "string" ? (x as { msg: string }).msg : ""))
        .filter(Boolean);
      if (msgs.length) return `The input was refused: ${msgs.join("; ")}.`;
    }
    const msg = typeof inner === "string" ? inner : (inner as { message?: unknown } | undefined)?.message;
    if (typeof msg === "string" && msg) return msg;
    if (err.status === 403) return "Your session expired. Sign in again.";
    if (context === "wizard") {
      if (err.status === 404) return "That template or plan no longer exists. Start again.";
      if (err.status === 422) return "The location was refused. Choose another folder.";
      if (err.status === 409) return "Something changed while you were planning. Review the plan again.";
      return "The project could not be created. Nothing was written.";
    }
    if (err.status === 404) return "This project is no longer in your list.";
    if (err.status === 422) return "The server refused the input.";
    if (err.status === 409) return "The project changed in the meantime. Reload the page and try again.";
    return "The server refused the change.";
  }
  return context === "wizard"
    ? "No answer from the console. Reload the page to see whether the project was created."
    : "No answer from the console. The change may or may not have been applied - reload the page to see the current state.";
}

/** A refusal that is about the id (a hint word in a managed id, an id already taken): its sentence goes under the id field. */
export function idProblem(err: unknown): string | null {
  if (!(err instanceof ApiError)) return null;
  const body = err.detail as { detail?: { reason_class?: unknown; message?: unknown } } | undefined;
  const d = body?.detail;
  if (!d || typeof d !== "object") return null;
  if (d.reason_class !== "hint_word" && d.reason_class !== "id_taken") return null;
  return typeof d.message === "string" && d.message ? d.message : null;
}

/** Human names for the review list. */
export function featureNames(ids: string[]): string[] {
  return ids.filter((f) => f !== "core").map(featureLabel);
}
