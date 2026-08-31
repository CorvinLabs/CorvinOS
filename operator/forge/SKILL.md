---
name: forge
description: Forge schema-bound, sandboxed tools at runtime. Paired with the cowork `forge` persona for chat-driven use in the voice-skill bridge. Use when you would otherwise write the same Bash snippet ≥3× with different parameters, when you need precise numerical results over a dataset, or when the user wants reproducible/deterministic output written to a file.
version: 0.2.0
mcp_server: forge
promoted_from: forge
---

# forge — runtime tool factory for Claude Code

You (Claude) can register a new tool at runtime via the `forge` MCP server.
The new tool is callable as `mcp__forge__<name>` from the next `tools/list`
onward, in the current session and in any future session that sees the same
`~/.config/corvin-voice/forge/` workspace.

When this plugin runs inside the voice-skill repo (the normal case), the
cowork `forge` persona auto-routes user phrases like "forge mir ein tool",
"build me a tool that …", or "I need a deterministic tool" onto this MCP
server — the persona ships restrictively (no Bash/Edit/Write) so generated
tools cannot inherit the owner's bypassPermissions privilege.

## When to use forge

Reach for `forge_tool` when **at least one** of these holds:

- You would otherwise write the same Bash/Python snippet ≥3 times with
  different parameters (paths, columns, thresholds)
- You need precise numerical results over a dataset (statistics, regression,
  aggregation, filtering, splitting)
- The user wants the answer **written to a file** (CSV, JSON, PNG)
- The user uses the words *"deterministic"*, *"reproducible"*, *"save as"*,
  *"compute exactly"*, *"audit trail"*
- The result of one computation is used multiple times later — caching
  it in a forged tool gets you reuse for free

## When NOT to use forge

- One-off shell commands (`wc -l`, `git status`) — Bash is fine
- LLM-shaped work (code review, translation, design discussion)
- An existing forged tool already does this — call it instead
- The data lives only in your conversation context — Bash + a heredoc is
  cheaper than codifying a tool

## Calling sequence

1. `mcp__forge__forge_tool({name, description, input_schema, impl, runtime?, meta?})`
2. The server emits `notifications/tools/list_changed` — wait one tick
3. Call `mcp__forge__<name>` with the input you wanted
4. (optional) `mcp__forge__forge_promote({name})` to make it durable

## Schema conventions

The `input_schema` is JSON-Schema. Three forge-specific extensions:

| Annotation         | Meaning                                                    |
|--------------------|------------------------------------------------------------|
| `"x-bind": "ro"`   | Path is read-only-mounted into the sandbox at exec time    |
| `"x-bind": "rw"`   | Path (or its parent) is read-write-mounted into the sandbox|
| `"x-redact": true` | Field is replaced with `<redacted>` in the run manifest    |

Use `"x-redact": true` on api keys, bearer tokens, secrets — the tool still
sees the real value, but it never appears in `runs/<id>/run_manifest.json`.

## Output convention (every forged tool follows this)

A tool prints **one** JSON object to stdout. Either form is accepted:

**Plain form** — server auto-wraps:
```json
{"sum": 42, "summary": "sum=42"}
```

**Full envelope form** — server passes through:
```json
{ "ok": true, "status": 200,
  "data": {"sum": 42, "summary": "sum=42"},
  "error": null,
  "meta": {"deterministic": true, "side_effects": false} }
```

Either way, the MCP `structuredContent` you receive looks like:
```json
{ "ok": true, "data": {...}, "envelope": {...},
  "artifacts": [...], "run_id": "2026-...", "sandbox": "bwrap",
  "duration_s": 0.024 }
```

