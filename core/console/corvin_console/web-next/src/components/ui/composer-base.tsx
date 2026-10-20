/**
 * ComposerBase — shared input container for Chat and all Forge systems.
 *
 * Provides:
 * - Pill-card layout (flex items-end gap-2, rounded-2xl border)
 * - Textarea with autosize and IME-aware Enter handling
 * - Voice input via useVoiceInput hook (push-to-talk Space-hold >=400ms)
 * - Long-press support on mic button (>=400ms hold starts recording, release stops)
 * - Touch targets: 44px minimum on touch devices (hover: none / pointer: coarse)
 * - Slots for optional buttons (attach, folder, mic, send) and features (attachments, counter)
 * - Chat-specific slots: CommandPalette, entity hints (via children props)
 * - Forge-specific features: attachments controller, character counter, verdict labels
 *
 * Accepts all controls via optional props; buttons/children render in order.
 *
 * Performance contract (2026-10-20, chat typing lag in long sessions):
 * - `ComposerBase` is wrapped in React.memo. It re-renders only when a prop
 *   changes, so a parent that re-renders for another reason costs nothing here.
 *   Inline callbacks/render functions defeat the memo: keep them stable.
 * - Typing never forces a layout read on the normal path (see
 *   useAutosizeTextarea).
 * - To keep the draft out of the parent entirely, use `LocalDraftComposer`:
 *   it owns the draft text, so a keystroke re-renders the composer alone.
 *   The parent only sees the text on submit (and per keystroke if it passes
 *   `onDraftChange`). The controlled `ComposerBase` API is unchanged.
 */
import * as React from "react";
import { Mic, MicOff } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useVoiceInput } from "@/hooks/use-voice-input";
import { useAutosizeTextarea } from "@/hooks/use-autosize-textarea";
import { cn } from "@/lib/utils";

export interface ComposerBaseHandle {
  /** Get the underlying textarea element (for CommandPalette selection, etc.). */
  getTextarea(): HTMLTextAreaElement | null;
  /** Stop a live recording (the transcript stays in the field). */
  stopRecording(): void;
}

export interface ComposerBaseProps {
  /** Current input value. */
  value: string;
  /** Called on textarea change. */
  onChange: (v: string) => void;
  /** Called by Enter or send button. */
  onSubmit: () => void;
  /** CSRF token for voice input. */
  csrf: string;
  /** Disables all input (while streaming or busy). */
  disabled?: boolean;
  /** Disables just the submit action. */
  submitDisabled?: boolean;
  /** Called when voice input fails. */
  onError?: (message: string) => void;
  /** Called when auth token expires during voice. */
  onAuthExpired?: () => void;
  /** Called whenever the recording flag flips. */
  onRecordingChange?: (recording: boolean) => void;
  /** Voice commit (see useVoiceInput): asked at release, receives the transcript. */
  commitVoiceOnRelease?: () => boolean;
  onVoiceCommit?: (text: string) => void;
  /** Placeholder text. */
  placeholder?: string;
  /** Textarea rows (defaults to 1). */
  rows?: number;
  /** Max height for autosize (defaults to 320px). */
  maxHeight?: number;
  /** Test IDs for textarea and buttons. */
  textareaTestId?: string;
  micTestId?: string;
  /** Textarea id (required for label association). */
  textareaId?: string;

  /** Render function for optional pre-buttons (e.g., attach button). */
  renderPreButtons?: () => React.ReactNode;
  /** Render function for the mic button. If not provided, a default is rendered. */
  renderMicButton?: (recording: boolean, onStart: () => Promise<void>, onStop: () => void, disabled: boolean) => React.ReactNode;
  /** Render function for the submit/send button. */
  renderPostButton?: () => React.ReactNode;
  /** Content to render after the pill-card (e.g., attachments, counter). */
  footer?: React.ReactNode;
  /** Optional chat-specific slots (CommandPalette, entity hints, etc.). */
  beforeTextarea?: React.ReactNode;
  afterTextarea?: React.ReactNode;

  /** Space-hold dictation only while THIS textarea has focus (default: anywhere on the page). */
  pushToTalkFocusOnly?: boolean;

  /** Optional: override the pill-card class name for styling variations. */
  pillCardClassName?: string;
}

/**
 * Hook to detect long-press on an element (touch or mouse).
 * Fires onPress when hold >= duration, and onRelease on release.
 */
