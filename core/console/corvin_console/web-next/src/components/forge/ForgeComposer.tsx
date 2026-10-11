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
import { Loader2, Mic, MicOff } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useVoiceInput } from "@/hooks/use-voice-input";
import { useAutosizeTextarea } from "@/hooks/use-autosize-textarea";
import { cn } from "@/lib/utils";

/** The server's bounds for a generate request (forge_creator.GenerateRequest). */
export const FORGE_REQUEST_MIN = 10;
export const FORGE_REQUEST_MAX = 4000;

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
  /** Extra controls on the composer's bottom row, e.g. attachment chips (CONCEPT-0104 P3). */
  extras?: React.ReactNode;
}

export default function ForgeComposer({
  value, onChange, onSubmit, busy, submitDisabled, csrf, onError,
  textareaTestId, submitTestId, textareaId, label, placeholder, submitLabel, busyLabel, rows = 3, extras,
}: ForgeComposerProps) {
  const ref = React.useRef<HTMLTextAreaElement | null>(null);
  useAutosizeTextarea(ref, value, 320);
  const { recording, startRecording, stopRecording } = useVoiceInput({ value, onChange, csrf, disabled: busy, onError });
  const length = value.trim().length;
  const over = value.length > FORGE_REQUEST_MAX;

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key !== "Enter" || e.shiftKey || e.altKey || e.ctrlKey || e.metaKey) return;
    // An IME confirms a candidate with Enter; that must not send (keyCode 229 covers older engines).
    if (e.nativeEvent.isComposing || e.keyCode === 229) return;
    e.preventDefault();
    if (!busy && !submitDisabled && !recording && !over) onSubmit();
  };

  return (
    <div className="space-y-2" data-testid={`${textareaTestId}-composer`}>
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
        disabled={submitDisabled || over}
        className="w-full"
        data-testid={submitTestId}
      >
        {busy && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
        {busy ? busyLabel : submitLabel}
      </Button>
    </div>
  );
}
