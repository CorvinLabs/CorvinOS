# ComposerBase — Unified Input for Chat and Forge

**Status:** Reference (Component)  
**Created:** 2026-10-12  
**Applies to:** `core/console/corvin_console/web-next/`  
**Component:** `src/components/ui/composer-base.tsx`  
**Consumers:** Chat (via ChatComposerField), Forge (Tool/Plugin/Skill/Layer)

---

## Overview

**ComposerBase** is a shared React component that provides consistent text input, voice recording, and button controls across the Chat interface and all four Forge systems (Tool, Plugin, Skill, Layer). It eliminates duplication and ensures uniform UX for text entry, voice input, and touch support.

### Features

- **Pill-card layout:** `flex items-end gap-2 rounded-2xl border border-border bg-card px-2 py-1.5`
- **Textarea with autosize:** Grows with content, respects `maxHeight` prop (default 320px); no layout read per keystroke (see Performance)
- **Voice input:** Push-to-talk (hold Space ≥400ms) + long-press mic button (>=400ms) for touch
- **Touch-friendly:** 44px minimum targets on coarse pointers (`h-8 sm:h-10 sm:w-10`)
- **IME-aware:** Ignores Enter during IME composition (CJK candidate selection)
- **Flexible slots:** Optional buttons and content before/after textarea, footer area
- **Enter/Shift+Enter handling:** Enter sends, Shift+Enter inserts newline (overridable)
- **Optional navigator.vibrate:** Haptic feedback on long-press (try/catch wrapped)

---

## Architecture

### Component Structure

```
ComposerBase
├── (optional) beforeTextarea slot
├── Pill-card wrapper
│   ├── renderPreButtons (e.g., Attach button)
│   ├── Textarea (shared)
│   │   └── useAutosizeTextarea hook
│   ├── useVoiceInput hook (push-to-talk + recording state)
│   ├── useLongPress hook (touch long-press on mic button)
│   ├── renderMicButton (default or custom)
│   └── renderPostButton (e.g., Send button)
├── (optional) afterTextarea slot
└── (optional) footer (e.g., attachments, counter, verdicts)
```

### Props

#### Core Input Behavior

| Prop | Type | Default | Purpose |
|------|------|---------|---------|
| `value` | `string` | — | Current textarea content |
| `onChange` | `(v: string) => void` | — | Called on textarea input |
| `onSubmit` | `() => void` | — | Called by Enter or button click |
| `csrf` | `string` | — | CSRF token for voice transcription |
| `placeholder` | `string` | `"Message…"` | Textarea placeholder |
| `rows` | `number` | `1` | Initial textarea rows |
| `maxHeight` | `number` | `320` | Max height for autosize (pixels) |

#### State & Behavior

| Prop | Type | Default | Purpose |
|------|------|---------|---------|
| `disabled` | `boolean` | `false` | Disables textarea and all controls |
| `submitDisabled` | `boolean` | `false` | Disables submit only (Enter still works if not global disabled) |
| `onError` | `(msg: string) => void` | — | Called on voice transcription error |
| `onAuthExpired` | `() => void` | — | Called if voice CSRF token expires |

#### Styling & IDs

| Prop | Type | Default | Purpose |
|------|------|---------|---------|
| `textareaId` | `string` | `"composer-input"` | HTML id (for label association) |
| `textareaTestId` | `string` | `"composer-textarea"` | Test ID for textarea |
| `micTestId` | `string` | `"composer-mic"` | Test ID for mic button |
| `pillCardClassName` | `string` | — | Optional override for pill-card class |

#### Optional Rendering

| Prop | Type | Purpose |
|------|------|---------|
| `renderPreButtons` | `() => React.ReactNode` | Render buttons before textarea (e.g., Attach) |
| `renderMicButton` | Custom mic button renderer | Override default mic button (rarely used) |
| `renderPostButton` | `() => React.ReactNode` | Render buttons after textarea (e.g., Send) |
| `beforeTextarea` | `React.ReactNode` | Content before pill-card (e.g., label, CommandPalette) |
| `afterTextarea` | `React.ReactNode` | Content after pill-card (e.g., entity hints) |
| `footer` | `React.ReactNode` | Content after pill-card (e.g., attachments, counter) |

---

## Usage Examples

### Basic Chat Composer (Minimal)