`artifacts` is the list of files the tool wrote into `_artifacts_dir`,
auto-discovered by the server (the tool can't fake the list).

## meta hints

When you forge a tool, set `meta` to declare its character. The flags
change runtime behaviour:

| Flag                   | Effect                                                          |
|------------------------|-----------------------------------------------------------------|
| `deterministic: true`  | Result cache: identical real inputs return the cached envelope, |
|                        | with `meta.replayed_from = <old_run_id>`. Sandbox = `"cache"`.  |
| `side_effects: false`  | Documentation only (used by promote-to-skill flow); does not    |
|                        | currently change runtime behaviour.                             |

Set `deterministic: true` only when the tool's output is a **pure function**
of its inputs — no clock reads, no RNG without an explicit seed parameter,
no network calls. If unsure, leave it unset.

## Output paths — where artifacts land

Every call has its own `runs/<ts>_<id>/artifacts/` directory. The server
injects its absolute path into the payload as `_artifacts_dir`. Your tool
can write any number of files there:

```python
adir = json.loads(sys.stdin.read())["_artifacts_dir"]
with open(f"{adir}/result.csv", "w") as fh:
    ...
```

The server scans that directory after the tool exits and reports the files
back as `artifacts: [{path, rel, bytes, kind, preview?}, ...]`.

If the **user** named an output path explicitly (`~/Desktop/report.csv`),
pass it as a regular schema field with `"x-bind": "rw"` so the sandbox can
write there too.

## Workspace layout

```
~/.config/corvin-voice/forge/   (override via $FORGE_ROOT)
├── registry.json          tool manifest (one row per forged tool)
├── tools/<name>.{py,sh}   tool implementations on disk
├── skills/<name>/         promoted tools (durable across sessions)
├── runs/<ts>_<id>/        one folder per call:
│   ├── run_manifest.json    input + tool_sha + start time
│   ├── run_completion.json  status + duration + sandbox + artifacts
│   ├── RUN_SUMMARY.md       human-readable digest
│   ├── stdout.json          full envelope the tool returned
│   └── artifacts/           files the tool wrote
├── cache/<key>.json       deterministic-tool cache entries
├── memory/<date>.md       free-form notes (append-only)
└── audit.jsonl            create / delete / promote events
```

## Useful CLI subcommands

(For when you want to inspect from the host shell — Claude doesn't run
these directly, but mention them to the user.)

| Command                                     | What it does                       |
|---------------------------------------------|------------------------------------|
| `forge.py run-list`                         | Recent runs, newest first          |
| `forge.py run-show [--id <id>]`             | RUN_SUMMARY.md of a run            |
| `forge.py cleanup --keep N`                 | Keep last N runs, delete the rest  |
| `forge.py cleanup --keep N --purge-cache`   | …and wipe the deterministic cache  |
| `forge.py sync [--target <dir>]`            | Copy promoted skills to `~/.claude/skills/` |

## Examples

### 1. Group statistics on a CSV the user dropped in

```jsonc
mcp__forge__forge_tool({
  "name": "csv_group_stats",
  "description": "mean + pop-stddev of a numeric column grouped by another",
  "input_schema": {
    "type": "object",
    "required": ["file", "group_by", "value_col"],
    "properties": {
      "file":      {"type": "string", "x-bind": "ro"},
      "group_by":  {"type": "string"},
      "value_col": {"type": "string"}
    }
  },
  "impl": "...python that reads stdin JSON, prints {data: {groups: {...}}}...",
  "meta": {"deterministic": true, "side_effects": false}
})
```

Then call `mcp__forge__csv_group_stats({file, group_by, value_col})`. The
result is in `data`; the full envelope is in `runs/<id>/stdout.json`.
Calling it twice with the same file path → cache hit, ~0ms second time.

### 2. Synthesize a CSV the user can download

```jsonc
mcp__forge__forge_tool({
  "name": "synth_sales_csv",
  "description": "synthesize a deterministic sales CSV from a seed",
  "input_schema": {
    "type": "object",
    "required": ["n_rows", "seed"],
    "properties": {
      "n_rows": {"type": "integer"},
      "seed":   {"type": "integer"}
    }
  },
  "impl": "...writes CSV into open(_artifacts_dir + '/sales.csv', 'w')...",
  "meta": {"deterministic": true}
})
```

The MCP response will include `artifacts[0].path` — that's the absolute
path of the generated CSV. Mention it to the user; they can `cat` / open
it directly.

### 3. Tool with a secret

```jsonc
mcp__forge__forge_tool({
  "name": "fetch_user",
  "description": "fetch a user record",
  "input_schema": {
    "type": "object",
    "required": ["api_key", "user_id"],
    "properties": {
      "api_key": {"type": "string", "x-redact": true},
      "user_id": {"type": "string"}
    }
  },
  "impl": "..."
})
```

`api_key` is sent verbatim to the tool but written as `<redacted>` in
`runs/<id>/run_manifest.json` and never lands in audit logs.

## Security policy — the safety envelope

The operator's `policy.json` (next to `registry.json` in the workspace)
is the safety envelope nothing in `meta` can widen. If absent, strict
built-in defaults apply.

```jsonc
{
  "default_budget":  { "cpu_seconds": 10, "wall_seconds": 30,
                        "output_bytes": 4194304, "artifact_bytes": 67108864 },
  "max_budget":      { "cpu_seconds": 60, "wall_seconds": 300,
                        "output_bytes": 16777216, "artifact_bytes": 268435456 },
  "forbidden_imports":   ["socket", "subprocess", "ctypes", "multiprocessing"],
  "forbidden_tool_names": ["shell.*", "system.*"],
  "allowed_namespaces":   null,                // null = all OK (subject to deny)
  "rate_limit":      { "default_calls_per_minute": 60,
                        "per_tool": { "csv.heavy": 10 } },
  "circuit_breaker": { "enabled": true, "failure_threshold": 5,
                        "reset_timeout": 60, "half_open_max": 2 },
  "network":  { "default": false },
  "audit":    { "hash_chain": true }
}
```

**What gets enforced when:**

| Phase | Check | On violation |
|---|---|---|
| `forge_tool` | `name_allowed(name)` (forbidden globs + namespace) | reject + `policy.namespace_denied` event |
| `forge_tool` | AST static check vs. `forbidden_imports` | reject + `policy.import_denied` event |
| Pre-call | rate limiter token-bucket consume | reject + `rate_limit.exceeded` event |
| Pre-call | circuit breaker `can_execute()` (CLOSED/OPEN/HALF_OPEN) | reject + `circuit_breaker.rejected` event |
| Spawn | `meta.budget` clamped by `max_budget` → applied as rlimits + timeout + output_cap | clamp_info surfaces in `envelope.meta.policy_clamped` |
| Post-run | artifacts dir total bytes vs. `budget.artifact_bytes` | error + `budget.exceeded` |
| Post-run (success) | breaker `record_success()` (resets counter / closes HALF_OPEN) | `circuit_breaker.closed` event on transition |
| Post-run (ToolError) | breaker `record_failure()` (may trip OPEN) | `circuit_breaker.opened` event on transition |

`SchemaError`, `PermissionDenied`, and `TamperError` do **not** count
against the breaker — those are caller- or security-side problems, not
"the tool is broken." Tamper writes its own `tool.tamper_detected` event.

## Audit & integrity

Every security-relevant action appends a structured event to
`<workspace>/audit.jsonl`. With `audit.hash_chain: true` (default), each
record carries `prev_hash` and `hash` (sha256 of `prev_hash || canonical_record`).
The CLI exposes verification:

```bash
python3 forge.py audit-verify
# audit OK  (.../audit.jsonl)            ← rc 0
# audit INTEGRITY VIOLATION ...          ← rc 1, lists offending lines
```

Tampering with any field re-localizes to a `tampered` issue at that line;
deleting a record produces a `broken_chain` at the next record's line.

## Anti-patterns to avoid

- **Don't forge inside a single Bash one-liner's worth of work** — the
  forge step itself costs ~50 tokens of schema. Pay for it only when you'll
  reuse the tool ≥3 times or you need its sandbox/audit guarantees.
- **Don't put one-off paths into the impl source** — pass them as schema
  fields. Otherwise the tool's sha changes with every call and no cache
  ever hits.
- **Don't write outside `_artifacts_dir`** unless an `x-bind: rw` field
  explicitly tells the runner to mount that path. The sandbox will block
  the write and you'll get a confusing exit code.
- **Don't claim `deterministic: true`** if your impl reads the clock or
  calls `random.random()` without a seed parameter — the cache will lock
  in stale answers.