function useLongPress(options: {
  onPress?: () => void;
  onRelease?: () => void;
  duration?: number;
  disabled?: boolean;
}) {
  const { onPress, onRelease, duration = 400, disabled = false } = options;
  const timerRef = React.useRef<ReturnType<typeof setTimeout> | null>(null);
  const pressedRef = React.useRef(false);

  const handleMouseDown = React.useCallback(() => {
    if (disabled) return;
    pressedRef.current = false;
    timerRef.current = setTimeout(() => {
      pressedRef.current = true;
      onPress?.();
    }, duration);
  }, [disabled, duration, onPress]);

  const handleMouseUp = React.useCallback(() => {
    if (timerRef.current) clearTimeout(timerRef.current);
    if (pressedRef.current) {
      onRelease?.();
    }
    pressedRef.current = false;
  }, [onRelease]);

  const handleTouchStart = React.useCallback(
    (e: React.TouchEvent) => {
      if (disabled) return;
      // Prevent context menu on long-press
      e.preventDefault();
      pressedRef.current = false;
      timerRef.current = setTimeout(() => {
        pressedRef.current = true;
        try {
          navigator.vibrate?.(100);
        } catch {
          // vibrate not supported
        }
        onPress?.();
      }, duration);
    },
    [disabled, duration, onPress],
  );

  const handleTouchEnd = React.useCallback(() => {
    if (timerRef.current) clearTimeout(timerRef.current);
    if (pressedRef.current) {
      onRelease?.();
    }
    pressedRef.current = false;
  }, [onRelease]);

  return { handleMouseDown, handleMouseUp, handleTouchStart, handleTouchEnd };
}

const ComposerBaseImpl = React.forwardRef<ComposerBaseHandle, ComposerBaseProps>(
  function ComposerBase(
    {
      value,
      onChange,
      onSubmit,
      csrf,
      disabled = false,
      submitDisabled = false,
      onError,
      onAuthExpired,
      onRecordingChange,
      commitVoiceOnRelease,
      onVoiceCommit,
      placeholder = "Message…",
      rows = 1,
      maxHeight = 320,
      textareaTestId = "composer-textarea",
      micTestId = "composer-mic",
      textareaId = "composer-input",
      renderPreButtons,
      renderMicButton,
      renderPostButton,
      footer,
      beforeTextarea,
      afterTextarea,
      pillCardClassName,
      pushToTalkFocusOnly = false,
    },
    ref,
  ) {
    const textareaRef = React.useRef<HTMLTextAreaElement>(null);

    useAutosizeTextarea(textareaRef, value, maxHeight);
    const pushToTalkTarget = React.useCallback(() => textareaRef.current, []);

    const { recording, startRecording, stopRecording } = useVoiceInput({
      value,
      onChange,
      csrf,
      disabled,
      onError,
      onAuthExpired,
      onRecordingChange,
      commitOnRelease: commitVoiceOnRelease,
      onCommit: onVoiceCommit,
      pushToTalkTarget: pushToTalkFocusOnly ? pushToTalkTarget : undefined,
    });

    React.useImperativeHandle(
      ref,
      () => ({
        getTextarea: () => textareaRef.current,
        stopRecording,
      }),
      [stopRecording],
    );

    // Long-press support on mic button (touch-friendly)
    const micLongPress = useLongPress({
      onPress: () => void startRecording(),
      onRelease: stopRecording,
      duration: 400,
      disabled: disabled || recording,
    });

    const handleKeyDown = React.useCallback(
      (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
        if (e.key === "Enter" && !e.shiftKey && !e.metaKey && !e.ctrlKey) {
          // IME candidate confirmation is never sent
          if (e.nativeEvent.isComposing || e.keyCode === 229) return;
          e.preventDefault();
          if (!disabled && !submitDisabled && !recording) onSubmit();
        }
      },
      [disabled, submitDisabled, recording, onSubmit],
    );

    // Default mic button with long-press support
    const defaultMicButton =
      renderMicButton === undefined ? (
        <Button
          type="button"
          variant={recording ? "destructive" : "ghost"}
          size="icon"
          className={cn(
            "h-8 w-8 shrink-0 sm:h-10 sm:w-10", // 44px on touch devices
            !recording && "text-muted-foreground",
          )}
          onClick={recording ? stopRecording : () => void startRecording()}
          onMouseDown={micLongPress.handleMouseDown}
          onMouseUp={micLongPress.handleMouseUp}
          onTouchStart={micLongPress.handleTouchStart}
          onTouchEnd={micLongPress.handleTouchEnd}
          disabled={disabled}
          aria-label={recording ? "Stop recording" : "Start recording"}
          title={
            recording
              ? "Stop recording (or release Space)"
              : "Start recording (or hold Space/press)"
          }
          data-testid={micTestId}
        >
          {recording ? <MicOff className="h-4 w-4" /> : <Mic className="h-4 w-4" />}
        </Button>
      ) : (
        renderMicButton(
          recording,
          () => void startRecording(),
          stopRecording,
          disabled,
        )
      );

    return (
      <div>
        {beforeTextarea}
        <div
          className={cn(
            "flex items-end gap-2 rounded-2xl border border-border bg-card px-2 py-1.5 shadow-sm transition-colors focus-within:border-accent/50 focus-within:ring-2 focus-within:ring-accent/15",
            pillCardClassName,
          )}
        >
          {renderPreButtons && (
            <>
              {renderPreButtons()}
            </>
          )}

          <Textarea
            ref={textareaRef}
            id={textareaId}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={disabled || recording}
            rows={rows}
            placeholder={
              recording
                ? "Listening — release Space or mic to stop"
                : placeholder
            }
            className="min-h-[2rem] resize-none border-0 bg-transparent px-1 py-1 font-sans text-sm leading-relaxed shadow-none focus-visible:ring-0 focus-visible:ring-offset-0"
            data-testid={textareaTestId}
          />

          {defaultMicButton}

          {renderPostButton && (
            <>
              {renderPostButton()}
            </>
          )}
        </div>
        {afterTextarea}
        {footer}
      </div>
    );
  },
);

