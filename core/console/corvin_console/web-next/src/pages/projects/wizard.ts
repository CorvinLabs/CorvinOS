/**
 * The new-project wizard's decisions, kept pure so they are unit-tested: which features a template starts
 * with, what a step may advance past, and how the server's error becomes a sentence the user can act on.
 */
import type { PlanRequest, ProjectPlan, TemplateCatalogue, TemplateInfo } from "@/lib/api/projects";
import { ApiError } from "@/lib/api/client";
import { featureLabel } from "./encodings";

export type Step = 1 | 2 | 3 | 4;

export interface WizardState {
  step: Step;
  templateId: string | null;
  title: string;
  location: string;
  features: string[];
  docsMode: "nested" | "plain";
}

export const INITIAL: WizardState = { step: 1, templateId: null, title: "", location: "", features: [], docsMode: "nested" };

export function templateById(cat: TemplateCatalogue | undefined, id: string | null): TemplateInfo | undefined {
  return cat?.templates.find((t) => t.id === id);
}

/** Choosing a template starts from its default features and its default docs mode. */
export function chooseTemplate(s: WizardState, cat: TemplateCatalogue | undefined, id: string): WizardState {
  const t = templateById(cat, id);
  return { ...s, templateId: id, features: t ? [...t.features] : [], docsMode: t?.docs_mode ?? "nested" };
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
    case 2: return s.title.trim().length > 0 && s.title.trim().length <= 120;
    case 3: return s.features.length > 0;
    case 4: return !!plan && plan.ready;
  }
}

export function planRequest(s: WizardState): PlanRequest {
  return {
    template: s.templateId ?? "",
    title: s.title.trim(),
    ...(s.location.trim() ? { location: s.location.trim() } : {}),
    features: s.features,
    docs_mode: s.docsMode,
  };
}

/** The server's refusal as one sentence; an unknown error never shows a raw status code. */
export function explain(err: unknown): string {
  if (err instanceof ApiError) {
    // ApiError.detail is the whole response body: { detail: <string | { message, reason_class }> }
    const body = err.detail as { detail?: unknown } | undefined;
    const inner = body && typeof body === "object" && "detail" in body ? body.detail : body;
    const msg = typeof inner === "string" ? inner : (inner as { message?: unknown } | undefined)?.message;
    if (typeof msg === "string" && msg) return msg;
    if (err.status === 404) return "That template or plan no longer exists. Start again.";
    if (err.status === 422) return "The location was refused. Choose a folder inside your projects folder.";
    if (err.status === 409) return "Something changed while you were planning. Review the plan again.";
    if (err.status === 403) return "Your session expired. Sign in again.";
  }
  return "The project could not be created. Nothing was written.";
}

/** Human names for the review list. */
export function featureNames(ids: string[]): string[] {
  return ids.filter((f) => f !== "core").map(featureLabel);
}
