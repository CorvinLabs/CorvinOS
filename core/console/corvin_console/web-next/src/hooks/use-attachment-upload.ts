/**
 * Attachment picker + pending-attachment state — extracted from
 * pages/chat.tsx (ChatPane).
 *
 * Generic over the result type `T` so it serves both server-stored
 * attachments (api/chat.ts::AttachmentMeta for session chat,
 * api/chat-groups.ts::uploadGroupAttachments for group chat — a `path`
 * under the conversation's own `attachments/` directory) and peer/A2A
 * chat's client-side base64-encoded attachments
 * (api/a2a.ts::encodeFilesForA2A — a `content_b64` field, no server path
 * at all; see that module's comment for why peer chat has no upload
 * route). `uploadFn` is the only thing that differs between the three call
 * sites; this hook owns the shared picker/preview/error/busy UX.
 */
import * as React from "react";
import type { AttachmentLike } from "@/components/chat/AttachmentChip";

export interface UseAttachmentUploadOptions<T extends AttachmentLike> {
  uploadFn: (files: File[]) => Promise<T[]>;
  /** e.g. streaming/busy — blocks new uploads while true. */
  disabled?: boolean;
  formatError?: (err: unknown) => string;
}

export interface UseAttachmentUploadResult<T extends AttachmentLike> {
  pendingAttachments: T[];
  uploading: boolean;
  uploadError: string | null;
  /** Upload (or, for peer chat, encode) one or more files directly — used
   * by both the file-picker's onChange and drag-and-drop's onDrop. */
  addFiles: (files: File[]) => Promise<void>;
  removeAttachment: (index: number) => void;
  clearAttachments: () => void;
  fileInputRef: React.RefObject<HTMLInputElement | null>;
  onFileInputChange: (evt: React.ChangeEvent<HTMLInputElement>) => void;
}

export function useAttachmentUpload<T extends AttachmentLike>(
  opts: UseAttachmentUploadOptions<T>,
): UseAttachmentUploadResult<T> {
  const { uploadFn, disabled = false, formatError } = opts;
  const [pendingAttachments, setPendingAttachments] = React.useState<T[]>([]);
  const [uploading, setUploading] = React.useState(false);
  const [uploadError, setUploadError] = React.useState<string | null>(null);
  const fileInputRef = React.useRef<HTMLInputElement>(null);

  const addFiles = React.useCallback(async (files: File[]) => {
    if (files.length === 0 || disabled) return;
    setUploading(true);
    setUploadError(null);
    try {
      const metas = await uploadFn(files);
      setPendingAttachments((prev) => [...prev, ...metas]);
    } catch (e) {
      setUploadError(
        formatError ? formatError(e) : e instanceof Error ? e.message : "Upload failed",
      );
    } finally {
      setUploading(false);
    }
  }, [uploadFn, disabled, formatError]);

  const onFileInputChange = React.useCallback((evt: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(evt.target.files ?? []);
    evt.target.value = "";
    void addFiles(files);
  }, [addFiles]);

  const removeAttachment = React.useCallback((index: number) => {
    setPendingAttachments((prev) => prev.filter((_, i) => i !== index));
  }, []);

  const clearAttachments = React.useCallback(() => {
    setPendingAttachments([]);
    setUploadError(null);
  }, []);

  return {
    pendingAttachments, uploading, uploadError,
    addFiles, removeAttachment, clearAttachments,
    fileInputRef, onFileInputChange,
  };
}
