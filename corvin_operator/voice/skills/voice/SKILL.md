---
name: voice
description: Voice mode for Claude Code — automatically reads assistant replies aloud via TTS, with German/English auto-detection and optional summarization for long responses. Trigger when the user mentions voice, vorlesen, sprachausgabe, TTS, "speak", "say it out loud", or asks about /voice-* commands.
---

# Claude Voice — Read-Aloud TTS Mode

Dieses Skill steuert das Vorlese-Verhalten von Claude Code. Es ergänzt
das eingebaute STT (Spracheingabe via `/voice` und Hold-Space) um
**Sprachausgabe** der Assistant-Antworten.

## Was es macht

- Stop-Hook: Nach jeder Claude-Antwort wird die letzte Textnachricht
  automatisch vorgelesen (wenn `enabled=true`).
- Sprache wird automatisch erkannt (Deutsch / Englisch) oder fest
  per Config gesetzt.
- Lange Antworten (> Threshold) werden vor dem Vorlesen via Anthropic
  Haiku zusammengefasst, damit das Vorlesen erträglich kurz bleibt.
  Steuerbar pro Antwort (Phrasen wie "lies vollständig vor" /
  "fass zusammen") und global (`/voice-mode`).
- Engine-Auswahl ist auto-detect: bevorzugt OpenAI TTS (wenn
  `OPENAI_API_KEY` gesetzt), sonst Piper (lokal) oder espeak-ng als
  letzter Fallback.

## Slash-Commands

- `/voice-on` — automatisches Vorlesen aktivieren
- `/voice-off` — automatisches Vorlesen deaktivieren
- `/voice-status` — aktueller Status, Engine, Config
- `/voice-test [de|en|both]` — Audio-Pipeline testen
- `/voice-speak <text>` — Text manuell vorlesen
- `/voice-lang auto|de|en` — Sprach-Modus setzen
- `/voice-mode auto|full|summary` — Vorlese-Modus (Default-Verhalten)
- `/voice-full` — Shortcut für `/voice-mode full`
- `/voice-summary` — Shortcut für `/voice-mode summary`
- `/voice-auto` — Shortcut für `/voice-mode auto`
- `/voice-config show|path|edit` — Konfiguration anzeigen / Pfad

### Per-Antwort-Override (kein Setup)

Egal welcher Default-Modus aktiv ist, Phrasen in **deiner Frage** kippen
einmalig den Modus für genau diese Antwort:

- `full` (kein Summarize) — "lies (mir) das **vollständig | komplett |
  wörtlich | im Ganzen** vor", "voll vorlesen", "ohne Kürzung", "nicht
  zusammenfassen"; EN: "read it in full / verbatim", "no summary".
- `summary` (immer zusammenfassen) — "fass das zusammen", "Kurzfassung",
  "in kurz", "in Kürze"; EN: "summarize", "short version", "TL;DR".

`full` schlägt `summary`, wenn beides matcht.

## Wann dieses Skill aktiviert wird

Wenn der Nutzer eines der folgenden Themen erwähnt:
- "voice mode", "vorlesen", "sprachausgabe", "TTS", "speak"
- "die Antwort soll vorgelesen werden"
- "sag mir das auf Deutsch / Englisch"
- Fragen zu Audio-Setup, OpenAI TTS, Piper, ElevenLabs

Bei Fragen zu **Spracheingabe** (STT, Mikrofon, Diktat): Hinweis, dass
Claude Code dafür das eingebaute `/voice` (Hold-Space-Recording) hat.

## Konfiguration

Liegt in `~/.config/corvin-voice/config.json`. Wichtige Felder:

| Feld | Default | Beschreibung |
|---|---|---|
| `enabled` | `true` | Auto-TTS an/aus |
| `engine` | `auto` | `auto`, `openai`, `piper`, `espeak-ng`, `say` |
| `lang_mode` | `auto` | `auto` (detect), `de`, `en` |
| `lang_default` | `de` | Fallback wenn auto-detect unsicher |
| `summarize` | `true` | Lange Antworten kürzen lassen |
| `summarize_threshold` | `2000` | Zeichen-Schwelle für Summarize |
| `summarize_max_chars` | `10000` | Ziel-Länge der Zusammenfassung (Soft-Limit) |
| `voice_mode` | `auto` | `auto` (threshold-basiert), `full` (immer komplett), `summary` (immer zusammenfassen) |
| `voice_de` / `voice_en` | *(unset → female)* | OpenAI-Voice-Name; leer ⇒ Default ist eine weibliche Stimme (`nova` für de, sonst `shimmer`). Zum Ändern z.B. `echo`/`onyx`/`fable` (männlich) setzen. |
| `speed` | `1.0` | Sprechgeschwindigkeit (OpenAI) |
| `openai_model` | `tts-1` | `tts-1` oder `tts-1-hd` |
| `anthropic_model` | `claude-haiku-4-5` | Modell für Summarizer |