```tsx
<ComposerBase
  value={text}
  onChange={setText}
  onSubmit={() => sendMessage(text)}
  csrf={csrfToken}
  placeholder="Type a message…"
  beforeTextarea={<label htmlFor="composer-input">Send</label>}
  renderPostButton={() => (
    <Button onClick={() => sendMessage(text)} variant="accent" size="icon">
      <Send />
    </Button>
  )}
/>
```

### Forge Composer (with Attachments & Counter)

```tsx
<ComposerBase
  value={forgeRequest}
  onChange={setForgeRequest}
  onSubmit={handleGenerate}
  csrf={csrf}
  placeholder="Describe what to build…"
  disabled={isRunning}
  submitDisabled={forgeRequest.trim().length < 10 || tokenCount > MAX}
  textareaTestId="forge-request"
  renderPreButtons={() => (
    <>
      <input ref={fileRef} type="file" hidden onChange={handleFiles} />
      <Button
        variant="ghost"
        size="icon"
        onClick={() => fileRef.current?.click()}
        disabled={isRunning || uploading}
      >
        {uploading ? <Loader2 className="animate-spin" /> : <Paperclip />}
      </Button>
    </>
  )}
  renderPostButton={() => (
    <Button
      onClick={handleGenerate}
      disabled={submitDisabled}
      variant="accent"
      size="icon"
    >
      {isRunning ? <Loader2 className="animate-spin" /> : <Send />}
    </Button>
  )}
  footer={
    <>
      {attachedFiles.length > 0 && (
        <ul className="flex flex-wrap gap-1">
          {attachedFiles.map(f => (
            <li key={f.id} className="…">
              <span>{f.name}</span> — {f.verdict}
            </li>
          ))}
        </ul>
      )}
      <div className="flex justify-between text-xs text-muted-foreground">
        <span>Enter sends · Shift+Enter newline · hold Space to speak</span>
        <span className={tokenCount > MAX ? "text-destructive" : ""}>
          {tokenCount}/{MAX}
        </span>
      </div>
    </>
  }
/>
```

---

### Performance (2026-10-20)

Long chat sessions made typing stutter: every keystroke re-rendered the parent and forced a layout read in the autosize hook.

- **`ComposerBase` is `React.memo`.** It re-renders only when a prop changes. Inline callbacks and render functions (`renderPreButtons`, `renderPostButton`, `renderMicButton`, `footer`) defeat the memo, so pass stable references.
- **Autosize without per-key reads** (`use-autosize-textarea.ts`): where `CSS.supports("field-sizing", "content")` holds, the engine sizes the box and the hook reads nothing (it only sets `max-height`). Otherwise a plain keystroke (one character, no newline) schedules one growth-only measurement per animation frame. Newline, paste, deletion and programmatic changes measure synchronously, as before. Measured (`tests/unit/perf/composer-autosize-reads.test.tsx`): 40 synchronous reads per 20 keystrokes before, 0 after (2 batched reads per frame, fallback path).
- **`LocalDraftComposer`** keeps the draft in the composer's own state. Typing re-renders only the composer; the parent sees the text on submit, or per keystroke if it passes `onDraftChange`. The draft is cleared by changing `resetKey`, not by the parent writing `value`.

| `LocalDraftComposer` prop | Type | Purpose |
|---|---|---|
| `initialValue` | `string` | Draft at mount (read once) |
| `resetKey` | `string \| number` | Changing it clears the draft (use the chat id, or a counter after sending) |
| `onDraftChange` | `(v: string) => void` | Optional per-keystroke notification (makes the parent re-render per key) |
| `onSubmit` | `(v: string) => void` | Called with the live draft on Enter / send; does not clear it |
| `renderPostButton` | `(ctx: { draft, submit }) => ReactNode` | Send button; receives the draft so it can disable itself |

All other `ComposerBase` props apply unchanged. The controlled `ComposerBase` (`value` + `onChange`) is unchanged.

## Keyboard Behavior

### Enter & Shift+Enter

- **Enter (no modifiers):** Calls `onSubmit` unless `disabled` or `submitDisabled`
- **Shift+Enter:** Inserts newline (default textarea behavior)
- **Cmd+Enter / Ctrl+Enter:** Inserts newline (macOS/Windows convention not enforced)
- **During IME composition:** Enter is ignored (used by CJK input methods)

