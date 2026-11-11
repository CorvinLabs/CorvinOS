/**
 * Projects API - routes/projects.py (core/projects, ADR-2315).
 *
 * The tenant is always the session's. A project's files belong to the user; the only
 * destructive call here removes a registry entry, never a file.
 */
import { api } from "./client";

export type ProjectState = "ok" | "missing" | "invalid" | "pending";
export type Role = "software" | "data_science" | "marketing" | "product" | "sales" | "legal" | "video" | "research" | "knowledge";
export type DataClass = "public" | "internal" | "confidential" | "secret";
export type Access = "read" | "write";

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
  /** Corvin itself: a built-in project that can only be looked at (CONCEPT-0129). */
  builtin?: boolean;
  role?: Role | "";
  classification?: DataClass;
  assistant_access?: Access;
  /** Where the folder lives: in the Corvin workspace, in a folder of the user's own, or Corvin itself. */
  storage?: Storage;
  /** The path shortened for display (`Corvin workspace › alpha`, `~/code/alpha`, `D:\work\alpha`); `path` is the full one. */
  display_path?: string;
}

export type Storage = "managed" | "own" | "builtin";
/** Where NEW projects go: the Corvin workspace (`managed`) or the projects folder (`root`). */
export type DefaultStorage = "managed" | "root";
/** The managed workspace of this tenant: usable, copied from another installation, or not available on this machine. */
export type WorkspaceState = "ok" | "foreign" | "unavailable";

export interface ProjectList {
  projects_root: string;
  projects: ProjectSummary[];
  default_storage?: DefaultStorage;
  managed_root?: string;
  /** false: `projects_root` is only the fallback (~/projects), the user never chose a projects folder */
  projects_root_set?: boolean;
  workspace?: WorkspaceState;
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
  role?: Role | "";
  classification?: DataClass;
  assistant_access?: Access;
  /** What this role typically produces, in plain words. */
  outcome?: string;
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
  role?: Role | "";
  classification?: DataClass;
  assistant_access?: Access;
  /** managed = the Corvin workspace; own = any other folder */
  storage?: Storage;
}

export interface PlanRequest {
  template: string;
  title: string;
  project_id?: string;
  location?: string;
  features?: string[];
  docs_mode?: "nested" | "plain";
  classification?: DataClass;
  assistant_access?: Access;
  /** The place without a path: the Corvin workspace or the projects folder (ignored when `location` is given). */
  storage?: DefaultStorage;
}

export function planProject(body: PlanRequest, csrf: string): Promise<ProjectPlan> {
  return api<ProjectPlan>("/projects/plan", { method: "POST", body, csrf });
}

export function createProject(planId: string, csrf: string): Promise<{ project: ProjectSummary; written: string[]; actions: string[] }> {
  return api("/projects", { method: "POST", body: { plan_id: planId }, csrf });
}

/** `note`: an observation that is not a failure (for example a hint word in the path). */
export interface HealthCheck { id: string; ok: boolean; detail: string; at: string; note?: boolean }
export interface Health { project_id: string; ok: boolean; checks: HealthCheck[] }

export interface FeatureRow {
  id: string;
  title: string;
  description: string;
  active: boolean;
  requires: string[];
  suggests: string[];
  dependants: string[];
  plugins: PluginNeed[];
}

export interface ProjectDetail extends ProjectSummary {
  catalogue?: { available: boolean; reason?: string; features?: FeatureRow[]; suggestions?: { feature: string; suggests: string }[] };
  warnings?: string[];
  /** the peers this project may ask (directory.peers of project.yaml) */
  directory_peers?: string[];
  /** notes on the folder's place that do not refuse it (an own folder whose path contains a hint word) */
  location_warnings?: { class: string; message: string }[];
}

export interface StructureEntry { path: string; kind: "file" | "dir" | "symlink"; size: number | null }
export interface VideoRow { name: string; de: boolean; en: boolean; folder: string; state: string; folder_exists: boolean }
export interface ProjectStructure {
  project_id: string;
  entries: StructureEntry[];
  truncated: boolean;
  kb_counts: Record<string, number>;
  videos: VideoRow[];
  videos_root: string;
}

export function getStructure(id: string, signal?: AbortSignal): Promise<ProjectStructure> {
  return api<ProjectStructure>(`/projects/${encodeURIComponent(id)}/structure`, { signal });
}

export interface FeatureRequest {
  set: Record<string, boolean>;
  accept_dependencies?: boolean;
  cascade?: boolean;
}

export function getProject(id: string, signal?: AbortSignal): Promise<ProjectDetail> {
  return api<ProjectDetail>(`/projects/${encodeURIComponent(id)}`, { signal });
}

