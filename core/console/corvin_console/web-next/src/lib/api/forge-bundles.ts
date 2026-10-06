/**
 * Forge Bundle API — backend: core/console/corvin_console/routes/forge_bundle_routes.py,
 * mounted under the console's "/v1/console" with relative "/forge-bundles/..." paths.
 * JSON calls go through api(); uploads and the ZIP download use fetch with the
 * same BASE and an explicit CSRF header.
 */
import { api, ApiError, BASE } from "./client";

export type BundleKind = "skill" | "tool" | "layer" | "plugin";

export interface Exportable {
  skills: { id: string; version: string; description: string }[];
  tools: { id: string; description: string; runtime: string }[];
  layers: { id: string; version: string; status: string | null }[];
  plugins: never[];
}

export interface Selection {
  kind: BundleKind;
  id: string;
  version: string;
}

export interface ValidationOk {
  valid: true;
  bundle_id: string;
  bundle_version: string;
  description: string | null;
  created_at: string | null;
  artifacts: { kind: BundleKind; id: string; version: string; file_count: number;
               requires: { kind: BundleKind; id: string; version: string | null }[] }[];
  total_uncompressed_bytes: number;
  unscanned_files: string[];
  origin_verified: false;
}

export interface ValidationRejected {
  valid: false;
  stage: string;
  reason: string;
  origin_verified: false;
}

export type ValidationResult = ValidationOk | ValidationRejected;

export interface ArtifactOutcome {
  kind: BundleKind;
  id: string;
  version: string;
  status: "installed" | "forged" | "pending_approval" | "quarantined" | "failed";
  detail: string;
}

export interface ImportResult {
  bundle_id: string;
  bundle_version: string;
  artifact_count: number;
  failed_count: number;
  outcomes: ArtifactOutcome[];
  unscanned_files: string[];
  origin_verified: false;
}

export interface QuarantineItem {
  quarantine_id: string;
  kind: "tool";
  tool_id: string;
  version: string;
  bundle_id: string;
  bundle_version: string;
  staged_at: string;
  runtime: string;
  description: string;
  impl_sha256: string;
  origin_verified: false;
}

const P = "/forge-bundles";

async function failure(res: Response): Promise<ApiError> {
  let payload: unknown = null;
  try {
    payload = await res.json();
  } catch {
    /* non-JSON body */
  }
  return new ApiError(res.status, payload);
}

async function upload<T>(path: string, file: File, csrf: string): Promise<T> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${BASE}${P}/${path}`, {
    method: "POST", body: form, credentials: "include",
    headers: { "X-CSRF-Token": csrf },
  });
  if (!res.ok) throw await failure(res);
  return (await res.json()) as T;
}

export const fetchExportable = () => api<Exportable>(`${P}/exportable`);

export async function exportBundle(
  body: { bundle_id: string; bundle_version: string; description?: string; selections: Selection[] },
  csrf: string,
): Promise<Blob> {
  const res = await fetch(`${BASE}${P}/export`, {
    method: "POST", credentials: "include",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw await failure(res);
  return res.blob();
}

export const validateBundle = (file: File, csrf: string) => upload<ValidationResult>("validate", file, csrf);
export const importBundle = (file: File, csrf: string) => upload<ImportResult>("import", file, csrf);

export const fetchQuarantine = () => api<{ items: QuarantineItem[]; count: number }>(`${P}/quarantine`);

export const decideQuarantine = (qid: string, action: "accept" | "reject", csrf: string) =>
  api<{ status: string; tool_id: string }>(`${P}/quarantine/${encodeURIComponent(qid)}/${action}`, {
    method: "POST", csrf,
  });