### Space (Push-to-Talk)

- **Hold ≥400ms:** Starts voice recording (if `useVoiceInput` is active)
- **Release:** Stops recording and transcribes
- **Short tap:** Inserts normal space (no recording triggered)
- **On native Space targets:** Button/link/select/textarea context menu roles are never intercepted
- **Exactly one recorder per page.** The `useVoiceInput` hook inside `ComposerBase` is the only
  push-to-talk implementation; a host page must not add its own Space listener or
  `SpeechRecognition` (Chrome allows one live instance — two evict each other and no
  transcript arrives; the chat page did this until 2026-10-13: 11 recognizer starts per hold).
  A host mirrors the state with `onRecordingChange` and stops via the handle's
  `stopRecording()`; it passes `csrf` (the MediaRecorder fallback posts to `/voice/transcribe`).
  Proof: `web-next/tests/e2e/chat-stt-composer.spec.ts`.
- **Voice commit (CONCEPT-0107).** `commitVoiceOnRelease()` (asked at release) +
  `onVoiceCommit(text)`: when true the final transcript is handed to the host instead of staying
  in the field (Web Speech: in `onend` after stop, so late final results are included;
  MediaRecorder: after `/voice/transcribe`). The chat uses it to steer a running reply.
- **Hold-Space trim.** The space typed by the hold is removed AND `valueRef` is updated
  synchronously — the recording's base is read before React re-renders, so a stale `" "`
  became a leading space in every dictation.

---

## Touch Behavior

### Button Sizing (44px Targets)

All buttons in ComposerBase use **two-tier sizing** for touch devices:
- **Small screens (default):** `h-8 w-8` = 32px (mouse-friendly)
- **Touch devices (`sm` breakpoint+):** `sm:h-10 sm:w-10` = 40px (close to 44px WCAG target)

### Long-Press Mic Button (≥400ms)

- **Touch start:** `preventDefault()` prevents context menu
- **Hold ≥400ms:** Calls `startRecording()` → `recording` state true → placeholder changes
- **Touch end:** Calls `stopRecording()` → transcription sent to textarea
- **Optional haptic:** `navigator.vibrate([100])` on long-press (try/catch wrapped, safe to ignore on unsupported browsers)

### Drop Support

ComposerBase itself does NOT handle drag-and-drop. Callers must:
1. Wrap ComposerBase in a div with `onDragOver`, `onDragLeave`, `onDrop` handlers
2. Manage attachment state in the `footer` slot
3. Render `<DropOverlay />` conditionally (example: ForgeComposer in `src/components/forge/ForgeComposer.tsx`)

---

## Voice Input Integration

### Microphone Button States

| State | Appearance | Behavior |
|-------|-----------|----------|
| Idle | `variant="ghost"` + `Mic` icon | Click to start, or hold Space to record |
| Recording | `variant="destructive"` + `MicOff` icon | Click to stop (or release Space) |

### Push-to-Talk (Space Hold)

Handled by `useVoiceInput` hook (global window listener):
- Activates anywhere on page (except native Space targets like buttons/textareas)
- Survives textarea focus (if not in textarea, Space is prevented + held ≥400ms = record)
- Cleans up on unmount

### Long-Press (Mic Button)

Handled by `useLongPress` hook (localized to mic button):
- Touchscreen-specific: ≥400ms hold on the mic button = record
- Also supports mouse long-press (for testing)
- Triggers `navigator.vibrate([100])` if available
- Does NOT prevent the button's `onClick` (fallback for short taps)

---

## Integration Example: Unified Chat + Forge

### Before (2026-10-11)

- ChatComposerField: local to `pages/chat.tsx`, duplicated button logic
- ForgeComposer: separate component, duplicate textarea + voice + button code
- Result: 5 independent implementations (chat/group/peer/tool/plugin) → divergent UX

### After (2026-10-12)

- ComposerBase: single source for all input
- ChatComposerField: unchanged (no regression risk)
- ForgeComposer: uses ComposerBase, removes 134 lines of duplication
- SkillForge: uses ForgeComposer (inherits ComposerBase via composition)
- LayerForge: uses ForgeComposer (inherits ComposerBase via composition)

---

## Testing

### Unit Tests

File: `tests/unit/composer-base.test.tsx`

