/**
 * Projects API - routes/projects.py (core/projects, ADR-2315).
 *
 * The tenant is always the session's. A project's files belong to the user; the only
 * destructive call here removes a registry entry, never a file.
 */
import { ApiError, BASE, api, isCsrfError, notifyCsrfError } from "./client";

export type ProjectState = "ok" | "missing" | "invalid" | "pending" | "trashed";
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
  /** Set while an import of this project is not applied yet (state `pending`). */
  pending?: "import";
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
  storage?: Storage | "trashed";
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
  /** Projects whose folder is in the Trash (state `trashed`, reason "In Trash"): never in `projects`, listed for the scope switcher. */
  trashed?: ProjectSummary[];
  /** Entries in the Trash (GET /projects/-/trash). */
  trash_count?: number;
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

/** One entry of the Trash: a managed project's folder kept for 30 days (ADR-2321 Decision 12). */
export interface TrashEntry {
  entry: string;
  id: string;
  title: string;
  deleted_at: string | null;
  restorable_until: string | null;
  /** true while the move into the Trash has not finished: it cannot be restored or deleted yet. */
  pending: boolean;
}
/** One page of the Trash, newest first (REVIEW-0063 m6): `total` counts every entry, `next_offset` is null on the last page. */
export interface TrashList {
  entries: TrashEntry[];
  ttl_days: number;
  total?: number;
  offset?: number;
  limit?: number;
  next_offset?: number | null;
}

export function deleteProjectFiles(id: string, confirmId: string, csrf: string):
  Promise<{ project_id: string; trash_entry: string; deleted_at: string; restorable_until: string; warnings: string[] }> {
  return api(`/projects/${encodeURIComponent(id)}/files`, { method: "DELETE", body: { confirm_id: confirmId }, csrf });
}

export function listTrash(signal?: AbortSignal, offset: number = 0): Promise<TrashList> {
  return api<TrashList>(offset > 0 ? `/projects/-/trash?offset=${offset}` : "/projects/-/trash", { signal });
}

export function restoreTrashEntry(entry: string, csrf: string): Promise<ProjectDetail & { warnings: string[] }> {
  return api(`/projects/-/trash/${encodeURIComponent(entry)}/restore`, { method: "POST", csrf });
}

