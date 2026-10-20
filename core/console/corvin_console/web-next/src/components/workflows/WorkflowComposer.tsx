/**
 * WorkflowComposer — the workflow editor's input, built from the SAME shared
 * composer field as the main chat (CONCEPT-0115): Enter sends, Shift+Enter is a
 * newline, hold Space or press the mic to dictate INTO the field (the transcript
 * is never sent unseen), `/` opens the command palette.
 *
 * The context (design chat, run reply) brings only: where Send goes, its
 * command table and its placeholder. Gates, tenant and validity are the
 * server's job on every request.
 */
import * as React from "react";
import { Send } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ChatComposerField, type ComposerFieldHandle } from "@/components/chat/ChatComposerField";
import type { SlashCommand } from "@/components/chat/SlashCommandPalette";

export interface WorkflowComposerProps {
  /** Keys DOM ids so two composers on one page never share an id. */
  contextKey: string;
  csrf: string;
  /** false while the transport is down: the field is disabled, nothing is lost. */
  connected: boolean;
  placeholder: string;
  commands: readonly SlashCommand[];
  /** Called with the trimmed text. The field clears once it returns. */
  onSend: (text: string) => void;
  onAuthExpired?: () => void;
}

export const WorkflowComposer = React.memo(function WorkflowComposer({
  contextKey, csrf, connected, placeholder, commands, onSend, onAuthExpired,
}: WorkflowComposerProps) {
  const fieldRef = React.useRef<ComposerFieldHandle>(null);
  const [hasText, setHasText] = React.useState(false);
  const [recording, setRecording] = React.useState(false);
  const [voiceError, setVoiceError] = React.useState<string | null>(null);
  const noHint = React.useCallback(() => {}, []);

  const submit = React.useCallback((raw: string) => {
    const text = raw.trim();
    if (!text || !connected) return;
    onSend(text);
    fieldRef.current?.setValue("");
  }, [connected, onSend]);

  return (
    <div className="shrink-0 border-t border-border p-3" data-testid="workflow-composer">
      {voiceError && (
        <button
          type="button"
          className="mb-2 flex w-full items-center justify-between rounded-md bg-destructive/10 px-2 py-1.5 text-left text-xs text-destructive"
          onClick={() => setVoiceError(null)}
          data-testid="workflow-composer-voice-error"
        >
          <span>{voiceError}</span>
          <span className="ml-2 opacity-60" aria-hidden>✕</span>
        </button>
      )}
      <div className="flex items-end gap-2 rounded-2xl border border-border bg-card px-2 py-1.5 shadow-sm transition-colors focus-within:border-accent/50 focus-within:ring-2 focus-within:ring-accent/15">
        <ChatComposerField
          ref={fieldRef}
          placeholder={recording ? "Listening — release Space to stop" : connected ? placeholder : "Connecting…"}
          disabled={!connected}
          csrf={csrf}
          onSubmit={submit}
          onHasTextChange={setHasText}
          onEntityHintChange={noHint}
          onRecordingChange={setRecording}
          onVoiceError={setVoiceError}
          onAuthExpired={onAuthExpired}
          extraCommands={commands}
          sessionCommands={false}
          textareaId={`wf-composer-${contextKey}`}
          textareaTestId="workflow-composer-textarea"
          micTestId="workflow-composer-mic"
          pushToTalkFocusOnly
        />
        <Button
          variant="accent"
          size="icon"
          className="h-8 w-8 shrink-0 rounded-full"
          onClick={() => submit(fieldRef.current?.getValue() ?? "")}
          disabled={!connected || !hasText}
          title="Send (Enter · Shift+Enter for newline)"
          data-testid="workflow-composer-send"
        >
          <Send className="h-4 w-4" />
        </Button>
      </div>
    </div>
  );
});