Tests cover:
- ✅ Textarea value and change events
- ✅ Enter sends, Shift+Enter does not send
- ✅ Respects `submitDisabled` prop
- ✅ `disabled` prop disables all input
- ✅ Mic button rendering and sizing (44px targets)
- ✅ Optional button slots
- ✅ Footer content rendering
- ✅ beforeTextarea/afterTextarea slots
- ✅ Voice input hook integration

### E2E Tests

Existing E2E suites remain unchanged:
- `tests/e2e/chat-composer-autosize.spec.ts` — still passes
- `tests/e2e/chat-attachments.spec.ts` — still passes
- `tests/e2e-live/forge-live/15-composer.spec.ts` — still passes

### No Regressions

ForgeComposer uses ComposerBase without changing ChatComposerField:
- `tests/unit/forge-composer-attachments.test.tsx` — ✅ 8 tests still pass
- All unit tests: 114 files, 1235 tests ✅ pass

---

## Migration Guide

### If You're Adding a New Input (Tool/Plugin/Skill/Layer/Custom)

1. **Create a wrapper component** (like ForgeComposer):
   ```tsx
   import { ComposerBase } from "@/components/ui/composer-base";
   
   export function MyComposer({ ... }) {
     return (
       <ComposerBase
         value={...}
         onChange={...}
         onSubmit={handleSubmit}
         csrf={csrf}
         renderPreButtons={() => {/* custom buttons */}}
         renderPostButton={() => {/* custom button */}}
         footer={/* custom footer */}
       />
     );
   }
   ```

2. **Never duplicate textarea/voice logic** — use ComposerBase and props instead

3. **Test rendering** — verify button sizes, keyboard, voice (use existing test patterns)

### If You Need Custom Mic Button

Rarely needed, but supported:
```tsx
<ComposerBase
  {...props}
  renderMicButton={(recording, onStart, onStop, disabled) => (
    <CustomMicButton
      recording={recording}
      onStart={onStart}
      onStop={onStop}
      disabled={disabled}
    />
  )}
/>
```

---

## Common Pitfalls

### ❌ Don't

- Hardcode textarea styling (use ComposerBase defaults)
- Duplicate voice logic (use `useVoiceInput` hook directly)
- Ignore `submitDisabled` prop (caller is responsible for validation)
- Forget `csrf` token (needed for voice transcription fallback)

### ✅ Do

- Use `renderPreButtons` and `renderPostButton` for custom controls
- Wrap in div with drag-and-drop handlers (if attachments needed)
- Test Enter/Shift+Enter behavior with `fireEvent.keyDown`
- Test touch targets are 44px+ (check className for `sm:h-10`)

---

## Related

- **Chat Composer:** `src/pages/chat.tsx` (lines 1909–2050, uses ComposerBase indirectly)
- **Forge Composer:** `src/components/forge/ForgeComposer.tsx` (direct ComposerBase consumer)
- **Voice Hook:** `src/hooks/use-voice-input.ts` (push-to-talk + fallback MediaRecorder)
- **Autosize Hook:** `src/hooks/use-autosize-textarea.ts` (textarea height auto-grow, field-sizing or per-frame measure)
- **Local draft variant:** `LocalDraftComposer` in `src/components/ui/composer-base.tsx`
- **Console Design Guide:** `docs/claude-ref/console-design-guide-0-10-34.md` (visual identity)

---

**Last Updated:** 2026-10-12  
**Commit:** `d7699ceb1` (feat: align chat and forge input on shared ComposerBase)

---

## Shared composer field and context adapter (CONCEPT-0115)

The chat's text field is no longer private to `pages/chat.tsx`. It lives in
`src/components/chat/ChatComposerField.tsx` (exports `ChatComposerField`,
`ComposerFieldHandle`, `detectEntityHint`) and wraps `ComposerBase` with the slash
palette, the entity hint and the imperative handle (`getValue`/`setValue`/
`closePalette`/`stopRecording`). Typed text stays inside the field, so a keystroke
re-renders the field only.

Defaults reproduce the chat exactly (`textareaId="chat-input"`, the chat table of
slash commands, no outer pill). A surface customises through props:

