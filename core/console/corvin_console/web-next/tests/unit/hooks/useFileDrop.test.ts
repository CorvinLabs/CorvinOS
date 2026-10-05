/**
 * Folder-drop recursion in useFileDrop (operator request 2026-10-05: expand
 * the drop zone to the whole chat pane and accept dropped folders, not just
 * individual files).
 *
 * A real OS-level folder drag can't be driven through Playwright — browsers
 * only populate `DataTransferItem.webkitGetAsEntry()` from an actual
 * filesystem drag, which no automation layer can forge (that's a deliberate
 * browser security boundary, not a test-tooling gap). This is the
 * infeasibility exception: the recursion/flattening/truncation logic is
 * instead proven here against the hook's real public API
 * (`dropHandlers.onDrop`), with `FileSystemEntry`-shaped doubles standing in
 * for what the browser would otherwise hand it — the same boundary the real
 * drop event crosses, not a mock of the hook itself.
 */
import { describe, it, expect, vi, beforeAll } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import type React from "react";

// `supportsDirectoryDrop` is computed once at module load from
// `DataTransferItem.prototype` — jsdom ships no such global, so it must
// exist BEFORE the hook module is first imported, or the constant freezes
// at `false` for the rest of this file.
class FakeDataTransferItem {
  webkitGetAsEntry(): unknown { return null; }
}
(globalThis as unknown as { DataTransferItem: unknown }).DataTransferItem = FakeDataTransferItem;

let useFileDrop: typeof import("@/hooks/use-file-drop").useFileDrop;
let MAX_DROPPED_FILES: number;

beforeAll(async () => {
  const mod = await import("@/hooks/use-file-drop");
  useFileDrop = mod.useFileDrop;
  MAX_DROPPED_FILES = mod.MAX_DROPPED_FILES;
});

interface FakeEntry {
  isFile: boolean;
  isDirectory: boolean;
  fullPath: string;
  file?(success: (f: File) => void, error: (e: unknown) => void): void;
  createReader?(): {
    readEntries(success: (e: FakeEntry[]) => void, error: (e: unknown) => void): void;
  };
}

function fileEntry(fullPath: string): FakeEntry {
  return {
    isFile: true,
    isDirectory: false,
    fullPath,
    file(success) {
      success(new File(["x"], fullPath.split("/").pop()!, { type: "text/plain" }));
    },
  };
}

function dirEntry(fullPath: string, children: FakeEntry[]): FakeEntry {
  let delivered = false;
  return {
    isFile: false,
    isDirectory: true,
    fullPath,
    createReader() {
      return {
        readEntries(success) {
          // Real browsers return entries in one or more batches and signal
          // "done" with an empty array — never all at once synchronously.
          if (!delivered) { delivered = true; success(children); } else success([]);
        },
      };
    },
  };
}

function itemFor(entry: FakeEntry | null): DataTransferItem {
  return { webkitGetAsEntry: () => entry } as unknown as DataTransferItem;
}

function dropEvent(items: DataTransferItem[], files: File[] = []): React.DragEvent {
  return {
    preventDefault: () => {},
    dataTransfer: { items, files } as unknown as DataTransfer,
  } as unknown as React.DragEvent;
}

describe("useFileDrop — folder recursion", () => {
  it("flattens a dropped folder's nested files into the callback, path-joined with __", async () => {
    const onDropFiles = vi.fn();
    const { result } = renderHook(() => useFileDrop(onDropFiles));

    const root = dirEntry("/project", [
      fileEntry("/project/readme.txt"),
      dirEntry("/project/src", [fileEntry("/project/src/app.ts")]),
    ]);
    act(() => { result.current.dropHandlers.onDrop(dropEvent([itemFor(root)])); });

    await waitFor(() => expect(onDropFiles).toHaveBeenCalledTimes(1));
    const names = (onDropFiles.mock.calls[0][0] as File[]).map((f) => f.name).sort();
    expect(names).toEqual(["project__readme.txt", "project__src__app.ts"]);
  });

  it("never lets a folder entry's fullPath produce real path separators or traversal", async () => {
    const onDropFiles = vi.fn();
    const { result } = renderHook(() => useFileDrop(onDropFiles));

    // A crafted/unusual fullPath (leading slashes, a literal "..") must
    // never survive into the uploaded name — that name eventually reaches
    // attachments_common.py's safe_attach_name, but this hook is the first
    // line of defense against it ever looking like a path at all. Nested in
    // a folder so the walk (not the flat dataTransfer.files fast path,
    // which only ever sees browser-assigned plain names) actually handles it.
    const evil = dirEntry("/weird", [fileEntry("/weird/../../etc/passwd")]);
    act(() => { result.current.dropHandlers.onDrop(dropEvent([itemFor(evil)])); });

    await waitFor(() => expect(onDropFiles).toHaveBeenCalledTimes(1));
    const [file] = onDropFiles.mock.calls[0][0] as File[];
    expect(file.name).not.toMatch(/[/\\]/);
    expect(file.name).not.toContain("..");
  });

  it("truncates at MAX_DROPPED_FILES and reports it via onTruncated", async () => {
    const onDropFiles = vi.fn();
    const onTruncated = vi.fn();
    const { result } = renderHook(() => useFileDrop(onDropFiles, { onTruncated }));

    const many = Array.from({ length: MAX_DROPPED_FILES + 25 }, (_, i) => fileEntry(`/big/file${i}.txt`));
    const root = dirEntry("/big", many);
    act(() => { result.current.dropHandlers.onDrop(dropEvent([itemFor(root)])); });

    await waitFor(() => expect(onDropFiles).toHaveBeenCalledTimes(1));
    expect((onDropFiles.mock.calls[0][0] as File[]).length).toBe(MAX_DROPPED_FILES);
    expect(onTruncated).toHaveBeenCalledWith(MAX_DROPPED_FILES);
  });

  it("falls back to the flat dataTransfer.files list when entries resolve to nothing (no folders involved)", () => {
    const onDropFiles = vi.fn();
    const { result } = renderHook(() => useFileDrop(onDropFiles));

    const plain = new File(["x"], "plain.txt");
    // webkitGetAsEntry() returning null (e.g. a browser without the API, or
    // a plain-file drag that never produced directory entries) must still
    // deliver the file through dataTransfer.files, not drop it silently.
    act(() => { result.current.dropHandlers.onDrop(dropEvent([itemFor(null)], [plain])); });

    expect(onDropFiles).toHaveBeenCalledWith([plain]);
  });

  it("ignores the drop entirely while disabled", () => {
    const onDropFiles = vi.fn();
    const { result } = renderHook(() => useFileDrop(onDropFiles, { disabled: true }));

    act(() => { result.current.dropHandlers.onDrop(dropEvent([], [new File(["x"], "a.txt")])); });

    expect(onDropFiles).not.toHaveBeenCalled();
  });
});