### Self-forging — jede Persona kann ihre eigenen Tools generieren

Phase I: jede `zero_config: true` Persona (coder, research, browser,
inbox, homeassistant, assistant) inheritet automatisch
`mcp__forge__forge_tool` + `mcp__forge__forge_promote` und kriegt einen
**eigenen FORGE_ROOT-Namespace**:

    ~/.config/corvin-voice/forge/personas/<persona>/

Audit-Events tragen `details.persona` — `voice-audit tail` zeigt wer
was geforgte hat. Cross-Persona-Sharing via `forge_promote --target shared`.
Opt-out: `"forge_enabled": false` in der persona JSON.

### Workspace-Scopes — wo geforgte Tools landen

Geforgte Tools haben jetzt vier Lebenszeit-Scopes mit automatischer
Detection. Lookup-Reihenfolge: `task ▸ session ▸ project ▸ user`
(höherer Scope shadowed niedrigeren).

| Scope | Pfad | Default wenn... | Auto-Cleanup |
|---|---|---|---|
| `task` | `/tmp/.corvinOS/tasks/<id>/forge/` | nur explizit (`--scope task`) | `forge_cleanup tasks` (TTL 1h) |
| `session` | `~/.corvinOS/sessions/<channel-id>/forge/` | aus Bridge gestartet | `forge_cleanup sessions` (TTL 30d) |
| `project` | `<repo>/.corvinOS/forge/` | cwd in git-repo | nie auto |
| `user` | `~/.corvinOS/global/forge/` | sonst | nie auto |

Detection-Reihenfolge:
1. `CORVIN_FORCE_SCOPE` env (explizit, z.B. tests)
2. `CORVIN_DEFAULT_SCOPE` env (Persona-Default, vom Resolver gesetzt)
3. `CORVIN_CHANNEL_ID` gesetzt → session
4. cwd in git-repo → project
5. fallback → user

Persona-Defaults (in `personas/<name>.json` als `forge_default_scope`):

| Persona | Default | Begründung |
|---|---|---|
| coder | project | Coding-Projekte sind kontextual klar |
| research / browser / assistant | session | kurzlebige Bridge-Konversation |
| inbox / homeassistant | user | global verwendet über mehrere Sessions |
| forge | (kein Default) | folgt Caller-Kontext |

Override pro Aufruf: `forge_tool({"name":..., "scope":"task|session|project|user"})`.
Promotion: `forge_promote({"name":..., "to":"session|project|user"})` —
verschiebt Tool zwischen Scopes.

### Daten-Layout — `<repo>/.corvinOS/`

Alle vom Plugin erzeugten User-Daten leben unter einem einzigen Root.
Default ist `<repo>/.corvinOS/`, override via `CORVIN_HOME` env.

    .corvinOS/
    ├── voice/      config.json, sessions/, vault/, memory/, schedule.json, earcons/
    ├── cowork/     personas/, mcp-cache/
    ├── global/     forge/  (audit.jsonl + user-scope tools)
    ├── sessions/   <bridge>:<chat-id>/forge/  (session-scope tools)
    └── forge/      tools/  (project-scope tools — wenn cwd in git-repo)

Audit-Chain ist *unified* — alle Bridge- und Forge-Events landen in
`<root>/global/forge/audit.jsonl`, nicht pro Scope. Damit ist
`voice-audit verify` weiter ein einziger Aufruf für alles.

## Voraussetzungen

- `python3` mit `openai`-SDK *oder* `piper`-Binary *oder* `espeak-ng`
- Audio-Player: `aplay` (ALSA), `paplay`, `ffplay`, `mpv`, `play` oder `mpg123`
- Für OpenAI TTS: `OPENAI_API_KEY` als Env-Variable
- Für Summarizer: `ANTHROPIC_API_KEY` (sonst Fallback auf naive
  Truncation — funktioniert weiter, einfach weniger elegant)

