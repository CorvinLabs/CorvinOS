/**
 * Pending-attachment chip — extracted from pages/chat.tsx (ChatPane) so
 * session chat, group chat (GroupConversation.tsx) and peer/A2A chat
 * (PeerConversation.tsx) can share one rendering for a staged file.
 *
 * Generic over `AttachmentLike` (name/size/mime) rather than the concrete
 * `AttachmentMeta` type: session- and group-chat attachments carry a
 * server `path`, peer/A2A attachments carry a client-side `content_b64`
 * instead (see hooks/use-attachment-upload.ts) — this component only ever
 * needs the three display fields all of them share.
 */
import * as React from "react";
import { X } from "lucide-react";

export interface AttachmentLike {
  name: string;
  size: number;
  mime: string;
}

export function AttachmentChip({
  attachment,
  onRemove,
}: {
  attachment: AttachmentLike;
  onRemove: () => void;
}) {
  const isImage = attachment.mime.startsWith("image/");
  const isPdf = attachment.mime === "application/pdf" || attachment.name.endsWith(".pdf");
  const isCsv = attachment.mime === "text/csv" || attachment.name.endsWith(".csv");
  const kb = (attachment.size / 1024).toFixed(1);

  return (
    <div
      className="group flex items-center gap-1.5 rounded-lg border border-border/60 bg-card/60 px-2 py-1.5 text-xs"
      data-testid="attachment-chip"
      title={`${attachment.name} · ${kb} KB`}
    >
      <span className="text-accent shrink-0">
        {isImage ? "🖼" : isPdf ? "📄" : isCsv ? "📊" : "📎"}
      </span>
      <span className="max-w-[120px] truncate font-mono text-[11px] text-foreground">
        {attachment.name}
      </span>
      <span className="shrink-0 text-muted-foreground">{kb} KB</span>
      <button
        onClick={onRemove}
        className="ml-0.5 shrink-0 rounded p-0.5 text-muted-foreground opacity-60 transition-opacity hover:text-destructive hover:opacity-100"
        aria-label={`Remove ${attachment.name}`}
        data-testid="remove-attachment"
      >
        <X className="h-3 w-3" />
      </button>
    </div>
  );
}
