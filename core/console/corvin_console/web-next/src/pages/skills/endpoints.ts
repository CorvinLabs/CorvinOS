/**
 * The backend routes the skill-manager pages and the plugin-upload modal call.
 *
 * These pages used to call /v1/skills/{upload,uploads,installed,install,
 * status,available} — none of which any router serves. The real routes:
 *
 *  - routes/skill_manager.py, mounted at /v1/console/skills-manager:
 *      GET    /skills/installed                        {skills, total}
 *      POST   /skills/install     multipart: file, skill_id, version
 *      DELETE /skills/uninstall/{skill_id}/{version}
 *  - routes/plugin_upload.py, mounted at /v1/console:
 *      POST   /plugin-uploads     multipart: file      -> {upload_id, ...}
 *      GET    /plugin-uploads                          {uploads, count}
 *      POST   /plugin-uploads/{id}/approve | /reject
 *
 * Every mutating route requires the session's X-CSRF-Token. Plain window.fetch
 * gets it from lib/csrf-fetch.ts (installed in main.tsx); an XMLHttpRequest
 * must attach getCurrentCsrf() itself.
 */
const enc = encodeURIComponent;

export const SKILLS_MANAGER_BASE = "/v1/console/skills-manager/skills";
export const SKILLS_INSTALLED = `${SKILLS_MANAGER_BASE}/installed`;
export const SKILLS_INSTALL = `${SKILLS_MANAGER_BASE}/install`;
export const skillUninstallPath = (skillId: string, version: string): string =>
  `${SKILLS_MANAGER_BASE}/uninstall/${enc(skillId)}/${enc(version)}`;

export const PLUGIN_UPLOADS = "/v1/console/plugin-uploads";
export const pluginUploadActionPath = (uploadId: string, action: "approve" | "reject"): string =>
  `${PLUGIN_UPLOADS}/${enc(uploadId)}/${action}`;

/** FastAPI error bodies carry `detail` as a string or an object
 *  ({message, validation_errors} from plugin_upload.py). Never "[object Object]". */
export async function errorMessage(res: Response): Promise<string> {
  let body: unknown = null;
  try {
    body = await res.json();
  } catch {
    /* non-JSON error body */
  }
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object") {
    const d = detail as { message?: unknown; validation_errors?: unknown };
    const errs = Array.isArray(d.validation_errors) ? d.validation_errors.map(String) : [];
    const msg = typeof d.message === "string" ? d.message : `HTTP ${res.status}`;
    return errs.length ? `${msg}: ${errs.join("; ")}` : msg;
  }
  return `HTTP ${res.status}`;
}
