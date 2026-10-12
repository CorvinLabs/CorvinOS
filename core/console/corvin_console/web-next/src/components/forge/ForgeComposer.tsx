/**
 * One input for every Forge generator (skill, tool, plugin, layer intent).
 *
 * Same behaviour as the chat composer, built from the same hooks instead of a fifth hand-wired copy:
 *  - Enter sends, Shift+Enter inserts a newline, an IME composition in progress is never sent;
 *  - hold Space (400 ms or more) to dictate, a tap still types a space (`useVoiceInput`);
 *  - the textarea grows with its content (`useAutosizeTextarea`).
 *
 * Enter goes through the SAME `onSubmit` the button calls, so every guard the caller has
 * (minimum length, quota, a run in flight) applies to the key exactly as it does to the click.
 * A transcript only fills the text: nothing is sent after dictation, the user presses Enter.
 */
import * as React from "react";
import { Loader2, Mic, MicOff, Paperclip, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useVoiceInput } from "@/hooks/use-voice-input";
import { useAutosizeTextarea } from "@/hooks/use-autosize-textarea";
import { cn } from "@/lib/utils";
import { DropOverlay } from "@/components/chat/DropOverlay";

/** The server's bounds for a generate request (forge_creator.GenerateRequest). */
export const FORGE_REQUEST_MIN = 10;
export const FORGE_REQUEST_MAX = 4000;

/** What Intake (ADR-2254) says about one attachment. `unanalysed` is shown honestly until the service exists. */
export type AttachmentVerdict = "unanalysed" | "pending" | "admitted" | "admitted_redacted" | "refused";

export interface ComposerAttachment {
  id: string;
  name: string;
  size: number;
  verdict: AttachmentVerdict;
  /** Why it was refused, or what was redacted. */
  note?: string;
}

/**
 * Attachment controller, owned by the caller. When it is NOT passed, the composer shows no attach
 * button at all: a button whose files never reach the engine would mislead (CONCEPT-0104/0106).
 */
export interface ComposerAttachments {
  items: ComposerAttachment[];
  addFiles: (files: File[]) => void | Promise<void>;
  remove: (id: string) => void;
  uploading?: boolean;
  error?: string | null;
  /** Only the first N dropped files are taken; the rest are reported, never silently lost. */
  maxItems?: number;
  /** The tenant's own forged skills and tools, attachable by name. */
  references?: { options: { kind: "skill" | "tool"; name: string }[]; add: (kind: "skill" | "tool", name: string) => void | Promise<void> };
}

export const VERDICT_LABEL: Record<AttachmentVerdict, string> = {
  unanalysed: "not analysed",
  pending: "analysing…",
  admitted: "admitted",
  admitted_redacted: "admitted, redacted",
  refused: "refused",
};

export interface ForgeComposerProps {
  value: string;
  onChange: (v: string) => void;
  /** Called by the button and by Enter. The caller owns validation. */
  onSubmit: () => void;
  /** True while a run is in flight or a request is being sent: input and voice are off. */
  busy: boolean;
  /** True when the button must be disabled (the caller's own conditions, e.g. blank text or no quota). */
  submitDisabled: boolean;
  csrf: string;
  onError: (message: string) => void;
  /** Test id of the textarea and of the button (the existing suites key on these). */
  textareaTestId: string;
  submitTestId: string;
  textareaId: string;
  label: string;
  placeholder: string;
  submitLabel: string;
  busyLabel: string;
  rows?: number;
  /** Extra controls between the text and the counter, e.g. the plugin's panel field. */
  extras?: React.ReactNode;
  attachments?: ComposerAttachments;
}

