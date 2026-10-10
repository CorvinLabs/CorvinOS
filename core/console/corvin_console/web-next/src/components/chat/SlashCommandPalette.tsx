/**
 * Slash-command autocomplete — shared across every composer (session chat
 * `pages/chat.tsx::ChatPane`, direct A2A thread `PeerConversation`, group chat
 * `GroupConversation`) so the `/`-triggered dropdown is the same
 * component, with the same keyboard contract, everywhere a message can be
 * typed. Peer and group composers show only their server-provided
 * table (`sessionCommands: false`).
 *
 * Enter and Tab both INSERT the selected command into the composer (with its
 * argument placeholder pre-selected so typing replaces it) — neither sends or
 * executes anything. The palette only ever edits the composer's text; what
 * happens when that text is later sent is entirely up to the composer.
 */
import * as React from "react";
import { cn } from "@/lib/utils";

export interface SlashCommand {
  readonly cmd: string;
  readonly args: string;
  readonly desc: string;
}

// Peer-thread-only commands (direct A2A composer) are NOT a client-side
// constant — ADR-2235 Alternatives (e) rejected that explicitly: a command
// not parsed server-side can't be audited or refused fail-closed, and a
// client copy of the grammar is exactly how the console's two session
// dispatchers (slash_commands.py vs. the bridge) drifted. PeerConversation
// fetches its table from `GET /v1/console/peer-thread/commands`
// (`getPeerThreadCommands` in `lib/api/a2a.ts`) and passes it in as
// `extraCommands` below; every `/` line it sends goes through
// `sendPeerThreadCommand`, never `sendA2AFeedMessage`.

export const SLASH_COMMANDS = [
  // ── Help ──
  { cmd: "/help",              args: "",               desc: "List available console commands" },
  // ── Session management ──
  { cmd: "/stop",             args: "",                desc: "Abort the running task (aliases: /cancel, /halt)" },
  { cmd: "/new",              args: "",                desc: "Start a new session" },
  { cmd: "/clear",            args: "",                desc: "Clear conversation history" },
  { cmd: "/reset",            args: "",                desc: "Reset session and history" },
  // ── Agentic compute (ADR-0214) ──
  { cmd: "/use-engine tiered_delegation", args: "<task>", desc: "TDE: parallel three-gate delegation — needs Settings → Worker Engine = tde" },
  { cmd: "/use-engine acs",   args: "<task>",          desc: "Force ACS manager/worker fan-out" },
  { cmd: "/use-engine claude_code", args: "<task>",    desc: "Force the sequential OS engine" },
  { cmd: "/delegate",         args: "<task>",          desc: "Force ACS delegation for this turn" },
  { cmd: "/engine-auto",      args: "<task>",          desc: "Explicit auto-detection (normal behavior)" },
  { cmd: "/debug-engine",     args: "<task>",          desc: "Show engine-selection signals for this turn" },
  // ── CCC — entity creation (ADR-0168 M6) ──
  { cmd: "/create workflow",  args: '[name="…"] [schedule="*/5 * * * *"]', desc: "CCC: create a workflow" },
  { cmd: "/create task",      args: '[name="…"]',      desc: "CCC: create an ATS task" },
  { cmd: "/create tool",      args: '[name="…"]',      desc: "CCC: forge a new tool" },
  { cmd: "/create skill",     args: '[name="…"]',      desc: "CCC: create a skill" },
  { cmd: "/erase",            args: "user uid=<id>",   desc: "CCC: GDPR Art. 17 erasure request" },
  { cmd: "/audit",            args: "[last <n>]",      desc: "Show recent audit events" },
  // ── Config ──
  { cmd: "/engine",           args: "<name>",          desc: "Switch engine: claude_code · codex · opencode · copilot" },
  { cmd: "/persona",          args: "<name>",          desc: "Pin a persona for this chat" },
  { cmd: "/forget",           args: "",                desc: "Delete your memory (GDPR Art. 17)" },
  { cmd: "/quota",            args: "",                desc: "Check your message quota" },
  { cmd: "/share",            args: "",                desc: "Grant single-session consent (this console session)" },
  { cmd: "/go",               args: "[steering]",      desc: "Execute a pending proposal" },
  { cmd: "/propose",          args: "<text>",          desc: "Queue a proposal" },
  { cmd: "/btw",              args: "<text>",          desc: "Inject a note into an active stream" },
  { cmd: "/skills",           args: "",                desc: "List active skills" },
  { cmd: "/memory",           args: "",                desc: "Show memory summary" },
  { cmd: "/whoami",           args: "",                desc: "Show your identity and role" },
  { cmd: "/role",             args: "",                desc: "Show your current role" },
  { cmd: "/dialectic-on",     args: "",                desc: "Enable dialectic reasoning" },
  { cmd: "/dialectic-off",    args: "",                desc: "Disable dialectic reasoning" },
  // ── Plugin Builder (ADR-0253) ──
  { cmd: "/plugin-builder",   args: "[status|cancel]", desc: "Interview-driven plugin design (Idea/Architecture/ADR/Plan + scaffold)" },
] as const;

