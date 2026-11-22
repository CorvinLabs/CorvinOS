/** Video templates of the video producer: the list the server owns, and the pure helpers the picker draws with. */

export type TemplateCategory = "explain" | "persuade" | "engineer" | "research" | "format";

export interface VideoTemplate {
  id: string;
  name: { en: string; de: string };
  summary: { en: string; de: string };
  category: TemplateCategory;
  audience: string;
  tone: string;
  aspect: "16:9" | "9:16";
  available: boolean;
  unavailable_reason: string;
  duration: { target_s: number; min_s: number; max_s: number };
  scenes: { min: number; max: number };
  structure: Array<{ kind: string; purpose: string; required: boolean }>;
  hook: { kind: "question"; max_words: number } | null;
  evidence: "none" | "sources" | "cited_numbers";
  recommended_model: "haiku" | "sonnet" | "opus";
  /** "slides" (default) shapes a slide video; "website" is a narrated tour of the page whose address is in the brief. */
  mode?: "slides" | "website";
  input?: { kind: "url" } | null;
}

export interface VideoTemplateList {
  templates: VideoTemplate[];
  default_template_id: string;
  standard_template_id: string;
}

export const CATEGORY_LABEL: Record<TemplateCategory, string> = {
  explain: "Explain", persuade: "Persuade", engineer: "Engineering", research: "Research", format: "Formats",
};

/** "35 s", "1:15 min", "4 min": the length the template aims for. */
export function lengthLabel(seconds: number): string {
  if (seconds < 60) return `${seconds} s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return s === 0 ? `${m} min` : `${m}:${String(s).padStart(2, "0")} min`;
}

/** Templates grouped for the menu, in the server's order; a group appears only when it has a template. */
export function groupTemplates(list: VideoTemplate[]): Array<{ category: TemplateCategory; label: string; items: VideoTemplate[] }> {
  const order: TemplateCategory[] = [];
  for (const t of list) if (!order.includes(t.category)) order.push(t.category);
  return order.map((category) => ({ category, label: CATEGORY_LABEL[category], items: list.filter((t) => t.category === category) }));
}

/** The template a new video will use: an explicit pick, else the remembered one, else the standard one. A pick the
 *  server no longer lists (or can no longer produce) never survives. */
export function effectiveTemplateId(list: VideoTemplateList | undefined, picked: string | null): string | null {
  if (!list) return null;
  const usable = (id: string | null) => (id && list.templates.some((t) => t.id === id && t.available) ? id : null);
  return usable(picked) ?? usable(list.default_template_id) ?? list.standard_template_id;
}

export const templateName = (list: VideoTemplateList | undefined, id: string | null): string =>
  list?.templates.find((t) => t.id === id)?.name.en ?? "Standard";

/** One line under the name in the menu. */
export function templateFacts(t: VideoTemplate): string {
  const bits = [`about ${lengthLabel(t.duration.target_s)}`, t.aspect === "9:16" ? "vertical 9:16" : "16:9"];
  if (t.hook) bits.push("opens with a question");
  return bits.join(" · ");
}

/** A template whose job is not a slide video (the website tour): it needs more than text and is never remembered as the default. */
export const isTourTemplate = (list: VideoTemplateList | undefined, id: string | null): boolean =>
  list?.templates.find((t) => t.id === id)?.mode === "website";
