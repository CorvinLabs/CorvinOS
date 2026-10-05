/**
 * Full-pane drag-and-drop overlay, shared by all three chat surfaces
 * (session, group, peer). The drop target is the whole conversation pane
 * (header + message stream + composer) — not just the composer strip — so
 * dragging a file anywhere over the chat attaches it; this renders the
 * visual affordance for that, on top of everything else while a drag is
 * over the pane.
 */
import { FolderUp, Paperclip } from "lucide-react";

export function DropOverlay({ active, folders }: { active: boolean; folders: boolean }) {
  if (!active) return null;
  return (
    <div
      className="pointer-events-none absolute inset-0 z-30 flex items-center justify-center bg-background/80 backdrop-blur-sm"
      data-testid="chat-drop-overlay"
    >
      <div className="flex flex-col items-center gap-2 rounded-2xl border-2 border-dashed border-accent/60 bg-accent/5 px-8 py-6 text-center">
        {folders ? <FolderUp className="h-8 w-8 text-accent-foreground" /> : <Paperclip className="h-8 w-8 text-accent-foreground" />}
        <p className="text-sm font-medium text-accent-foreground">
          Drop {folders ? "files or folders" : "files"} to attach
        </p>
      </div>
    </div>
  );
}
