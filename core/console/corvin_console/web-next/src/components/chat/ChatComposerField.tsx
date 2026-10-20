/**
 * ChatComposerField — the one composer text field, shared by every surface that
 * takes typed, dictated or slash-command input (session chat, workflow design
 * chat, workflow run replies).
 *
 * Extracted verbatim from pages/chat.tsx (CONCEPT-0115, phase 1). The chat's
 * behaviour is unchanged: every new prop defaults to what the chat used before.
 *
 * The typed text lives HERE, not in the host page: with the state in the page,
 * every keystroke re-rendered the whole pane (message list reconciliation, task
 * panels) and typing lagged on long conversations. Now a keystroke re-renders
 * this component only. The host talks to it through the imperative handle
 * (voice dictation, send, "try this" samples) and learns about the two facts it
 * renders from — "is there any text" and the entity hint — via callbacks that
 * fire only when those values actually change.
 *
 * What a context brings (see composer-adapter.ts): its slash commands, its
 * submit logic, its voice-commit policy. What it must never bring: gate or
 * tenant decisions — the server stays the authority.
 */
import * as React from "react";
import { ComposerBase, type ComposerBaseHandle } from "@/components/ui/composer-base";
import {
  CommandPalette,
  applyCommandInsertion,
  useSlashCommandPalette,
  type SlashCommand,
} from "@/components/chat/SlashCommandPalette";

export interface ComposerFieldHandle {
  getValue: () => string;
  setValue: (next: string | ((prev: string) => string)) => void;
  closePalette: () => void;
  stopRecording: () => void;
}

// Mirrors entity_extract.py heuristics; pure, so it lives outside the component.
export function detectEntityHint(text: string): string | null {
  const t = text.trim();
  if (!t || t.length < 4) return null;
  if (/^ats\s*:/i.test(t))       return "ATS Task";
  if (/^a2a\s*:/i.test(t))       return "A2A Session";
  if (/^workflow\s*:/i.test(t))  return "Workflow";
  if (/^forge\s*:/i.test(t))     return "Forge Tool";
  if (/^skill\s*:/i.test(t))     return "Skill";
  if (/^rag\s*:/i.test(t))       return "RAG Source";
  if (/\b(workflow|awpkg|flow|pipeline)\b/i.test(t) && t.length > 8) return "Workflow";
  if (/\b(ats[\s_-]task|ats:)\b/i.test(t)) return "ATS Task";
  if (/\b(a2a|agent[\s-]to[\s-]agent|mesh)\b/i.test(t)) return "A2A";
  if (/\b(forge[\s-]tool|werkzeug)\b/i.test(t)) return "Forge Tool";
  if (/\b(skillforge|skill[\s-]forge)\b/i.test(t)) return "Skill";
  if (/\b(erasure|lösch\w+|erase)\b/i.test(t)) return "Erasure";
  if (/\b(vault|geheimnis|secret[\s-]vault)\b/i.test(t)) return "Vault";
  if (/\b(audit[\s-]log|hash[\s-]chain)\b/i.test(t)) return "Audit";
  return null;
}

const NO_COMMANDS: readonly SlashCommand[] = [];

export interface ChatComposerFieldProps {
  placeholder: string;
  disabled: boolean;
  onSubmit: (text: string) => void;
  onHasTextChange: (hasText: boolean) => void;
  onEntityHintChange: (hint: string | null) => void;
  csrf?: string; // Required for voice input in ComposerBase
  onRecordingChange?: (recording: boolean) => void;
  onVoiceError?: (message: string) => void;
  onAuthExpired?: () => void;
  commitVoiceOnRelease?: () => boolean;
  onVoiceCommit?: (text: string) => void;
  /** Context commands merged into (or, with sessionCommands=false, replacing) the chat table. */
  extraCommands?: readonly SlashCommand[];
  /** false: show ONLY extraCommands (a surface whose `/` lines go to another dispatcher). */
  sessionCommands?: boolean;
  textareaId?: string;
  textareaTestId?: string;
  micTestId?: string;
  /** Outer pill override. The chat's row draws the pill itself, so it neutralises ComposerBase's. */
  pillCardClassName?: string;
  /** Pass a stable value to keep the draft per context; the chat keeps its own `key`. */
  initialValue?: string;
  /** Space-hold dictation only while the field has focus (surfaces where Space is also a canvas key). */
  pushToTalkFocusOnly?: boolean;
}

