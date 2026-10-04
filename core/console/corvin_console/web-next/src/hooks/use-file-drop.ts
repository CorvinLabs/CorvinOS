/**
 * Drag-and-drop file intake for a chat composer — missing from all three
 * chat surfaces before this (grep confirmed: no onDrop/onDragOver anywhere
 * in pages/chat.tsx, which otherwise has every other attachment affordance
 * built). Spread `dropHandlers` onto the composer's outer container;
 * `isDragging` drives a border/ring highlight while a drag is over it.
 *
 * A counter (not a boolean) survives dragging over child elements, which
 * fire their own dragenter/dragleave as the pointer crosses them — a naive
 * boolean flips off on every child dragleave and the highlight flickers.
 */
import * as React from "react";

export interface UseFileDropResult {
  isDragging: boolean;
  dropHandlers: {
    onDragEnter: (e: React.DragEvent) => void;
    onDragOver: (e: React.DragEvent) => void;
    onDragLeave: (e: React.DragEvent) => void;
    onDrop: (e: React.DragEvent) => void;
  };
}

export function useFileDrop(
  onDropFiles: (files: File[]) => void,
  opts?: { disabled?: boolean },
): UseFileDropResult {
  const disabled = opts?.disabled ?? false;
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
    const files = Array.from(e.dataTransfer?.files ?? []);
    if (files.length > 0) onDropFiles(files);
  }, [disabled, onDropFiles]);

  return { isDragging, dropHandlers: { onDragEnter, onDragOver, onDragLeave, onDrop } };
}