/** Controlled composer: the parent owns `value` and `onChange`. */
export const ComposerBase = React.memo(ComposerBaseImpl);
ComposerBase.displayName = "ComposerBase";

export interface LocalDraftComposerProps
  extends Omit<ComposerBaseProps, "value" | "onChange" | "onSubmit" | "renderPostButton"> {
  /** Draft text at mount. Read once; later changes go through `resetKey`. */
  initialValue?: string;
  /** Change this value to clear the draft (e.g. after the parent sent it).
   *  Use the sending chat's id, or a counter. */
  resetKey?: string | number;
  /** Called on every keystroke. Optional: without it the parent does not
   *  re-render while the user types. */
  onDraftChange?: (value: string) => void;
  /** Called with the draft on Enter. The draft is NOT cleared here; change
   *  `resetKey` once the send has gone through. */
  onSubmit: (value: string) => void;
  /** Send button. Receives the live draft, so it can disable itself when empty. */
  renderPostButton?: (ctx: { draft: string; submit: () => void }) => React.ReactNode;
}

/**
 * ComposerBase with the draft held locally (see the performance contract in
 * the module comment). Same props as ComposerBase except value/onChange/
 * onSubmit/renderPostButton, which become the local-draft variants above.
 */
export const LocalDraftComposer = React.memo(
  React.forwardRef<ComposerBaseHandle, LocalDraftComposerProps>(function LocalDraftComposer(
    { initialValue = "", resetKey, onDraftChange, onSubmit, renderPostButton, ...rest },
    ref,
  ) {
    const [draft, setDraft] = React.useState(initialValue);
    const [seenResetKey, setSeenResetKey] = React.useState(resetKey);
    // Latest draft for submit(), so the callbacks keep their identity per keystroke.
    const draftRef = React.useRef(draft);
    // Derived-state reset during render (no effect, no extra commit): a changed
    // resetKey clears the draft before the next paint.
    if (resetKey !== seenResetKey) {
      setSeenResetKey(resetKey);
      setDraft("");
      draftRef.current = "";
    }

    const handleChange = React.useCallback(
      (v: string) => {
        draftRef.current = v;
        setDraft(v);
        onDraftChange?.(v);
      },
      [onDraftChange],
    );
    const submit = React.useCallback(() => onSubmit(draftRef.current), [onSubmit]);
    const postButton = renderPostButton
      ? () => renderPostButton({ draft, submit })
      : undefined;

    return (
      <ComposerBase
        ref={ref}
        {...rest}
        value={draft}
        onChange={handleChange}
        onSubmit={submit}
        renderPostButton={postButton}
      />
    );
  }),
);
LocalDraftComposer.displayName = "LocalDraftComposer";
