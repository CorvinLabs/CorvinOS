/**
 * forge-bundles.ts — Testable encoding, URL building, and file handling for
 * forge-bundle import/export operations. No component logic lives here; this
 * module is purely encoding + request helper.
 *
 * Export contract:
 *   GET /v1/console/forge-bundles/export/{type}/{id}
 *   (batch = several single-item downloads; the bundle POST needs a bundle id/version + CSRF)
 *
 * Import contract:
 *   POST /v1/console/forge-bundles/import (multipart/form-data)
 */

import { getCurrentCsrf } from '@/lib/csrf-fetch';

export type ForgeType = 'tools' | 'skills' | 'os_skills' | 'plugins' | 'layers';

/** The backend names artifact kinds in the singular. */
const KIND: Record<ForgeType, string> = {
  tools: 'tool',
  skills: 'skill',
  os_skills: 'os_skill',
  plugins: 'plugin',
  layers: 'layer',
};

interface ImportResponse {
  success: boolean;
  message: string;
  imported_count?: number;
  failed_count?: number;
  errors?: string[];
}

/**
 * Parse filename from Content-Disposition header.
 * Handles quoted and unquoted filenames per RFC 6266.
 *
 * @param contentDisposition e.g. 'attachment; filename="tools-export.zip"'
 * @returns filename or fallback 'export.zip'
 */
export function extractFilenameFromHeader(contentDisposition: string): string {
  if (!contentDisposition) return 'export.zip';

  // Try quoted filename* first (RFC 5987)
  const filenameStarMatch = contentDisposition.match(/filename\*=(?:UTF-8'')?([^;]+)/i);
  if (filenameStarMatch) {
    return decodeURIComponent(filenameStarMatch[1].replace(/^"(.*)"$/, '$1'));
  }

  // Try regular filename (RFC 6266)
  const filenameMatch = contentDisposition.match(/filename=([^;]+)/i);
  if (filenameMatch) {
    const filename = filenameMatch[1].trim().replace(/^"(.*)"$/, '$1');
    return filename;
  }

  return 'export.zip';
}

/**
 * Build the export URL for a single item.
 *
 * @param type Forge type (tools, skills, os_skills, plugins, layers)
 * @param id Item ID
 * @param version Optional version (required for skills, layers, os_skills; unused for tools/plugins)
 * @returns Full URL path (e.g. /v1/console/forge-bundles/export/tools/my-tool?version=...)
 */
export function buildExportUrl(type: ForgeType, id: string, version?: string): string {
  const url = `/v1/console/forge-bundles/export/${KIND[type]}/${encodeURIComponent(id)}`;
  if (version) {
    return `${url}?version=${encodeURIComponent(version)}`;
  }
  return url;
}

/**
 * Download a blob as a file.
 *
 * @param blob File content
 * @param filename Target filename
 */
export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

/**
 * Perform a single-item export.
 *
 * @param type Forge type
 * @param id Item ID
 * @param version Optional version (required for skills, layers, os_skills)
 * @returns { success, filename } or { success: false, error: string }
 */
export async function exportSingleItem(
  type: ForgeType,
  id: string,
  version?: string,
): Promise<{ success: boolean; filename?: string; error?: string }> {
  try {
    const url = buildExportUrl(type, id, version);
    const response = await fetch(url);

    if (!response.ok) {
      if (response.status === 404) {
        return { success: false, error: 'not available on this build' };
      }
      let detail = '';
      try {
        const body = await response.json();
        if (typeof body?.detail === 'string') detail = ` — ${body.detail}`;
      } catch {
        /* non-JSON body */
      }
      return { success: false, error: `HTTP ${response.status}${detail}` };
    }

    const blob = await response.blob();
    const contentDisposition = response.headers.get('Content-Disposition') || '';
    const filename = extractFilenameFromHeader(contentDisposition);

    downloadBlob(blob, filename);
    return { success: true, filename };
  } catch (err) {
    return {
      success: false,
      error: err instanceof Error ? err.message : 'Unknown error',
    };
  }
}

/**
 * Perform a batch export (multiple items).
 *
 * @param type Forge type
 * @param ids Item IDs
 * @returns { success, filename } or { success: false, error: string }
 */
export async function exportBatchItems(
  type: ForgeType,
  ids: string[],
): Promise<{ success: boolean; filename?: string; error?: string }> {
  if (ids.length === 0) {
    return { success: false, error: 'No items selected' };
  }
  const failed: string[] = [];
  for (const id of ids) {
    const r = await exportSingleItem(type, id);
    if (!r.success) failed.push(`${id}: ${r.error}`);
  }
  return failed.length ? { success: false, error: failed.join('; ') } : { success: true };
}

/**
 * Perform a bundle import.
 *
 * @param file ZIP file to import
 * @returns { success, imported_count, failed_count, errors } or error
 */
export async function importBundle(file: File): Promise<ImportResponse> {
  try {
    const formData = new FormData();
    formData.append('file', file);

    const response = await fetch('/v1/console/forge-bundles/import', {
      method: 'POST',
      body: formData,
      credentials: 'include',
      headers: { 'X-CSRF-Token': getCurrentCsrf() ?? '' },
    });

    if (!response.ok) {
      if (response.status === 404) {
        return {
          success: false,
          message: 'not available on this build',
        };
      }

      let detail = '';
      try {
        const body = await response.json();
        if (typeof body?.detail === 'string') detail = `: ${body.detail}`;
      } catch {
        /* non-JSON body */
      }
      return {
        success: false,
        message: `Import failed: HTTP ${response.status}${detail}`,
      };
    }

    const r = await response.json();
    const failed = r.failed_count ?? 0;
    return {
      success: failed === 0,
      message: failed === 0 ? 'Imported' : `${failed} item(s) were not imported`,
      imported_count: (r.artifact_count ?? 0) - failed,
      failed_count: failed,
      errors: (r.outcomes ?? [])
        .filter((o: { status?: string }) => o.status === 'failed' || o.status === 'refused')
        .map((o: { id: string; detail?: string }) => `${o.id}: ${o.detail ?? ''}`),
    };
  } catch (err) {
    return {
      success: false,
      message: err instanceof Error ? err.message : 'Unknown error',
    };
  }
}

/**
 * Validate filename has .zip extension (client-side check only).
 */
export function isValidZipFile(filename: string): boolean {
  return filename.toLowerCase().endsWith('.zip');
}
