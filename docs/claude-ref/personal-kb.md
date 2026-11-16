# Personal knowledge base ("Only for me")

Decisions: ADR-2325 (storage), ADR-2326 (owner, keys, erasure), ADR-2327 (capture, bounded self-evolution).
Specification: PLAN-0973. Personal decoder: CONCEPT-0134. Concept: CONCEPT-0133. All in Corvin-Knowledge.
Diagram: [docs/diagrams/personal-kb.svg](../diagrams/personal-kb.svg).

Every member of a tenant can keep a private, encrypted knowledge graph next to the project
knowledge base. Corvin fills it automatically with settings and facts the owner states about
themselves, the owner keeps ideas and personal tasks there, and the personal decoder turns an
idea into a checked plan. It appears in the Knowledge Graph panel and in Tasks under
**Only for me**, and in private bridge chats through `/personal` (alias `/me`).

## What it is not

- Not part of Corvin-Knowledge, not in git, not run by `kb.py`, never in `tasks.db`.
- Not shared: no route, no list and no count shows another owner's data.
- Console chats are not a personal surface (they are listed per tenant); only private bridge chats are.

## Storage (`core/personal_kb/store.py`)

```
<tenant_home>/personal_kb/
  _index.json  _lockouts.json  _pairs.json  .pepper  _revoked/<owner_hash>
  <owner_id>/ owner.json meta.enc manifest.enc objects/<oid>.enc versions/<oid>.<n>.enc policy/versions/<n>.enc .lock
<corvin_home>/secrets/personal_kb/<tenant_id>/<owner_id>.e<epoch>.key   (device key, "use in my chats" mode only)
```

- AES-256-GCM per object; the blob header (format, key epoch) and `object|owner|oid|epoch|version` are authenticated.
- The encrypted manifest detects a deleted object and a single rolled-back object. Such an entry is shown as unreadable;
  the status bar of the space offers **Remove unreadable entries** (`POST /personal/repair`), which also lets an interrupted key change finish.
- Restoring a whole older directory from a backup cannot be detected.
- Two processes (console, bridge adapter) share the directory: per-owner lock, manifest counter reload,
  revocation and key-epoch check inside the lock, `write_failed` audited after an audited intent.
- Quotas at write time: 20 000 objects, 64 MiB plaintext including versions, 60 manual adds per hour.
- Export is built in memory and streamed; the server writes no export file.

## Owners, keys, unlock (`owner.py`, `session.py`, `limits.py`)

| Mode | Console | Bridge chats | Protects against |
|---|---|---|---|
| Use in my chats (`convenient`) | handle + passphrase | device key | a copy of the tenant directory or export, other console users without the passphrase, a stolen session id |
| Console only (`strict`) | handle + passphrase | off | the above, and a passive same-user reader of the disk (no key material on disk) |

Neither mode protects against an actively tampering process of the same OS user, a root operator, malware in the
process, or text already sent to an engine. A bridge-only space (`/personal on`) has no passphrase and no recovery
code. Passphrases need 12 characters, are checked against a common list and an entropy estimate.

- Unlock sets an HttpOnly, SameSite=Strict cookie `corvin_personal` (`Path=/`), never written to disk, bound to the
  console session; idle 30 min, absolute 8 h. Signing out of the console ends every unlock of that session. A locked space answers **423**, a wrong passphrase **403** (never 401,
  which signs the user out of the console).
- No lockout: a failed unlock delays that handle (1, 2, 4 … up to 30 s) and backs off the session; one passphrase check
  per handle runs at a time (a parallel one gets 429); key derivation runs in a dedicated pool of 2 threads with a queue
  of 8 (overflow 429). The passphrase checks of "change passphrase" and "renew the keys" are slowed down and audited the
  same way. Owner creation is limited to 5 per minute.
- Rekey (Settings → Renew the keys) destroys the old passphrase, device and recovery wraps at once and issues a new
  recovery code; the new device key file (one per key epoch) is written before `owner.json` switches and the old one
  is deleted after. The console's unlocked sessions switch to the new key inside the same lock, and the new recovery
  code is returned at once; the re-encryption then runs in the background over a fixed work list in slices of 200 files
  (every file read once, the lock released between slices), and a crash resumes at the next unlock. A version made
  while the sweep is still running is re-sealed under the new key when it is written. Rekey, passphrase change and recovery refuse a space that is being erased.

## Surfaces

**Console** (`/v1/console/personal/*`, `routes/personal_kb.py`): status, create, unlock, recovery, lock, entities
CRUD (Entries → Edit: title, details, earlier versions), restore a version, review, promote an idea to the project knowledge base, task transitions, decoder,
feed, policy (view, edit, rollback, confirm, unfreeze), auto-capture switch, pairing, unbind, export, passphrase,
rekey, erase. Scoped reads: `GET /plugins/corvin-knowledge/graph?scope=personal`,
`GET /task-tracking/items?scope=personal` (answered with `Cache-Control: no-store`, like every personal route).
Promoting an idea (Entries → Edit → Share with the project, after a warning that every member can read it) runs once per
idea at a time (a second click gets 409). UI: Knowledge Graph → **Only for me** (`?space=personal`) with tabs
Entries · Graph · Recently learned · How Corvin learns · Settings; Tasks → **Only for me**.

