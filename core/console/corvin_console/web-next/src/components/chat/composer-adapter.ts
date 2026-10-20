/**
 * Composer context adapter (CONCEPT-0115).
 *
 * One composer, many surfaces. A surface (session chat, workflow design chat,
 * workflow run reply) hands the shared composer UI this small port instead of
 * forking the UI. The adapter carries only what differs per context — where an
 * upload goes, which `/` commands exist, what "send" does. It never carries a
 * decision: tenant, gates, attachment validity and limits are re-checked by the
 * server on every request (the client is never the authority).
 */
import type { SlashCommand } from "@/components/chat/SlashCommandPalette";

/** A stored, not-yet-sent attachment. `id` is stable and carries no path. */
export interface ComposerAttachmentRef {
  id: string;
  name: string;
  size: number;
  mime: string;
}

export interface ComposerLimits {
  maxFiles: number;
  maxBytes: number;
}

/** Mirrors attachments_common.ATTACH_MAX_FILES / ATTACH_MAX_BYTES on the server. */
export const DEFAULT_COMPOSER_LIMITS: ComposerLimits = {
  maxFiles: 10,
  maxBytes: 50 * 1024 * 1024,
};

export interface ComposerContextAdapter<A extends ComposerAttachmentRef = ComposerAttachmentRef> {
  /** Upload target: chat → session workdir, workflow → workflow attachment store. */
  upload: (files: File[]) => Promise<A[]>;
  /** Drop an uploaded-but-unsent attachment server-side. Absent → client-only removal. */
  remove?: (att: A) => Promise<void>;
  /** Commands for this context (merged with, or replacing, the chat table). */
  extraCommands: readonly SlashCommand[];
  sessionCommands: boolean;
  /** Send the draft with its attachments. */
  send: (text: string, attachments: A[]) => void;
  /** Display/hook key only — never an authority. */
  contextKey: string;
  limits: ComposerLimits;
}

/**
 * Client-side pre-check so an oversized file is refused before any bytes move
 * (batching into maxFiles-sized requests is the upload client's job). Returns an error message, or null when the batch is acceptable. The server
 * enforces the same limits independently.
 */
export function checkUploadLimits(
  files: readonly File[],
  limits: ComposerLimits,
): string | null {
  if (files.length === 0) return null;
  const tooBig = files.find((f) => f.size > limits.maxBytes);
  if (tooBig) {
    const mib = Math.floor(limits.maxBytes / (1024 * 1024));
    return `"${tooBig.name}" is larger than ${mib} MiB.`;
  }
  return null;
}