export function purgeTrashEntry(entry: string, confirmId: string, csrf: string): Promise<{ entry: string; purged: boolean }> {
  return api(`/projects/-/trash/${encodeURIComponent(entry)}`, { method: "DELETE", body: { confirm_id: confirmId }, csrf });
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

// ── the folder picker (ADR-2322): directories on the machine that runs the console, with the location rule's verdict ────────

/** What the chosen folder is for; the verdict is the rule of that action. */
export type PickerPurpose = "create" | "adopt" | "projects_root" | "move" | "relocate" | "import";

export interface BrowsePlace {
  kind: "managed" | "root" | "home" | "desktop" | "documents" | "drive" | "recent" | "favourite";
  label: string;
  path: string;
  available: boolean;
  /** "removable", "network", or why the place is not available */
  note?: string;
}

export interface BrowseVerdict {
  ok: boolean;
  reason_class?: string;
  message: string;
  warnings?: string[];
  /** one sentence per warning class (the hint word named) */
  notes?: { class: string; message: string }[];
}

export interface BrowseEntry {
  name: string;
  /** where the row leads (the "Corvin workspaces" row leads to the own workspace) */
  path: string;
  kind: "dir";
  hidden: boolean;
  symlink: boolean;
  readable: boolean;
  project: { state: "mine" | "other" | "none"; id?: string; title?: string };
  refusal?: string;
}

export interface BrowseListing {
  path: string;
  display: string;
  parent: string | null;
  breadcrumbs: { label: string; path: string }[];
  /** false: the folder itself could not be read */
  readable: boolean;
  entries: BrowseEntry[];
  verdict: BrowseVerdict;
  truncated: boolean;
  next_page?: string;
}

export interface BrowseParams {
  path: string;
  purpose: PickerPurpose;
  projectId?: string;
  hidden?: boolean;
  page?: string;
}

export function browsePlaces(signal?: AbortSignal): Promise<{ places: BrowsePlace[]; verdict?: BrowseVerdict }> {
  return api("/projects/-/browse/places", { signal });
}

export function browse(p: BrowseParams, signal?: AbortSignal): Promise<BrowseListing> {
  const q = new URLSearchParams({ path: p.path, purpose: p.purpose });
  if (p.projectId) q.set("project_id", p.projectId);
  if (p.hidden) q.set("hidden", "1");
  if (p.page) q.set("page", p.page);
  return api<BrowseListing>(`/projects/-/browse?${q.toString()}`, { signal });
}

/** Replaces the favourites (at most 10); answers the favourite rows. */
export function setFavourites(paths: string[], csrf: string): Promise<{ places: BrowsePlace[] }> {
  return api("/projects/-/browse/favourites", { method: "PUT", body: { paths }, csrf });
}

/** "Clear recent places"; answers the places. */
export function clearRecent(csrf: string): Promise<{ places: BrowsePlace[] }> {
  return api("/projects/-/browse/recent", { method: "DELETE", csrf });
}

// ── share: export, import, duplicate (project bundles; routes under /projects/-/…) ─────────────────────────────────────────

export type PartName = "files" | "kb" | "git_history" | "videos_index" | "generated";
export interface PartRow { name: PartName; included: boolean; note?: string }
/** A path the bundle leaves out and why (`secret_name`, `generated`, `link`, `machine_local`, …). */
export interface LeftOut { path: string; reason: string }

export interface ExportPreviewProject {
  id: string;
  title: string;
  template: string;
  role: string;
  classification: DataClass;
  docs_mode: "nested" | "plain";
  features: Record<string, boolean>;
  parts: PartRow[];
  left_out: LeftOut[];
  counts: { files: number; bytes: number };
  commits: number | null;
  kb_entries: number;
  /** files in the git history that look like secrets (an export with history would be refused) */
  history_findings: number;
}
export interface ExportPreview { projects: ExportPreviewProject[]; notes: string[]; warnings: string[]; gpg: boolean }
export interface ExportParts { git_history: boolean; generated: boolean; kb: boolean }

function partsQuery(parts: ExportParts): string {
  return (Object.keys(parts) as (keyof ExportParts)[]).filter((k) => parts[k]).join(",");
}

/** What an export would contain; writes nothing. */
export function exportPreview(ids: string[], parts: ExportParts, signal?: AbortSignal): Promise<ExportPreview> {
  const q = new URLSearchParams({ ids: ids.join(","), parts: partsQuery(parts) });
  return api<ExportPreview>(`/projects/-/export/preview?${q.toString()}`, { signal, timeoutMs: 120_000 });
}

export type ExportAnswer =
  | { kind: "file"; blob: Blob; filename: string; warnings: string[] }
  | { kind: "job"; job_id: string; warnings: string[] };

async function failure(res: Response): Promise<ApiError> {
  let payload: unknown = null;
  try { payload = await res.json(); } catch { /* not JSON */ }
  if (isCsrfError(res.status, payload)) notifyCsrfError();
  return new ApiError(res.status, payload);
}

function dispositionName(h: string | null, fallback: string): string {
  const m = h ? /filename="?([^";]+)"?/i.exec(h) : null;
  return m ? m[1] : fallback;
}