function matchSlashCommands(input: string, commands: readonly SlashCommand[]): SlashCommand[] {
  const q = input.toLowerCase();
  return commands.filter(({ cmd }) => {
    if (cmd.startsWith(q)) return true;
    // Multi-word match: each typed token must prefix the corresponding command token.
    const qParts = q.split(/\s+/);
    const cParts = cmd.split(/\s+/);
    return qParts.every((tok, i) => i < cParts.length && cParts[i].startsWith(tok));
  });
}

/**
 * Where to insert a chosen command into a composer's text, and which range
 * (if any) to pre-select so typing replaces the argument placeholder in one
 * keystroke rather than requiring the operator to delete it first.
 */
export function buildCommandInsertion(match: SlashCommand): { text: string; selStart: number; selEnd: number } {
  if (!match.args) {
    const text = `${match.cmd} `;
    return { text, selStart: text.length, selEnd: text.length };
  }
  const text = `${match.cmd} ${match.args}`;
  return { text, selStart: match.cmd.length + 1, selEnd: text.length };
}

/**
 * Applies `buildCommandInsertion` to a real composer: writes the new value
 * through the caller's own state setter, then — once React has re-rendered
 * the textarea with that value — selects the argument placeholder. The
 * selection step is deferred a frame because setting it against the OLD DOM
 * value (synchronously, before React commits the new one) would select the
 * wrong range or silently no-op on a shorter previous value.
 */
export function applyCommandInsertion(
  match: SlashCommand,
  setValue: (v: string) => void,
  textareaRef: React.RefObject<HTMLTextAreaElement | null>,
): void {
  const { text, selStart, selEnd } = buildCommandInsertion(match);
  setValue(text);
  requestAnimationFrame(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.focus();
    el.setSelectionRange(selStart, selEnd);
  });
}

/**
 * Owns open/selected state and the matched list for one composer. The
 * caller wires its own `value` (the textarea content) in on every change
 * and its own apply-command callback (how it writes the chosen command
 * back into its own text state) — this hook never touches the composer's
 * text itself, it only decides what the palette should show.
 *
 * `extraCommands` merges in composer-specific entries on top of the shared
 * `SLASH_COMMANDS` — pass a module-level constant (or a memoised array), not
 * an inline one, so it stays referentially stable across renders.
 *
 * `sessionCommands: false` is for a composer whose `/` lines go to a
 * DIFFERENT dispatcher than the session chat's (the peer thread): the session
 * table (/help, /new, /clear, /use-engine …) would be offered there and then
 * refused by that dispatcher ("unknown command — not sent"), and its `/stop`
 * would collide with the peer thread's own `/stop`. Such a composer shows ONLY
 * its server-provided table (ADR-2235).
 */
