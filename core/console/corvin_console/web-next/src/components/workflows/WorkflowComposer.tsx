/**
 * WorkflowComposer — the workflow editor's input, built from the SAME shared
 * composer field and attachment pieces as the main chat (CONCEPT-0115): paperclip,
 * folder, drag-and-drop (via the handle), attachment chips, dictation INTO the
 * field (never sent unseen), `/` command palette, Enter sends, Shift+Enter newline.
 *
 * The context (design chat, run reply) hands in a `ComposerContextAdapter`: where
 * uploads go, its commands, what Send does. Gates, tenant and attachment validity
 * are the server's job on every request — the chips here are a convenience view.
 */
import * as React from "react";
import { FolderUp, Loader2, Paperclip, Send } from "lucide-react";
import { Button } from "@/components/ui/button";
import { AttachmentChip } from "@/components/chat/AttachmentChip";
import { ChatComposerField, type ComposerFieldHandle } from "@/components/chat/ChatComposerField";
import {
  checkUploadLimits,
  type ComposerAttachmentRef,
  type ComposerContextAdapter,
} from "@/components/chat/composer-adapter";
import { useAttachmentUpload } from "@/hooks/use-attachment-upload";
import { MAX_DROPPED_FILES, supportsDirectoryDrop } from "@/hooks/use-file-drop";

export interface WorkflowComposerProps {
  csrf: string;
  /** false while the transport is down: the field is disabled, nothing is lost. */
  connected: boolean;
  placeholder: string;
  adapter: ComposerContextAdapter;
  onAuthExpired?: () => void;
  /** Hide the paperclip/folder row for a context that takes text only. */
  attachments?: boolean;
}

export interface WorkflowComposerHandle {
  /** Stage files (drag-and-drop on the surrounding pane lands here). */
  addFiles: (files: File[]) => void;
}

export const WorkflowComposer = React.memo(React.forwardRef<WorkflowComposerHandle, WorkflowComposerProps>(
  function WorkflowComposer({ csrf, connected, placeholder, adapter, onAuthExpired, attachments = true }, ref) {
    const fieldRef = React.useRef<ComposerFieldHandle>(null);
    const folderInputRef = React.useRef<HTMLInputElement>(null);
    const [hasText, setHasText] = React.useState(false);
    const [recording, setRecording] = React.useState(false);
    const [voiceError, setVoiceError] = React.useState<string | null>(null);
    const [removeError, setRemoveError] = React.useState<string | null>(null);
    const [truncated, setTruncated] = React.useState<number | null>(null);
    const noHint = React.useCallback(() => {}, []);

    const adapterRef = React.useRef(adapter);
    adapterRef.current = adapter;
    const uploadFn = React.useCallback(async (files: File[]) => {
      const a = adapterRef.current;
      const refusal = checkUploadLimits(files, a.limits);
      if (refusal) throw new Error(refusal);
      return a.upload(files);
    }, []);
    const {
      pendingAttachments, uploading, uploadError, addFiles,
      removeAttachment, clearAttachments, fileInputRef, onFileInputChange,
    } = useAttachmentUpload<ComposerAttachmentRef>({ uploadFn, disabled: !connected });

    React.useImperativeHandle(ref, () => ({
      addFiles: (files) => { setTruncated(null); void addFiles(files); },
    }), [addFiles]);

    const submit = React.useCallback((raw: string) => {
      const text = raw.trim();
      if ((!text && pendingAttachments.length === 0) || !connected || uploading) return;
      adapterRef.current.send(text, pendingAttachments);
      fieldRef.current?.setValue("");
      clearAttachments();
    }, [connected, uploading, pendingAttachments, clearAttachments]);

    const remove = React.useCallback((index: number) => {
      const att = pendingAttachments[index];
      removeAttachment(index);
      setRemoveError(null);
      const rm = adapterRef.current.remove;
      if (att && rm) rm(att).catch((e: unknown) => setRemoveError(e instanceof Error ? e.message : "Could not remove the attachment."));
    }, [pendingAttachments, removeAttachment]);

    const canSend = connected && !uploading && (hasText || pendingAttachments.length > 0);

    return (
      <div className="shrink-0 border-t border-border p-3" data-testid="workflow-composer">
        {attachments && (
          <>
            <input
              ref={fileInputRef} type="file" multiple className="sr-only"
              aria-label="Attach files" onChange={onFileInputChange} data-testid="workflow-file-input"
            />
            <input
              ref={folderInputRef} type="file" multiple
              // @ts-expect-error -- webkitdirectory has no TS lib.dom typing
              webkitdirectory="" directory="" className="sr-only"
              aria-label="Attach a folder" data-testid="workflow-folder-input"
              onChange={(e) => {
                const files = Array.from(e.target.files ?? []).slice(0, MAX_DROPPED_FILES);
                if (e.target.files && e.target.files.length > MAX_DROPPED_FILES) setTruncated(MAX_DROPPED_FILES);
                e.target.value = "";
                if (files.length > 0) void addFiles(files);
              }}
            />
          </>
        )}
        {pendingAttachments.length > 0 && (
          <div className="mb-2 flex flex-wrap gap-1.5" data-testid="workflow-attachment-bar">
            {pendingAttachments.map((a, i) => (
              <AttachmentChip key={a.id} attachment={a} onRemove={() => remove(i)} />
            ))}
          </div>
        )}
        {(uploadError || removeError) && (
          <p className="mb-2 text-xs text-destructive" data-testid="workflow-upload-error">{uploadError ?? removeError}</p>
        )}
        {truncated != null && (
          <p className="mb-2 text-xs text-amber-600 dark:text-amber-400">
            Only the first {truncated} files were attached — drop fewer at once for the rest.
          </p>
        )}
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
          {attachments && (
            <>
              <Button
                variant="ghost" size="icon" className="h-8 w-8 shrink-0 text-muted-foreground"
                onClick={() => fileInputRef.current?.click()} disabled={!connected || uploading}
                title="Attach files (text, CSV, JSON, …)" data-testid="workflow-attach-button"
              >
                {uploading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Paperclip className="h-4 w-4" />}
              </Button>
              {supportsDirectoryDrop && (
                <Button
                  variant="ghost" size="icon" className="h-8 w-8 shrink-0 text-muted-foreground"
                  onClick={() => folderInputRef.current?.click()} disabled={!connected || uploading}
                  title="Attach a folder" data-testid="workflow-attach-folder-button"
                >
                  <FolderUp className="h-4 w-4" />
                </Button>
              )}
            </>
          )}
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
            extraCommands={adapter.extraCommands}
            sessionCommands={adapter.sessionCommands}
            textareaId={`wf-composer-${adapter.contextKey}`}
            textareaTestId="workflow-composer-textarea"
            micTestId="workflow-composer-mic"
            pushToTalkFocusOnly
          />
          <Button
            variant="accent" size="icon" className="h-8 w-8 shrink-0 rounded-full"
            onClick={() => submit(fieldRef.current?.getValue() ?? "")} disabled={!canSend}
            title="Send (Enter · Shift+Enter for newline)" data-testid="workflow-composer-send"
          >
            <Send className="h-4 w-4" />
          </Button>
        </div>
      </div>
    );
  },
));
