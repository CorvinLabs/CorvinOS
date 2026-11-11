/**
 * Roles, data classes and assistant access - the words, the grouping and the small decisions the Projects
 * UI makes about them (CONCEPT-0127). Pure, so a wrong mapping is caught by a unit test and not by a user.
 *
 * Perception choices encoded here: nine roles in three families (chunking, Hick's law), plain-words
 * outcomes instead of feature jargon (recognition over recall), and every class carries a LABEL and a SHAPE
 * (icon id) besides its tone, so colour is never the only signal.
 */
import type { Access, DataClass, ProjectSummary, Role, TemplateInfo } from "@/lib/api/projects";
import { ApiError } from "@/lib/api/client";
import { featureLabel } from "./encodings";

export const ROLE_LABEL: Record<Role, string> = {
  software: "Software engineering",
  data_science: "Data science",
  marketing: "Marketing",
  product: "Product management",
  sales: "Sales",
  legal: "Legal and compliance",
  video: "Video engineering",
  research: "Research",
  knowledge: "Knowledge",
};

export interface Family { id: string; label: string; roles: Role[] }

/** Three rows by job family; the order inside a row is the order users reach for them. */
export const FAMILIES: Family[] = [
  { id: "build", label: "Build", roles: ["software", "data_science", "video"] },
  { id: "grow", label: "Grow", roles: ["marketing", "product", "sales"] },
  { id: "govern", label: "Govern and learn", roles: ["legal", "research", "knowledge"] },
];

export const CLASS_ORDER: DataClass[] = ["public", "internal", "confidential", "secret"];

/** `shape` is an icon id drawn next to the label; `tone` only adds emphasis. */
export const CLASS_META: Record<DataClass, { label: string; shape: "globe" | "building" | "lock" | "shield"; tone: "plain" | "care" | "strict"; hint: string }> = {
  public: { label: "Public", shape: "globe", tone: "plain", hint: "May leave the machine; no restriction." },
  internal: { label: "Internal", shape: "building", tone: "plain", hint: "Work material; the default." },
  confidential: { label: "Confidential", shape: "lock", tone: "care", hint: "Only engines your data policy allows may read it." },
  secret: { label: "Secret", shape: "shield", tone: "strict", hint: "Never leaves the machine; refused for cloud engines." },
};

export const ACCESS_META: Record<Access, { label: string; short: string; hint: string }> = {
  read: { label: "Assistant reads only", short: "Read only", hint: "Corvin can read this project but not change its files." },
  write: { label: "Assistant can edit files", short: "Can edit", hint: "Corvin may create and change files in this project." },
};

export function roleLabel(r: string | undefined | null): string {
  return r && r in ROLE_LABEL ? ROLE_LABEL[r as Role] : "";
}

export function classOf(p: { classification?: DataClass }): DataClass {
  return p.classification && p.classification in CLASS_META ? p.classification : "internal";
}

export function accessOf(p: { assistant_access?: Access }): Access {
  return p.assistant_access === "write" ? "write" : "read";
}

/** What "Use in a chat" says the assistant may do there - from the project's own access, never a fixed sentence (REVIEW-0067 M3). */
export function chatAccessSentence(p: { assistant_access?: Access }): string {
  return accessOf(p) === "write" ? "The assistant may create and change files in the project." : "The assistant can read the project but not change its files.";
}

/** The backend rule (attachment.spawn_parts): a feature's MCP tools are handed to an attached chat only with write access. */
export const TOOLS_NEED_EDIT = "Its tools reach a chat only when the assistant may edit files.";

interface ToolRow { id: string; title?: string; tools?: boolean; active?: boolean }

/** Where "what the assistant may do" is changed: the Protection section of the project's Settings tab. */
export function protectionHref(id: string): string {
  return `/app/projects/${encodeURIComponent(id)}?tab=settings#settings-protection`;
}

/** The note under a feature row that brings tools, while the assistant reads only; null when there is nothing to say. */
export function featureToolsNote(f: ToolRow, p: { assistant_access?: Access }): string | null {
  if (!f.tools || accessOf(p) === "write") return null;
  return f.active ? `On, but its tools are not given to a chat. ${TOOLS_NEED_EDIT}` : TOOLS_NEED_EDIT;
}

/** The active features whose tools an attached chat does not get, because the assistant reads only. */
export function toolsWithheldFeatures(d: { assistant_access?: Access; catalogue?: { features?: ToolRow[] } }): string[] {
  if (accessOf(d) === "write") return [];
  return (d.catalogue?.features ?? []).filter((f) => f.tools && f.active).map((f) => featureLabel(f.id, f.title));
}

/** What "Use in a chat" says about the tools of a read-only project; null when every tool reaches the chat (or there are none). */
export function chatToolsNote(d: { assistant_access?: Access; catalogue?: { features?: ToolRow[] } }): string | null {
  const names = toolsWithheldFeatures(d);
  if (!names.length) return null;
  const list = names.length === 1 ? names[0] : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
  return `The tools of ${list} are not given to a chat while the assistant reads only. ${TOOLS_NEED_EDIT}`;
}

/** Lowering the class, or letting the assistant write, is the direction that needs a second look. */
export function lowersProtection(from: DataClass, to: DataClass, fromAccess: Access, toAccess: Access): boolean {
  return CLASS_ORDER.indexOf(to) < CLASS_ORDER.indexOf(from) || (fromAccess === "read" && toAccess === "write");
}

export interface TemplateGroup { id: string; label: string; items: TemplateInfo[] }

/** Templates grouped by job family, empty families dropped; a template without a known role goes last under "Other". */
export function groupTemplates(templates: TemplateInfo[]): TemplateGroup[] {
  const known = new Set<string>(FAMILIES.flatMap((f) => f.roles));
  const out: TemplateGroup[] = [];
  for (const fam of FAMILIES) {
    const items = fam.roles.flatMap((r) => templates.filter((t) => t.role === r));
    if (items.length) out.push({ id: fam.id, label: fam.label, items });
  }
  const rest = templates.filter((t) => !t.role || !known.has(t.role));
  if (rest.length) out.push({ id: "other", label: "Other", items: rest });
  return out;
}

export interface Filter { role: Role | "all"; cls: DataClass | "all" }
export const NO_FILTER: Filter = { role: "all", cls: "all" };

export function filterProjects(list: ProjectSummary[], f: Filter): ProjectSummary[] {
  return list.filter((p) => (f.role === "all" || p.role === f.role) && (f.cls === "all" || classOf(p) === f.cls));
}

/** Roles and classes actually present, so the filter never offers an empty choice. */
export function presentValues(list: ProjectSummary[]): { roles: Role[]; classes: DataClass[] } {
  const roles = FAMILIES.flatMap((f) => f.roles).filter((r) => list.some((p) => p.role === r));
  const classes = CLASS_ORDER.filter((c) => list.some((p) => classOf(p) === c));
  return { roles, classes };
}

/** One line for a place with no room for badges (the chat button): "Internal · read only". */
export function badgeLine(p: { classification?: DataClass; assistant_access?: Access }): string {
  return `${CLASS_META[classOf(p)].label} · ${accessOf(p) === "read" ? "read only" : "can edit"}`;
}

/** The server answers 409 `confirmation_required` when a change lowers protection; that is a question, not an error. */
export function needsConfirmation(err: unknown): boolean {
  if (!(err instanceof ApiError) || err.status !== 409) return false;
  const body = err.detail as { detail?: { reason_class?: string } } | undefined;
  return body?.detail?.reason_class === "confirmation_required";
}