// The chat row (attach / mic / send) already draws the one pill border and focus
// ring around this field. ComposerBase's own pill would paint a second border and
// ring inside it, so the default neutralises it.
const NEUTRAL_PILL =
  "border-0 bg-transparent p-0 shadow-none focus-within:border-transparent focus-within:ring-0";

/**
 * Shared composer field using ComposerBase (ADR-2252-WIRED).
 * Features: voice input (Space-hold >=400ms, mic button long-press), touch targets 44px,
 * Enter/Shift+Enter, IME-aware, CommandPalette, entity hints.
 */
export const ChatComposerField = React.memo(
  React.forwardRef<ComposerFieldHandle, ChatComposerFieldProps>(function ChatComposerField(
    {
      placeholder, disabled, onSubmit, onHasTextChange, onEntityHintChange, csrf = "",
      onRecordingChange, onVoiceError, onAuthExpired, commitVoiceOnRelease, onVoiceCommit,
      extraCommands = NO_COMMANDS,
      sessionCommands = true,
      textareaId = "chat-input",
      textareaTestId = "chat-composer-textarea",
      micTestId = "chat-composer-mic",
      pillCardClassName = NEUTRAL_PILL,
      initialValue = "",
      pushToTalkFocusOnly = false,
    },
    ref,
  ) {
    const [value, setValueState] = React.useState(initialValue);
    // Always current, also between a handle write and the next commit.
    const valueRef = React.useRef(initialValue);
    const composerRef = React.useRef<ComposerBaseHandle>(null); // Ref to ComposerBase to access textarea (UNIFIED_COMPOSER_IMPLEMENTATION)
    const slashPalette = useSlashCommandPalette(value, extraCommands, { sessionCommands });

    const setValue = React.useCallback(
      (next: string | ((prev: string) => string)) => {
        const v = typeof next === "function" ? next(valueRef.current) : next;
        valueRef.current = v;
        setValueState(v);
      },
      [],
    );

    const closePaletteRef = React.useRef(slashPalette.close);
    closePaletteRef.current = slashPalette.close;
    React.useImperativeHandle(
      ref,
      () => ({
        getValue: () => valueRef.current,
        setValue,
        closePalette: () => closePaletteRef.current(),
        stopRecording: () => composerRef.current?.stopRecording(),
      }),
      [setValue],
    );

    const hasText = value.trim().length > 0;
    React.useEffect(() => { onHasTextChange(hasText); }, [hasText, onHasTextChange]);

    // Deferred: the regex cascade must never delay the keystroke itself.
    const deferredValue = React.useDeferredValue(value);
    const entityHint = React.useMemo(() => detectEntityHint(deferredValue), [deferredValue]);
    const shownHint = slashPalette.open ? null : entityHint;
    React.useEffect(() => { onEntityHintChange(shownHint); }, [shownHint, onEntityHintChange]);

    // Wrapping onSubmit to also close the palette
    const handleComposerSubmit = React.useCallback(() => {
      slashPalette.close();
      onSubmit(valueRef.current);
    }, [slashPalette, onSubmit]);

    return (
      <div className="relative min-w-0 flex-1" data-composer-field="shared">
        <ComposerBase
          ref={composerRef}
          value={value}
          onChange={(v) => {
            valueRef.current = v;
            setValueState(v);
            slashPalette.onChange(v);
          }}
          onSubmit={handleComposerSubmit}
          disabled={disabled}
          csrf={csrf}
          onError={onVoiceError}
          onAuthExpired={onAuthExpired}
          onRecordingChange={onRecordingChange}
          commitVoiceOnRelease={commitVoiceOnRelease}
          onVoiceCommit={onVoiceCommit}
          placeholder={placeholder}
          textareaId={textareaId}
          textareaTestId={textareaTestId}
          micTestId={micTestId}
          pillCardClassName={pillCardClassName}
          pushToTalkFocusOnly={pushToTalkFocusOnly}
          beforeTextarea={
            <CommandPalette
              matches={slashPalette.matches}
              selected={slashPalette.selected}
              onSelect={(match) => {
                const textareaEl = composerRef.current?.getTextarea();
                if (textareaEl) {
                  applyCommandInsertion(match, setValue, { current: textareaEl });
                }
                slashPalette.close();
              }}
            />
          }
        />
      </div>
    );
  }),
);
