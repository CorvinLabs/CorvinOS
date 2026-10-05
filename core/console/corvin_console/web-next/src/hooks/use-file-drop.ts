/**
 * Drag-and-drop file intake for a chat composer — missing from all three
 * chat surfaces before this (grep confirmed: no onDrop/onDragOver anywhere
 * in pages/chat.tsx, which otherwise has every other attachment affordance
 * built). Spread `dropHandlers` onto the whole chat pane (header + message
 * stream + composer, not just the composer strip) so a drop anywhere in the
 * conversation attaches files; `isDragging` drives a full-pane overlay.
 *
 * A counter (not a boolean) survives dragging over child elements, which
 * fire their own dragenter/dragleave as the pointer crosses them — a naive
 * boolean flips off on every child dragleave and the highlight flickers.
 *
 * Folder support: Chromium/Firefox expose `DataTransferItem.webkitGetAsEntry()`
 * (not in any W3C spec, but universally shipped since ~2014 under that
 * prefix — Safari added it as `webkitGetAsEntry` too, no vendor ships the
 * unprefixed `getAsEntry`). Where it's missing, a dropped folder degrades to
 * zero files silently at the browser level (`dataTransfer.files` never
 * contains directory entries) — `supportsDirectoryDrop` lets callers warn
 * the operator instead of just doing nothing.
 */
import * as React from "react";

/** Bound on a single drop's total file count — a drop (vs. an explicit
 * picker selection) can be an entire project directory with no size hint
 * before the walk completes; stop early and tell the operator rather than
 * hang the tab or flood the 50 MB/upload server limit with thousands of
 * small requests. */
export const MAX_DROPPED_FILES = 200;

export interface UseFileDropResult {
  isDragging: boolean;
  dropHandlers: {
    onDragEnter: (e: React.DragEvent) => void;
    onDragOver: (e: React.DragEvent) => void;
    onDragLeave: (e: React.DragEvent) => void;
    onDrop: (e: React.DragEvent) => void;
  };
}

interface FileSystemEntryLike {
  isFile: boolean;
  isDirectory: boolean;
  fullPath: string;
  file(success: (f: File) => void, error: (e: unknown) => void): void;
  createReader?(): {
    readEntries(
      success: (entries: FileSystemEntryLike[]) => void,
      error: (e: unknown) => void,
    ): void;
  };
}

/** A dropped folder's files arrive with their path info only in
 * `FileSystemEntry.fullPath` (e.g. "/project/src/app.ts") — `File.name` is
 * just "app.ts", and `File.webkitRelativePath` (read-only, picker-only) is
 * empty for entries resolved this way. Fold the relative path into the
 * uploaded name instead of silently dropping it, but never let it become an
 * actual path: no "/", no "\", no "..", so the server's own filename
 * sanitizer sees one opaque segment, exactly like a flat upload. */
function flattenEntryPath(fullPath: string): string {
  const stripped = fullPath.replace(/^\/+/, "");
  const segments = stripped.split("/").filter((s) => s && s !== ".." && s !== ".");
  return segments.join("__") || stripped;
}

function readEntryFile(entry: FileSystemEntryLike): Promise<File> {
  return new Promise((resolve, reject) => {
    entry.file((f) => {
      const flatName = flattenEntryPath(entry.fullPath);
      // Re-wrap so the visible/uploaded name carries the folder context
      // (File.name is otherwise just the leaf "app.ts" with no indication
      // it came from a subfolder at all).
      resolve(flatName !== f.name ? new File([f], flatName, { type: f.type }) : f);
    }, reject);
  });
}

function readAllDirEntries(entry: FileSystemEntryLike): Promise<FileSystemEntryLike[]> {
  return new Promise((resolve, reject) => {
    const reader = entry.createReader?.();
    if (!reader) { resolve([]); return; }
    const all: FileSystemEntryLike[] = [];
    // readEntries() is explicitly NOT guaranteed to return everything in one
    // call (historically capped around 100 per Chromium batch) — it must be
    // called repeatedly until it resolves an empty array.
    const readBatch = () => {
      reader.readEntries((batch) => {
        if (batch.length === 0) { resolve(all); return; }
        all.push(...batch);
        readBatch();
      }, reject);
    };
    readBatch();
  });
}