| Prop | Purpose |
|---|---|
| `extraCommands` + `sessionCommands` | Context commands; `sessionCommands={false}` shows ONLY `extraCommands` (the surface's `/` lines go to another dispatcher) |
| `textareaId` / `textareaTestId` / `micTestId` | Stable ids per surface |
| `pillCardClassName` | Outer pill; the default neutralises it because the host row draws the pill |
| `initialValue` | Seeds the draft (per-context drafts) |

`src/components/chat/composer-adapter.ts` defines the port a surface hands to the shared
UI (`ComposerContextAdapter`: `upload`, `remove?`, `extraCommands`, `sessionCommands`,
`send`, `contextKey`, `limits`) and `checkUploadLimits()` for the client-side pre-check.
The adapter carries no decision: tenant, gates, attachment validity and limits are
re-checked by the server on every request.

Tests: `tests/unit/chat-composer-field.test.tsx`.

### Workflow design chat (CONCEPT-0115 P2)

The workflow editor's design chat (`pages/workflows.tsx::ChatPane`) no longer owns a
textarea or a recorder. It renders `components/workflows/WorkflowComposer.tsx`, which wraps
`ChatComposerField` (Enter sends, Shift+Enter newline, mic button, Space-hold, slash palette).

* **Dictation lands in the field.** The old path sent the transcript immediately; now it is
  inserted and the operator sends it (same as the chat). Web Speech first, MediaRecorder →
  `POST /voice/transcribe` as fallback.
* **Space-hold is scoped** (`pushToTalkFocusOnly` → `useVoiceInput({ pushToTalkTarget })`):
  only while the composer textarea has focus, so Space stays free on the canvas. The chat
  keeps the page-wide hold.
* **Slash commands are a server table**: `GET /v1/console/workflow-design/commands`
  (`routes/workflow_composer.py::DESIGN_COMMANDS`). The design WebSocket parses a bare
  `/word` line itself (`parse_design_command` / `run_design_command`): `/help`, `/phase`,
  `/nodes`, `/voice on|off`. A command spawns no engine, is not charged as a design turn,
  is not written to `<wid>.chat.jsonl` (so it never feeds a later prompt), and writes
  `console.action_performed` with `action=workflow.design_command`, `trigger=<word>`. An unknown
  `/word` is answered "unknown command" and never forwarded. A path-like first token
  (`/home/x`) is ordinary text.
* The draft lives inside the field (same performance contract as `LocalDraftComposer`:
  a keystroke re-renders the field, not the message list). `LocalDraftComposer` itself stays
  unused: the shared field already owns its draft and additionally needs the palette state.

Tests: `tests/unit/workflow-composer.test.tsx`, `core/console/tests/test_workflow_composer.py`
(HTTP + WebSocket against the console router, sandboxed home), `tests/e2e/workflow-composer.spec.ts`.

### Workflow attachments and the gate scope (CONCEPT-0115 P3)

Diagram: `docs/diagrams/workflow-composer-attachment-flow.svg`.

**Store.** `<tenant_home>/workflows/<wid>/attachments/<att_id>/{meta.json,data/<safe_name>}` —
under the same root as the workflow files (and the Art. 17 handler), never in a worker's cwd.
`att_id` is 16 hex. The tenant comes ONLY from the session record (`rec.tenant_id`). A client
names an attachment by id; `load_attachment()` re-checks existence, that the file is a regular
non-symlink file in the attachment's own `data/` dir, and its size + sha256 on EVERY use.

**Routes** (`routes/workflow_composer.py`, router-level session + CSRF-on-mutation):

| Method | Path | Notes |
|---|---|---|
| POST | `/v1/console/workflows/{wid}/attachments` | multipart `files`; 10 files × 50 MiB per request (`attachments_common`), 100 per workflow; all-or-nothing; returns `{id,name,size,mime}` only (no path) |
| GET | `/v1/console/workflows/{wid}/attachments/{att_id}` | `serve_headers()` (inline only for safe types, CSP sandbox otherwise) |
| DELETE | `/v1/console/workflows/{wid}/attachments/{att_id}` | removes the files |

`body_cap.py` lists the upload path (prefix `/v1/console/workflows/` + `/attachments`). Since 2026-10-21 the
gateway host applies the same table to every mounted `/v1/console/*` route (`gateway_cap_for` →
`standalone_cap_for`); before, only the video routes were capped there and an unauthenticated multipart body of
any size was parsed and spooled before the session check.

**Design chat.** The WebSocket `user` frame takes `attachments: [att_id]`. The server resolves them
(`resolve_for_message`): ≤10 per message, UTF-8 text types only (`READABLE_EXTENSIONS` / `text/*`) —
anything else is storable but refused at send time with an in-band `error` frame (415), because an
engine prompt is text and L34 classifies exactly the text it is handed. The text (24 KiB/file,
64 KiB/message) is appended to the turn's prompt, so it passes the gates with the operator's words.
Attachment text is visible to the designer for the turn it is sent in; later turns see the chat
history lines (with attachment names), not the file.

**Gate scope (every spawn prompt).**

| Prompt | Gate channel |
|---|---|
| operator text + attachment text (before the line is stored) | `workflow-design-input` |
| the design chat prompt (history + turn) | `workflow-design` |
| the separately built YAML prompt (12 history lines) — `_generate_awp_yaml` | `workflow-design-yaml` |
| a human's reply resuming a paused run — `POST …/runs/{rid}/resume` | `workflow-resume` (403 `refused`, run stays paused) |

A refused user line is stored as `{"refused": true, "user_sha256", "user_len"}` — never its text —
and `_chat_history_text()` skips such lines, so it cannot reach any later prompt.
`_generate_awp_yaml` without a tenant refuses (never an ungated spawn).

**Runs.** `_stream_run` snapshots the workflow's attachments into
`<wid>/runs/<rid>/attachments/<att_id>/` before any node starts (variant b of the concept: a run is
reproducible). A missing or changed attachment refuses the run (`error: attachment changed`).
Run meta carries `attachments` (id/name/size/mime/sha256_prefix). Node prompts do not read the
snapshot yet — it is the reproducibility record.

**Audit** (`audit.py::workflow_attachment_*`, registered in BOTH `EVENT_SEVERITY` and
`_EVENT_ALLOWLIST`): `workflow.attachment_uploaded` (wid, att_id, size_bytes, closed-shape mime,
sha256_prefix, sid_fingerprint), `workflow.attachment_deleted`, `workflow.attachment_snapshot`,
`workflow.attachment_refused` (WARNING; closed `reason_code`: bad_id, missing, corrupt, changed,
not_readable, too_many). Never a file name, never content.

UI: `components/workflows/WorkflowComposer.tsx` (paperclip, folder where the browser supports it,
chips, drop via the pane's `useFileDrop` → handle `addFiles`), adapter built in
`pages/workflows.tsx::ChatPane`. Tests: `core/console/tests/test_workflow_composer.py`,
`tests/unit/workflow-composer.test.tsx`, `tests/e2e/workflow-composer.spec.ts`.

### Edit window = design chat; run-reply panel (CONCEPT-0115 P4)

Assumption A1 (operator wording "Unterworkflow / Edit-Fenster"; the code has no sub-workflow
concept — `sub_workflow`/`subworkflow`/`child_workflow`: 0 hits): the editing window of a workflow is
the design chat in the editor, so P2/P3 already are its migration. No sub-workflow feature was invented.

The one other free-text input of the editor, the `ask_human` reply panel
(`WorkflowChatPanel`), now uses `WorkflowComposer` too (`attachments={false}`: a reply is text-only).
Send → `POST …/runs/{rid}/resume`, which runs the pre-spawn gates on the reply; a refusal (403
`refused`) or failure shows the server's message and puts the text back into the field
(`WorkflowComposerHandle.setDraft`). The "Optional comment" box of the binary approval bar is a
comment on a decision, not a message, and stays a plain textarea.
Tests: `tests/unit/workflow-chat-panel.test.tsx`.

### Erasure and cleanup (CONCEPT-0115 P5)

Art. 17: `WorkflowChatHandler` (`erasure_handlers.py`, layer `L-workflows`) removes whole attachment
directories — store and per-run snapshots — whose `meta.json` names the subject (`uploaded_by` =
session fingerprint); see `layer-36-erasure.md`. Deleting a workflow removes its `<wid>/` tree
(attachments included); `DELETE …/attachments/{att_id}` removes one.
Cleanup: the design chat's own recorder/transcription code and two-row textarea were removed in P2;
`CreateWorkflowDialog` (name only) and `ScheduleModal` (cron) ask for neither text nor files and stay.