export function getHealth(id: string, signal?: AbortSignal): Promise<Health> {
  return api<Health>(`/projects/${encodeURIComponent(id)}/health`, { signal });
}

export function setFeatures(id: string, body: FeatureRequest, csrf: string): Promise<ProjectDetail> {
  return api<ProjectDetail>(`/projects/${encodeURIComponent(id)}/features`, { method: "PUT", body, csrf });
}

export function repairProject(id: string, dryRun: boolean, csrf: string): Promise<{ pending: { path: string; status: string; diff: string }[]; dry_run: boolean }> {
  return api(`/projects/${encodeURIComponent(id)}/repair`, { method: "POST", body: { dry_run: dryRun }, csrf });
}

/** One `cd` per shell, each quoted so nothing in a folder name is evaluated. `cmd` is null when the name holds `%` or `!` (cmd.exe expands
 * those even inside quotes). `command` is the one for this machine's usual shell (PowerShell on Windows). */
export interface OpenCommands { powershell: string; cmd: string | null; posix: string }

export function openProject(id: string, csrf: string): Promise<{ command: string; commands?: OpenCommands; chat_attachment: boolean; note: string }> {
  return api(`/projects/${encodeURIComponent(id)}/open`, { method: "POST", csrf });
}

/** Removes the registry entry ONLY. The directory and every file stay. */
export function removeProjectFromList(id: string, csrf: string): Promise<{ removed: string; files_deleted: boolean }> {
  return api(`/projects/${encodeURIComponent(id)}`, { method: "DELETE", csrf });
}

export interface NewVideoResult {
  video: { name: string; path: string; languages: string[]; kb: boolean; epic: string | null; tasks: string[] };
  note: string;
}

/** Creates one video's folder, sources.md, index row and (with the knowledge base on) its epic and tasks. */
export function createVideo(projectId: string, name: string, csrf: string): Promise<NewVideoResult> {
  return api<NewVideoResult>(`/projects/${encodeURIComponent(projectId)}/videos`, { method: "POST", body: { name }, csrf });
}

/** Registers an existing folder. A folder without project.yaml gets exactly that one file; nothing else in it is touched. */
export function adoptProject(body: { location: string; title?: string; project_id?: string }, csrf: string): Promise<ProjectDetail> {
  return api<ProjectDetail>("/projects/adopt", { method: "POST", body, csrf });
}

/** Changes the title only; the id never changes. */
export function renameProject(id: string, title: string, csrf: string): Promise<ProjectDetail> {
  return api<ProjectDetail>(`/projects/${encodeURIComponent(id)}/title`, { method: "PUT", body: { title }, csrf });
}

/** Moves the folder to a new place on the same disk and follows it in the list; machine-local files are regenerated. A move to another
 *  disk is refused (409 `cross_device`) and changes nothing. */
export function moveProject(id: string, location: string, csrf: string): Promise<ProjectDetail & { repaired_files: number | null; warnings?: string[] }> {
  return api(`/projects/${encodeURIComponent(id)}/move`, { method: "POST", body: { location }, csrf });
}

export interface ChatAttachment { session_id: string; project_id: string | null; title: string | null; withheld?: string | null }

/** Which project (if any) one console chat is attached to. */
export function getChatAttachment(sessionId: string, signal?: AbortSignal): Promise<ChatAttachment> {
  return api<ChatAttachment>(`/projects/chat-attachment?session_id=${encodeURIComponent(sessionId)}`, { signal });
}

/** Attaches the project to one chat: its rules and tools become available there; its files stay read-only. */
export function attachProject(id: string, sessionId: string, csrf: string): Promise<{ attached: boolean; note: string }> {
  return api(`/projects/${encodeURIComponent(id)}/attach`, { method: "POST", body: { session_id: sessionId }, csrf });
}

export function detachProject(sessionId: string, csrf: string): Promise<{ attached: boolean }> {
  return api("/projects/detach", { method: "POST", body: { session_id: sessionId }, csrf });
}


/** Changes the data class and/or what the assistant may do. Lowering the class or raising access answers 409
 *  `confirmation_required` until `confirm` is true; the server decides, the UI only asks. */
export function setProjectSettings(
  id: string, body: { classification?: DataClass; assistant_access?: Access; confirm?: boolean }, csrf: string,
): Promise<ProjectDetail> {
  return api<ProjectDetail>(`/projects/${encodeURIComponent(id)}/settings`, { method: "PUT", body, csrf });
}