export function useSlashCommandPalette(
  value: string,
  extraCommands: readonly SlashCommand[] = [],
  { sessionCommands = true }: { sessionCommands?: boolean } = {},
) {
  const [open, setOpen] = React.useState(false);
  const [selected, setSelected] = React.useState(0);
  const commands = React.useMemo<readonly SlashCommand[]>(() => {
    if (!sessionCommands) return extraCommands;
    return extraCommands.length ? [...SLASH_COMMANDS, ...extraCommands] : SLASH_COMMANDS;
  }, [extraCommands, sessionCommands]);

  const onChange = React.useCallback((next: string) => {
    if (next.startsWith("/") && !next.includes("\n")) {
      setOpen(true);
      setSelected(0);
    } else {
      setOpen(false);
    }
  }, []);

  const matches = React.useMemo(
    () => (open && value.startsWith("/") ? matchSlashCommands(value, commands) : []),
    [open, value, commands],
  );
  const clampedSelected = Math.min(selected, Math.max(0, matches.length - 1));

  const close = React.useCallback(() => setOpen(false), []);

  const onKeyDown = React.useCallback(
    (e: React.KeyboardEvent, applyCommand: (match: SlashCommand) => void): boolean => {
      if (!open || matches.length === 0) return false;
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setSelected((s) => (s - 1 + matches.length) % matches.length);
        return true;
      }
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSelected((s) => (s + 1) % matches.length);
        return true;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        setOpen(false);
        return true;
      }
      // Enter and Tab both INSERT the highlighted command — never send or
      // execute it. Sending/executing stays the composer's own Enter
      // handler, reached only once this hook has no open match (below).
      if (e.key === "Tab" || (e.key === "Enter" && !e.nativeEvent.isComposing)) {
        e.preventDefault();
        const m = matches[clampedSelected];
        if (m) applyCommand(m);
        setOpen(false);
        return true;
      }
      return false;
    },
    [open, matches, clampedSelected],
  );

  return { open, selected: clampedSelected, matches, onChange, onKeyDown, close };
}

export function CommandPalette({
  matches,
  selected,
  onSelect,
}: {
  matches: readonly SlashCommand[];
  selected: number;
  onSelect: (match: SlashCommand) => void;
}) {
  if (matches.length === 0) return null;
  return (
    <div data-testid="slash-palette" className="absolute bottom-full left-0 right-0 z-50 mb-1 overflow-hidden rounded-lg border border-border bg-popover shadow-xl">
      <div className="max-h-72 overflow-y-auto py-1">
        {matches.map((item, i) => (
          <button
            key={item.cmd}
            onMouseDown={(e) => {
              e.preventDefault(); // prevent textarea blur before we insert
              onSelect(item);
            }}
            className={cn(
              "flex w-full items-baseline gap-3 px-3 py-2 text-left transition-colors",
              i === selected
                ? "bg-accent/15 text-foreground"
                : "text-muted-foreground hover:bg-muted hover:text-foreground",
            )}
          >
            <span className="w-36 shrink-0 font-mono text-[13px] font-medium text-foreground">
              {item.cmd}
            </span>
            {item.args && (
              <span className="shrink-0 font-mono text-[11px] text-muted-foreground">
                {item.args}
              </span>
            )}
            <span className="min-w-0 truncate text-xs text-muted-foreground">
              {item.desc}
            </span>
          </button>
        ))}
      </div>
      <div className="border-t border-border/60 px-3 py-1.5 text-[10px] text-muted-foreground">
        <kbd className="rounded bg-muted px-1 font-mono">↑↓</kbd> navigate ·{" "}
        <kbd className="rounded bg-muted px-1 font-mono">Enter</kbd>/{" "}
        <kbd className="rounded bg-muted px-1 font-mono">Tab</kbd> insert ·{" "}
        <kbd className="rounded bg-muted px-1 font-mono">Esc</kbd> close
      </div>
    </div>
  );
}