export default function ForgeComposer({
  value, onChange, onSubmit, busy, submitDisabled, csrf, onError,
  textareaTestId, submitTestId, textareaId, label, placeholder, submitLabel, busyLabel, rows = 3, extras, attachments,
}: ForgeComposerProps) {
  const ref = React.useRef<HTMLTextAreaElement | null>(null);
  useAutosizeTextarea(ref, value, 320);
  const { recording, startRecording, stopRecording } = useVoiceInput({ value, onChange, csrf, disabled: busy, onError });
  const length = value.trim().length;
  const over = value.length > FORGE_REQUEST_MAX;
  const fileRef = React.useRef<HTMLInputElement | null>(null);
  const [dragging, setDragging] = React.useState(false);
  const [dropNote, setDropNote] = React.useState<string | null>(null);
  // A refused attachment blocks Send: the run must never start without something the user attached.
  const refused = attachments?.items.filter((a) => a.verdict === "refused") ?? [];
  const blocked = refused.length > 0 || !!attachments?.uploading;

  const take = (files: File[]) => {
    if (!attachments || files.length === 0) return;
    const room = attachments.maxItems === undefined ? files.length : Math.max(0, attachments.maxItems - attachments.items.length);
    setDropNote(files.length > room ? `Only ${room} more file${room === 1 ? "" : "s"} can be attached; ${files.length - room} left out.` : null);
    if (room > 0) void attachments.addFiles(files.slice(0, room));
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key !== "Enter" || e.shiftKey || e.altKey || e.ctrlKey || e.metaKey) return;
    // An IME confirms a candidate with Enter; that must not send (keyCode 229 covers older engines).
    if (e.nativeEvent.isComposing || e.keyCode === 229) return;
    e.preventDefault();
    if (!busy && !submitDisabled && !recording && !over && !blocked) onSubmit();
  };

  return (
    <div
      className="relative space-y-2"
      data-testid={`${textareaTestId}-composer`}
      onDragOver={attachments ? (e) => { e.preventDefault(); setDragging(true); } : undefined}
      onDragLeave={attachments ? (e) => { if (e.currentTarget === e.target) setDragging(false); } : undefined}
      onDrop={attachments ? (e) => { e.preventDefault(); setDragging(false); take(Array.from(e.dataTransfer.files)); } : undefined}
    >
      {attachments && <DropOverlay active={dragging && !busy} folders={false} />}
      <Label htmlFor={textareaId} className="text-sm font-medium">{label}</Label>
      <div className="flex items-start gap-2">
        <Textarea
          ref={ref}
          id={textareaId}
          data-testid={textareaTestId}
          placeholder={recording ? "Listening — release Space to stop" : placeholder}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={onKeyDown}
          disabled={busy || recording}
          rows={rows}
          className="font-mono text-xs"
        />
        {attachments && (
          <>
            <input
              ref={fileRef}
              type="file"
              multiple
              className="hidden"
              data-testid={`${textareaTestId}-attach-input`}
              onChange={(e) => { take(Array.from(e.target.files ?? [])); e.target.value = ""; }}
            />
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="h-8 w-8 shrink-0 text-muted-foreground"
              onClick={() => fileRef.current?.click()}
              disabled={busy || attachments.uploading}
              aria-label="Attach files"
              title="Attach files"
              data-testid={`${textareaTestId}-attach`}
            >
              {attachments.uploading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Paperclip className="h-4 w-4" />}
            </Button>
          </>
        )}
        <Button
          type="button"
          variant={recording ? "destructive" : "ghost"}
          size="icon"
          className={cn("h-8 w-8 shrink-0", !recording && "text-muted-foreground")}
          onClick={recording ? stopRecording : startRecording}
          disabled={busy}
          aria-label={recording ? "Stop recording" : "Start recording"}
          title={recording ? "Stop recording (or release Space)" : "Start recording (or hold Space)"}
          data-testid={`${textareaTestId}-mic`}
        >
          {recording ? <MicOff className="h-4 w-4" /> : <Mic className="h-4 w-4" />}
        </Button>
      </div>
      {attachments?.references && attachments.references.options.length > 0 && (
        <select
          className="h-7 rounded-md border bg-background px-2 text-xs"
          aria-label="Attach an existing skill or tool"
          data-testid={`${textareaTestId}-attach-existing`}
          value=""
          disabled={busy || attachments.uploading || attachments.items.length >= (attachments.maxItems ?? 5)}
          onChange={(e) => {
            const [kind, ...rest] = e.target.value.split(":");
            if (kind && rest.length) void attachments.references?.add(kind as "skill" | "tool", rest.join(":"));
          }}
        >
          <option value="">Attach existing…</option>
          {attachments.references.options.map((o) => (
            <option key={`${o.kind}:${o.name}`} value={`${o.kind}:${o.name}`}>{o.kind}: {o.name}</option>
          ))}
        </select>
      )}
      {attachments && attachments.items.length > 0 && (
        <ul className="flex flex-wrap gap-1.5" data-testid={`${textareaTestId}-chips`}>
          {attachments.items.map((a) => (
            <li
              key={a.id}
              data-testid="attachment-chip"
              data-verdict={a.verdict}
              className={cn(
                "inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-xs",
                a.verdict === "refused" ? "border-destructive/50 bg-destructive/5 text-destructive" : "border-border bg-muted/40",
              )}
            >
              <Paperclip className="h-3 w-3" />
              <span className="max-w-[14rem] truncate">{a.name}</span>
              <span className="text-muted-foreground" data-testid="attachment-verdict">
                {VERDICT_LABEL[a.verdict]}{a.note ? `: ${a.note}` : ""}
              </span>
              <button type="button" aria-label={`Remove ${a.name}`} onClick={() => attachments.remove(a.id)} disabled={busy}>
                <X className="h-3 w-3" />
              </button>
            </li>
          ))}
        </ul>
      )}
      {(attachments?.error || dropNote || refused.length > 0) && (
        <p className="text-xs text-destructive" role="alert" data-testid={`${textareaTestId}-attach-note`}>
          {attachments?.error ?? dropNote ?? "Remove the refused attachment to send."}
        </p>
      )}
      {extras}
      <div className="flex items-center justify-between text-[10px] text-muted-foreground">
        <span>Enter sends · Shift+Enter for a new line · hold Space to dictate</span>
        <span
          className={over ? "text-destructive" : undefined}
          data-testid={`${textareaTestId}-count`}
        >
          {length}/{FORGE_REQUEST_MAX}
        </span>
      </div>
      <Button
        onClick={onSubmit}
        disabled={submitDisabled || over || blocked}
        className="w-full"
        data-testid={submitTestId}
      >
        {busy && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
        {busy ? busyLabel : submitLabel}
      </Button>
    </div>
  );
}