export type ChangeStatus = "modified" | "added" | "deleted" | "renamed" | "untracked" | "committed";
export interface ChangeLink { id: string; kind: "task" | "knowledge" }
export interface ChangedFile {
  path: string;
  status: ChangeStatus;
  additions: number | null;
  deletions: number | null;
  area: "code" | "docs" | "knowledge";
  links: ChangeLink[];
}
export interface ChangeCommit { sha: string; short: string; repo: string; author: string; at: number; subject: string; links: ChangeLink[] }
export interface ChangeList {
  project_id: string;
  commit: string | null;
  files: ChangedFile[];
  commits: ChangeCommit[];
  has_repository: boolean;
  /** why a folder that has a .git is not listed (worktree checkout, shared objects, a link, too large); null otherwise */
  repository_note?: string | null;
  truncated: boolean;
}
export interface ChangeFileView {
  path: string;
  status: ChangeStatus;
  area: string;
  binary: boolean;
  truncated: boolean;
  diff: string;
  text: string | null;
}

export function getChanges(id: string, commit?: string | null, signal?: AbortSignal): Promise<ChangeList> {
  const q = commit ? `?commit=${encodeURIComponent(commit)}` : "";
  return api<ChangeList>(`/projects/${encodeURIComponent(id)}/changes${q}`, { signal });
}

export function getChangeFile(id: string, path: string, commit?: string | null, signal?: AbortSignal): Promise<ChangeFileView> {
  const q = `?path=${encodeURIComponent(path)}${commit ? `&commit=${encodeURIComponent(commit)}` : ""}`;
  return api<ChangeFileView>(`/projects/${encodeURIComponent(id)}/changes/file${q}`, { signal });
}

// ── live work on a project (CONCEPT-0128, ADR-2320) ──────────────────────────────────────────

export type ActivityKind = "turn_started" | "tool_use" | "turn_finished" | "file_changed" | "file_settled" | "files_burst";
export interface ActivityEvent {
  seq: number;
  id: string;
  ts: number;
  kind: ActivityKind;
  session_id?: string;
  title?: string;
  name?: string;
  detail?: string;
  sub?: boolean;
  ok?: boolean;
  reason?: string;
  path?: string;
  status?: string;
  area?: string;
  additions?: number | null;
  deletions?: number | null;
  count?: number;
  links?: ChangeLink[];
}
export interface ActivityAgent {
  session_id: string;
  title: string;
  state: "working" | "idle";
  since: number;
  tool: string | null;
  detail: string;
  tools: number;
  finished?: number;
  ok?: boolean;
}
export interface ActivitySnapshot {
  epoch: string;
  seq: number;
  oldest_seq: number;
  reset: boolean;
  gap: boolean;
  events: ActivityEvent[];
  agents: ActivityAgent[];
  watcher: "off" | "running" | "paused";
  note: string;
}

/** The snapshot: agents and the backlog after `after` ("<epoch>:<seq>"). Cheap; it starts no watcher on the server. */
export function getActivity(id: string, after?: string | null, signal?: AbortSignal): Promise<ActivitySnapshot> {
  const q = after ? `?after=${encodeURIComponent(after)}` : "";
  return api<ActivitySnapshot>(`/projects/${encodeURIComponent(id)}/activity${q}`, { signal });
}

export function activityStreamUrl(id: string, after?: string | null): string {
  return `/v1/console/projects/${encodeURIComponent(id)}/activity/stream${after ? `?after=${encodeURIComponent(after)}` : ""}`;
}

/** Changes where new projects are created. Projects already outside the new folder are named and keep working. */
export function setProjectsRoot(path: string, csrf: string): Promise<{ projects_root: string; outside: string[] }> {
  return api("/projects/root", { method: "PUT", body: { path }, csrf });
}

export interface StorageSettings { default_storage: DefaultStorage; projects_root: string | null; managed_root: string; outside: string[] }

/** Where NEW projects go. Nothing is moved; projects already somewhere else keep working. `root` needs a folder (409 `root_required`). */
export function setDefaultStorage(body: { default_storage: DefaultStorage; projects_root?: string }, csrf: string): Promise<StorageSettings> {
  return api<StorageSettings>("/projects/settings/storage", { method: "PUT", body, csrf });
}

/** Starts a new, empty workspace for this installation when the current one belongs to another installation (409 `not_foreign`). */
export function rekeyWorkspace(csrf: string): Promise<StorageSettings> {
  return api<StorageSettings>("/projects/settings/storage", { method: "PUT", body: { rekey: true }, csrf });
}

/** "Locate folder": points the list at the folder that now holds this project's project.yaml. Moves nothing. */
export function relocateProject(id: string, location: string, csrf: string): Promise<ProjectDetail & { repaired_files: number | null; warnings?: string[] }> {
  return api(`/projects/${encodeURIComponent(id)}/relocate`, { method: "POST", body: { location }, csrf });
}