/** Breadth-first walk so a cap (MAX_DROPPED_FILES) hit mid-walk still
 * reflects a reasonably even sample across sibling folders rather than
 * exhausting the budget on the first-listed subtree. */
async function walkEntries(
  roots: FileSystemEntryLike[],
  limit: number,
): Promise<{ files: File[]; truncated: boolean }> {
  const files: File[] = [];
  let queue = roots;
  let truncated = false;
  while (queue.length > 0 && files.length < limit) {
    const next: FileSystemEntryLike[] = [];
    for (const entry of queue) {
      if (files.length >= limit) { truncated = true; break; }
      if (entry.isFile) {
        try {
          files.push(await readEntryFile(entry));
        } catch {
          // unreadable entry (permission, race with external deletion) — skip it
        }
      } else if (entry.isDirectory) {
        try {
          next.push(...(await readAllDirEntries(entry)));
        } catch {
          // unreadable directory — skip its subtree
        }
      }
    }
    if (queue.length > 0 && next.length === 0 && files.length >= limit) truncated = true;
    queue = next;
  }
  if (queue.length > 0) truncated = true;
  return { files, truncated };
}

export const supportsDirectoryDrop =
  typeof DataTransferItem !== "undefined" &&
  "webkitGetAsEntry" in DataTransferItem.prototype;

export function useFileDrop(
  onDropFiles: (files: File[]) => void,
  opts?: { disabled?: boolean; onTruncated?: (limit: number) => void },
): UseFileDropResult {
  const disabled = opts?.disabled ?? false;
  const onTruncated = opts?.onTruncated;
  const [isDragging, setIsDragging] = React.useState(false);
  const counterRef = React.useRef(0);

  const onDragEnter = React.useCallback((e: React.DragEvent) => {
    if (disabled) return;
    e.preventDefault();
    counterRef.current += 1;
    setIsDragging(true);
  }, [disabled]);

  const onDragOver = React.useCallback((e: React.DragEvent) => {
    if (disabled) return;
    // Required: a browser only fires `drop` if `dragover` was prevented.
    e.preventDefault();
  }, [disabled]);

  const onDragLeave = React.useCallback((e: React.DragEvent) => {
    if (disabled) return;
    e.preventDefault();
    counterRef.current = Math.max(0, counterRef.current - 1);
    if (counterRef.current === 0) setIsDragging(false);
  }, [disabled]);

  const onDrop = React.useCallback((e: React.DragEvent) => {
    e.preventDefault();
    counterRef.current = 0;
    setIsDragging(false);
    if (disabled) return;

    const items = e.dataTransfer?.items;
    const entries: FileSystemEntryLike[] =
      items && supportsDirectoryDrop
        ? Array.from(items)
            // Items must be converted to entries SYNCHRONOUSLY, inside the
            // native drop event — the browser invalidates DataTransfer the
            // moment this handler returns, so an entry captured after an
            // `await` is already gone.
            .map((it) =>
              // Cast through `unknown`: lib.dom's own `webkitGetAsEntry()`
              // typing returns `FileSystemEntry`, whose `file()` callback is
              // typed to receive a `FileSystemEntry` — but at runtime it
              // receives a `File`, which is what every browser actually
              // passes. FileSystemEntryLike reflects the runtime shape.
              (it as unknown as { webkitGetAsEntry?(): FileSystemEntryLike | null })
                .webkitGetAsEntry?.(),
            )
            .filter((e): e is FileSystemEntryLike => e != null)
        : [];

    if (entries.length > 0 && entries.some((en) => en.isDirectory)) {
      void walkEntries(entries, MAX_DROPPED_FILES).then(({ files, truncated }) => {
        if (truncated) onTruncated?.(MAX_DROPPED_FILES);
        if (files.length > 0) onDropFiles(files);
      });
      return;
    }

    // No folders involved (or the browser can't resolve entries at all) —
    // the plain flat-file path, unchanged from before folder support.
    const files = Array.from(e.dataTransfer?.files ?? []).slice(0, MAX_DROPPED_FILES);
    if (e.dataTransfer && e.dataTransfer.files.length > MAX_DROPPED_FILES) {
      onTruncated?.(MAX_DROPPED_FILES);
    }
    if (files.length > 0) onDropFiles(files);
  }, [disabled, onDropFiles, onTruncated]);

  return { isDragging, dropHandlers: { onDragEnter, onDragOver, onDragLeave, onDrop } };
}