/** Up to 100 MiB the server answers the file (200); a larger export runs as a job (202 `{job_id}`). */
export async function exportProjects(
  body: { project_ids: string[]; parts: ExportParts; confirm?: boolean; passphrase?: string }, csrf: string,
): Promise<ExportAnswer> {
  const res = await fetch(`${BASE}/projects/-/export`, {
    method: "POST", credentials: "include",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf, Accept: "application/zip, application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw await failure(res);
  const warn = (res.headers.get("X-Corvin-Export-Warnings") ?? "").trim();
  if (res.status === 202) {
    const j = (await res.json()) as { job_id: string; warnings?: string[] };
    return { kind: "job", job_id: j.job_id, warnings: j.warnings ?? [] };
  }
  const blob = await res.blob();
  return { kind: "file", blob, filename: dispositionName(res.headers.get("Content-Disposition"), "corvin-project.zip"), warnings: warn ? [warn] : [] };
}

export interface ExportJob {
  state: "running" | "done" | "error";
  progress: { files: number; bytes: number };
  download_url?: string;
  warnings?: string[];
  reason_class?: string;
  message?: string;
}

export function exportJob(jobId: string, signal?: AbortSignal): Promise<ExportJob> {
  return api<ExportJob>(`/projects/-/exports/${encodeURIComponent(jobId)}`, { signal });
}

/** The finished file of a job (kept 24 h). */
export function exportJobFileUrl(jobId: string): string {
  return `${BASE}/projects/-/exports/${encodeURIComponent(jobId)}/file`;
}

export interface ImportConflict { kind: string; message: string; fix: { suggested_id?: string; choose_location?: boolean } }

export interface ImportCard {
  id: string;
  title: string;
  role: string;
  classification: DataClass;
  docs_mode: "nested" | "plain";
  template: string;
  features: Record<string, boolean>;
  counts: { files: number; bytes: number };
  parts: PartRow[];
  git: { head: string; docs_head?: string } | null;
  left_out: LeftOut[];
  warnings: string[];
  source_project_uid?: string;
  default_location: { storage: DefaultStorage | "own"; path: string };
  conflicts: ImportConflict[];
}

export interface ImportReport {
  staging_id: string;
  origin: "unverified";
  exporter: { app: string; version: string };
  created_at: string;
  projects: ImportCard[];
}

/** Uploads one bundle into this tenant's staging and validates it; nothing is written into the projects area. */
export async function importValidate(file: File, csrf: string): Promise<ImportReport> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${BASE}/projects/-/import/validate`, {
    method: "POST", body: form, credentials: "include", headers: { "X-CSRF-Token": csrf },
  });
  if (!res.ok) throw await failure(res);
  return (await res.json()) as ImportReport;
}

/** The conflicts recomputed for a chosen id and place. */
export function importVerdict(
  stagingId: string, p: { project: string; projectId?: string; location?: string | null }, signal?: AbortSignal,
): Promise<{ conflicts: ImportConflict[]; location?: { storage: string; path: string } }> {
  const q = new URLSearchParams({ project: p.project });
  if (p.projectId) q.set("project_id", p.projectId);
  if (p.location) q.set("location", p.location);
  return api(`/projects/-/import/${encodeURIComponent(stagingId)}/verdict?${q.toString()}`, { signal });
}

export interface ImportApplied { project: ProjectDetail; warnings: string[]; notes: string[]; origin: "unverified" }

export function importApply(
  stagingId: string,
  body: { project?: string; project_id?: string; location?: string; parts?: { git_history: boolean; kb: boolean }; title?: string },
  csrf: string,
): Promise<ImportApplied> {
  return api<ImportApplied>(`/projects/-/import/${encodeURIComponent(stagingId)}/apply`, { method: "POST", body, csrf, timeoutMs: 300_000 });
}

/** Cancel: the uploaded file is removed from the staging. */
export function importCancel(stagingId: string, csrf: string): Promise<void> {
  return api<void>(`/projects/-/import/${encodeURIComponent(stagingId)}`, { method: "DELETE", csrf });
}

/** A copy under a new id, through the same bundle pipeline (no download). */
export function duplicateProject(
  id: string, body: { project_id: string; location?: string; parts?: { git_history: boolean; kb: boolean; generated: boolean } }, csrf: string,
): Promise<ImportApplied> {
  return api<ImportApplied>(`/projects/${encodeURIComponent(id)}/duplicate`, { method: "POST", body, csrf, timeoutMs: 300_000 });
}