**Bridges** (`bridge.py`, wired in `adapter.py`): `/personal on|status|remember|idea|unfold|forget|auto|link|unlink|erase`
(alias `/me`; Discord's and Slack's own clients take `/me` for themselves, so use `/personal` there). The L44 classifier runs with no session
persistence, and the inbound announcer never announces a `/personal` text. Search in the console filters in the browser:
its text is not put in a URL, which access logs keep. Slack sends a
message starting with "/" only as a declared slash command: declare `/personal` in the Slack app configuration (the
daemon handles it), or send it with a leading space (` /personal on`). Personal features need the daemon's
`chat_type == "private"`:

| Bridge | Private when |
|---|---|
| discord | a one-to-one DM |
| telegram | chat type `private` |
| slack | `im` (a slash command: channel `directmessage`) |
| teams | `personal` conversation (binds the AAD object id) |
| signal | a 1:1 chat, only with `CORVIN_SIGNAL_DEDICATED_NUMBER=1` |
| whatsapp, email | never |

A `/personal` turn is recorded in the session ledger as a length only (reason `personal_command`), and its inbox
envelope is deleted instead of archived (also when the turn fails: it is never kept as a poison copy). The settings block is injected before the L28.2 slot, framed as data,
bounded to 12 entries / 1 500 characters, and withheld (audited `context_withheld`) when the L34 gate does not allow
`CONFIDENTIAL` for the engine; a turn that goes to another provider (rerouted by ADR-2268, a provider assigned to
`claude_code`, or a Console Models tier row naming OpenRouter / Ollama) never receives the block: a provider
assigned to `claude_code` has no clearance for personal text, so the block is removed from that turn's system prompt. A
turn rerouted by a declared sensitive route (ADR-2268) is judged again for its provider and removed when refused.
A personal block never falls back to the command line: if its system-prompt file cannot be written, the turn is not
started. The block exists in plain
text only in memory and in the spawn's own system-prompt temp file (mode 0600, deleted after the turn, swept after an
hour); every persisted copy of the prompt (the CEL inspector's `cel-briefs/*.assembly.json`) carries its length only. In a chat whose sender owns a space, the legacy L28.2 block is
not injected. Extraction and decoder calls run with `--no-session-persistence` in an empty temporary directory.

**Linking a chat account:** Settings → Link a chat account shows a code; send `/personal link <code>` in a private
chat; confirm the counterpart in the console. A code sent in a group chat is burned, also by a read-only observer (whose
`/personal` text never reaches the observer buffer). Linking never changes a passphrase.
The pending identity (channel, uid, name) is sealed to the owner's key in `_pairs.json`; only its keyed hash stays
readable, for an Art. 17 purge. A key renewal re-seals pending requests under the new key, so a link started before the
renewal still confirms. It expires after 10 minutes. A console-only space cannot be linked. A bridge-only space
stays chat-only (a chat already bound cannot be linked to a second space); `/personal unlink` refuses to remove its
only way in.

## Automatic capture and bounded self-evolution (`capture.py`, `policy.py`, `evolve.py`)

- Owner text only (no attachments, forwards — Telegram `forward_*` fields, a Discord forward reference or snapshot —, code fences, >1 500 characters or ≥3 URLs), a self-statement trigger,
  one extraction call (newest Haiku via `tier_model`) at most every 2 minutes and 40 per day.
- Non-evolvable filters: special categories (GDPR Art. 9), secrets, facts about other people, refused turns, uncertain
  items, unsafe values (URLs, e-mail, paths, shell tokens, tool imperatives), keys outside `AUTO_KEYS`.
- Automatic entries are active immediately, `review = pending`; Recently learned offers Keep, Correct, Forget. A
  forget or a correction of an automatic entry feeds the brake and the tuning wherever it happens (Recently learned,
  Entries → Edit or Forget, `/personal forget`); decoder drafts are not "learned" (no review state). The extraction and
  decoder budgets are checked and booked in one step under the store lock.
  Automatic capture never changes an entry the owner wrote or confirmed (reason `owner_entry`). Expiry runs at every
  open and after every capture.
  Forget remembers a keyed fingerprint so the fact is not learned again (also after a rekey).
- Policy (allowlist with bounds): `confidence_floor`, `daily_cap`, `context_ttl_days`, `type_enabled.*`,
  `extractor_variant` (`v0_strict` only literal self-statements, `v1`, `v2_broad` clearly implied preferences too; it
  changes the extraction prompt), `decoder_adversarial_rounds`, `decoder_task_cap`, `decoder_template_variant`. Tightening
  applies on its own; loosening waits for the owner; the brake (>50 % of the last 20 automatic entries forgotten or
  corrected) freezes tuning and tightens componentwise. Learning-system register: **L20** (state A).
- The legacy L28.2 user model of a linked private chat is imported once as pending `note` entries (30-day expiry) and
  deleted; items naming or referring to another person, hedged items, special categories, secrets and unsafe values are
  dropped; its distillation stops for that chat. Other legacy files are untouched.

## Personal decoder (`decoder.py`)

Seed → Shape → Choices → Plan → Done, one step per call (`/personal unfold <IDEA-0001>` or the Develop button).
Each step: find first (own entries, quoted as data) → generation (newest Sonnet) → deterministic gates → one repair
round with the gate failures as feedback → up to `decoder_adversarial_rounds` critic passes (at most 3 material
objections each, kept in the idea's decoder state) → loss = failed checks / checks of that stage (duplicates from
"find first" count as failed checks; contradiction detection is not built). Dismissing an objection recomputes the
loss of its own stage only. Redoing a step replaces only the drafts nobody touched: a task the owner moved on or a note
they edited stays as a standalone entry. A blocked or cancelled plan task offers **Rework the idea** in Tasks (the reason
picks the stage: unclear goal → Shape, missing decision → Choices, missing step or too big → Plan); at
most 5 revisions per idea; 12 decoder calls per owner per day. One step per idea runs at a time; a step whose idea
changed meanwhile (an edit in another tab, the other process) is discarded with its drafts instead of overwriting it. Measurements: `tests/personal_kb/decoder_eval/`
(`results.json` replay corpus, `live_results.json` real engine, `bench_results.json` find-first cache).

## Erasure (`erasure.py`)

- Owner: Settings → Erase my space (passphrase), or `/personal erase` for a bridge-only space.
- Operator: `python -m core.personal_kb.cli erase --tenant <tid> --handle <handle>` (no key needed; `count` prints the
  number of spaces only).
- Art. 17: `PersonalKbHandler` (`L-personal-kb`) in the default handler chain accepts a bound uid (bare or
  `channel:uid`), never an owner id, handle or `pkb:` value. Any console member who can file an Art. 17 request by uid
  can erase that space (stated residual, the same as for every other layer).
- Order: audit start → revocation sentinel (written first; every writer checks it inside the lock) → `erasing` flag →
  wraps and device keys destroyed → owner directory, handle counter, pair codes, index entries removed → audit end →
  sentinel marked done. An unfinished run is completed by the `_revoked/` scan that every `store.open`, unlock and
  Art. 17 request runs, even when the directory is already gone.
- The space is erased first (once, with its real entry count); "Also delete my chat history" then runs one orchestrator
  request per bound id. An id the orchestrator cannot name (`+49…`) is skipped, and so is every Teams binding: a Teams
  space is bound to the AAD object id while a Teams chat history is keyed by the sender's UPN, so it needs a separate
  operator request. `chat_histories` counts only requests every layer completed.
- Hosts: the console and the gateway finish interrupted erasures of every tenant at start, in a background thread.

## Audit

32 events (`personal_kb.*`, `erasure.personal_kb_deleted`), registered in `security_events.py` and mirrored in
`core/personal_kb/audit.py`. Records carry `owner_hash` (sha256 prefix of the random owner id), ids, counts and
closed reason codes; never text, handles or bridge uids.

## File hub and other readers

The File Hub never serves or writes `personal_kb/` or `secrets/` (a directory component anywhere in the path), and it
hides the per-turn system-prompt temp file (`.corvin-sysprompt-*`). Extraction and decoder calls run only on the native
endpoint: when a provider is assigned to `claude_code` or a Console tier row names another one, the call is withheld
(extraction is skipped, a decoder step reports that nothing was changed). A block removed before the spawn is recorded
as `personal_kb.context_withheld` with `entry_count` 0.

## Path gate

`path_gate.py` refuses agent **writes** to `<home>/tenants/<tid>/personal_kb/**` and `<home>/secrets/personal_kb/**`.
It does not see reads (its hook matcher omits Read, Grep and Glob); it is an accident guard, not a confidentiality boundary.

## Tests

| Suite | What |
|---|---|
| `tests/personal_kb/` | crypto, model, owner, store, two-process writes, rekey, capture, policy, brake, render, decoder, bridge, pairing, erasure, path gate, daemon contracts, decoder LDD/LLD measurements |
| `core/console/tests/test_personal_kb_e2e.py` | the routes over the real console app with real sessions and CSRF |
| `corvin_operator/bridges/shared/test_personal_kb_adapter.py` | `/personal`, the prompt block, the ledger, and capture through `process_one` |
| `tests/security/test_erasure_coverage_guard.py` | the store in `COVERED_DIRS`, planted through the real writer |
| `web-next/tests/unit/personal-kb.test.ts` | UI helpers |