## Runtime tool generation (forge plugin)

Optional sister plugin `operator/forge/` exposes a runtime tool factory:
the `forge` persona (in `operator/cowork/personas/forge.json`) lets a
chat register schema-bound Python/bash tools at runtime via the
`mcp__forge__forge_tool` MCP call. Generated tools execute in a bwrap
sandbox with no network, fresh `/tmp`, and POSIX rlimits — they never
inherit the owner's `--dangerously-skip-permissions` privileges,
because the forge persona itself runs with `permission_mode: "default"`
and explicitly disallows `Bash`, `Edit`, `Write`, `MultiEdit`.

Two ways to land in the forge persona:
- explicit pin: `chat_profiles[<chat>].persona = "forge"` in the
  channel's `settings.json`
- auto-routing on a routing-anchor phrase like "forge mir ein tool",
  "build me a tool that…", "I need a deterministic tool" — the cowork
  router picks the persona on a high-confidence match.

### Per-persona allowlist

A persona JSON may carry `allowed_forged_tools: ["csv.*", "stats.median"]`
to gate which forged tools that persona is permitted to *consume* (the
forge persona itself can always forge new ones; consumer personas only
see what they're allowed). The cowork resolver expands the list into
the `FORGE_ALLOWED_TOOLS` env var via the `{{ALLOWED_FORGED_TOOLS}}`
template variable, and the forge MCP server filters both `tools/list`
and `tools/call` against it. **Glob semantics are `fnmatch`** — `csv.*`
matches `csv.count` and `csv.median` but **NOT** `csv_count` (the dot is
literal in fnmatch, not a regex wildcard). Absence of the field means
no restriction (legacy behaviour).

A blocked call is rejected with `acl.persona_denied` in the audit log,
not silently filtered.

### Audit log

Bridge events (login, whitelist deny, PIN failure, channel rate-limit,
message received, persona routed, tool use) plus all forge events land
in a single sha256 hash-chained file at
`~/.config/corvin-voice/forge/audit.jsonl` — override path via the
`VOICE_AUDIT_PATH` env var, or move the whole forge workspace via
`FORGE_ROOT`. Each record carries `prev_hash` and `hash`; tampering
with any field breaks the chain at that record's line. Cross-process
writes (voice adapter + forge MCP server are separate processes) are
serialized via filesystem `flock`, so concurrent events stay linked.

CLI (the script `operator/voice/scripts/voice_audit.py` is the
`voice-audit` command — symlink it into `$PATH` for the short form):

- `voice-audit verify` (or `python3 operator/voice/scripts/voice_audit.py verify`)
  — exit 0 on intact chain, exit 1 on integrity violation (lines +
  issue type printed on stderr), exit 2 on IO error.
- `voice-audit tail --limit 50` (or
  `python3 operator/voice/scripts/voice_audit.py tail --limit 50`) —
  prints the most recent N entries with timestamp, severity,
  event_type, channel, chat_key, user, persona.

When the forge plugin isn't installed, `bridges/shared/audit.py` is a
silent no-op — the bridge still runs, just without audit persistence.

### Workflow policy

The operator's safety envelope lives in `~/.config/corvin-voice/forge/policy.json`
(override the workspace via `FORGE_ROOT`). The file controls:

| Field                    | What                                                  |
|--------------------------|-------------------------------------------------------|
| `default_budget`         | Per-call default (CPU/wall/output/artifact bytes)     |
| `max_budget`             | Per-call hard ceiling — `meta.budget` is clamped here |
| `forbidden_imports`      | AST-checked at forge-time (default: socket, subprocess, ctypes, multiprocessing) |
| `forbidden_tool_names`   | Glob deny-list at forge-time AND call-time            |
| `allowed_namespaces`     | Optional positive allowlist for tool name prefixes    |
| `rate_limit`             | Per-tool token-bucket (default + per-tool overrides)  |
| `circuit_breaker`        | CLOSED/OPEN/HALF_OPEN thresholds + reset timeout      |
| `audit.hash_chain`       | Whether new audit records carry `prev_hash`/`hash`    |

Edits to `policy.json` **hot-reload** on the next `tools/call` —
operator can tighten the envelope without restart, matching the rest
of the voice repo's reload convention. Live circuit-breaker thresholds
and rate-limit capacities are updated in place; in-flight failure
counters are preserved. A malformed edit is rejected with a
`policy.reload_failed` event and the previous policy stays in effect.
