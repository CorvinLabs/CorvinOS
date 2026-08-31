# User Management Reference (Layers 18–21, 28)

> Load when working on roles, disclosure, quota, proposals, recall, or user-model code.
> Quick summary in CLAUDE.md § Layer 18 / 28.

## Layer 18 — Capability-bundle role system (delegated authority)

The bridge whitelist is binary (owner-or-deny). The `read_only` role
(Layer 16 Phase 2) split that into "in the chat" vs "may trigger".
Layer 18 is the next step: it adds **capability bundles** that the
owner — and admins delegated by the owner — can grant to specific
(chat, uid) pairs, with a TTL and a full delegation chain in the
audit log.

### Bundles and capabilities

Four canonical bundles, intrinsic ordering high → low:

| Bundle | Trigger | Tools | Pin Persona | Delegate | Audit chat | Notes |
|---|---|---|---|---|---|---|
| `owner` | ✓ | all | ✓ | admin/member/observer | ✓ | implicit from channel whitelist |
| `admin` | ✓ | all | ✓ | member/observer | ✓ | cannot delegate `admin` (kerze rule) |
| `member` | ✓ | basic | own turn only | — | self only | quotaed in Phase 3 |
| `observer` | — | — | — | — | self only | pairs with consent.granted |

`owner` cannot be granted via this module — owners are intrinsic to the
channel `whitelist` in `bridges/<channel>/settings.json`. The
`/grant owner` path returns `owner-not-grantable` for symmetry.

### Storage

Single JSON file per (channel, chat) at
`<corvin_home>/global/roles/<safe_channel>__<safe_chat>.json`. Mtime
hot-reload via lazy-prune-on-read; concurrent writes use a `.lock`
sidecar (POSIX flock). Mirror of the `consent.py` /
`auth_elevation.py` pattern.

### Audit chain

Every `grant.issued` / `grant.denied` / `grant.revoked` /
`grant.revoke_denied` / `grant.left` / `grant.expired` lands in the
unified hash chain at `<corvin_home>/global/forge/audit.jsonl`.
`voice-audit verify` covers the new event-types automatically.

### Slash-commands (`bridges/shared/js/in_chat_commands.js`)

```
/grant <uid> <bundle> [<ttl>] [<reason>]   owner/admin only
/revoke <uid>                              owner/admin only
/role                                      caller's own role + capabilities
/role <uid>                                another user's role (owner/admin)
/roles                                     full chat overview (owner/admin)
/leave                                     drop your own granted role
```

The dispatcher passes `ctx.uid` (set by every daemon from the bridge
protocol). Identity is platform-bound — the caller cannot grant on
someone else's behalf, mirroring the consent-gate's identity rule.

`/role` was historically an alias for `/whoami`; with Layer 18 it owns
the role-status meaning. `/whoami` and `/wer` keep their persona-pinning
meaning (no UX break for users who only used `/whoami`).

### Authority matrix

`grant`:
- owner → admin/member/observer (`delegate_admin`, `delegate_member`,
  `delegate_observer`)
- admin → member/observer (`delegate_member`, `delegate_observer`)
- member/observer → nothing (no `delegate_*` capability)
- nobody can grant `owner` (not in any `delegate_*`)
- self-grant always rejected (`self-grant`)

`revoke`:
- owner → any non-owner (`revoke_any`)
- admin → member, observer only (`revoke_subordinate`); cannot revoke
  another admin or owner — that's the `cannot-revoke-peer` rejection
- member/observer → no one
- owner → owner is `owner-not-revocable`; the channel whitelist is the
  only way to demote an owner

`leave`:
- owner → `owner-cannot-leave` (whitelist edit required)
- everyone else → drops their own entry

### TTL contract

Default grant TTL is 7 days (`DEFAULT_TTL_S`). Min/max clamps are 60s
and 30 days, mirroring the consent-gate clamps. The `/grant` UI
defaults to `7d` when no TTL token is given. Indefinite grants are
explicitly opt-in via the keyword `never` / `forever` / `inf` /
`infinite` / `indefinite` — the parser recognises all five forms.

Lazy-prune happens on every read; the prune is persisted (the file
shrinks) and emits one `grant.expired` event per dropped entry into
the audit chain so an operator can see what fell off when.

### Daemon contract

All four daemons (`telegram`, `discord`, `slack`, `whatsapp`) need to
pass `ctx.uid` to `dispatch()` so `/grant` / `/revoke` / `/leave` can
identity-bind the caller. Until each daemon is updated, the
`_callerRole` helper falls back to `'owner' if ctx.isOwner else
'unknown'` — the owner code-path keeps working, but admin delegation
is gated on the daemon update. (This update is part of Phase 4 — the
live walkthrough on Discord.)

### What you, as Claude Code, must NOT do

- Don't write `owner` into the roles store. Owner is intrinsic via
  the channel whitelist — there's no fall-through path that should
  set `bundle: "owner"` in the JSON. The CLI rejects it; the module
  rejects it; tests verify both.
- Don't add an alias for `admin` that grants `admin`. The "kerze
  nicht heißer als die flamme" rule is the structural reason this
  delegation system is safe to use without further audit gates;
  removing it converts every admin into an owner-equivalent.
- Don't bypass the per-(chat, uid) identity binding. Owner-on-behalf
  flows ("grant for X via my key") would re-introduce the same gap
  the consent-gate closes; if a UX needs that, it's a separate
  authorize-then-execute layer, not a roles change.
- Don't move the roles store into a `forge` / `skill-forge` subtree.
  The path-gate hook does NOT cover `<corvin_home>/global/roles/`;
  the contract here is "operator-only via slash-commands or this
  CLI", same as `consent.py` / `auth_elevation.py`. Sliding it under
  a path-gate-protected subtree would either widen the gate's
  allowlist or wedge the CLI behind permission prompts — both wrong.
- Don't catch `ValueError` and silently fall back. Every value tag
  (`invalid-uid`, `invalid-bundle`, `owner-not-grantable`,
  `self-grant`, `insufficient-authority`, `cannot-revoke-peer`,
  `owner-not-revocable`, `owner-cannot-leave`, `no-entry`) is a
  user-facing signal — the JS wrapper renders each into a localised
  message. Silencing them produces the worst class of UX bug
  (silent partial success).

### Per-subtask E2E

`shared/test_roles.py` covers 14 cases (141 assertions): parse_ttl
semantics, bundle catalog consistency, intrinsic-owner with
DEV-mode parity, effective_role with lazy prune, grant input
validation, grant authority matrix, TTL clamp + indefinite,
revoke authority, owner-not-revocable, leave (4 sub-cases), status
fields, list_roles, CLI subcommand round-trip via subprocess, and
audit chain integrity with hash-chain verify.

`shared/js/test_roles_dispatcher.js` covers 22 sub-cases (53
assertions): dispatch routing for the five new commands, missing-uid
hint, owner status, stranger status, member-denied other-user
lookup, member self-lookup, owner /roles full listing, member
denied /roles, /grant usage hint, valid grant round-trip, /grant
owner rejection, admin-grants-admin denial, admin-grants-member
success, self-grant rejection, invalid bundle, owner revoke flow
+ idempotency, member revoke denial, admin-revokes-admin denial,
owner-revoke-owner denial, owner /leave denial, member /leave
success, no-entry /leave.

Both wired into `run-all-tests.sh` (suites 70 of 70).

## Layer 19 — Bot-disclosure card + /join self-service

Layer 18 (roles) lets the operator delegate authority. It does not solve
the **bot-disclosure problem**: a new participant in a chat does not know
that an AI bot is present, who runs it, what it does, or how to opt in /
out. Under the EU AI Act 2026 active disclosure is required for many
configurations; ethically it is the right baseline regardless.

Layer 19 closes that gap with three pieces:

1. **Per-(channel, chat, uid) seen-tracker** — a one-time disclosure
   card per fresh participant. Once shown, never re-shown.
2. **Card text generator** (DE / EN, ≤ 1500 chars) — explains who runs
   the bot, what it does, what `/join` / `/pass` / `/consent on` /
   `/leave` do. Includes a special paragraph when
   `observer_visibility = "transcript"` is on.
3. **`/join` self-service** — non-whitelist users register themselves
   as `observer` in the roles store via a self-grant. The owner sees
   them in `/roles` and can promote them later via `/grant`.

### Storage

Single JSON file per (channel, chat) at
`<corvin_home>/global/disclosure/<safe_channel>__<safe_chat>.json`:

```jsonc
{
  "<uid>": {
    "first_seen":     1778204770.0,
    "card_shown_at":  1778204770.0,
    "action":         "joined" | "passed" | "left" | "pending",
    "channel":        "telegram",
    "last_action_at": 1778205000.0    // present only after first transition
  }
}
```

Same `.lock` sidecar + atomic-replace pattern as `consent.py` /
`roles.py`. Nothing expires — disclosure is a one-time-per-uid contract;
the store grows monotonically. The owner is *implicit* (whitelist) and
never written to the store; `mark_seen` for owners returns
`action="owner-implicit"` and emits no audit event.

### Audit chain

`disclosure.shown` (first contact), `disclosure.action` (transitions),
`disclosure.joined` (self-grant via `/join`) all land in the unified
hash chain at `<corvin_home>/global/forge/audit.jsonl`. `voice-audit
verify` covers the new event-types automatically.

### Slash-commands

```
/join         (read-only-side) self-register as observer
/pass         (read-only-side) acknowledge card without taking action
/join         (owner-side)     friendly redirect — owners are intrinsic
/pass         (owner-side)     friendly redirect — owners don't see cards
```

The read-only-side commands are dispatched via `dispatchReadOnlyDisclosure`
in `bridges/shared/js/in_chat_commands.js`, exactly mirroring the
`dispatchReadOnlyConsent` pattern from Layer 16 Phase 4. The daemon
should call it BEFORE `dispatchReadOnlyConsent` and BEFORE
`maybeForwardAsObserver`.

### `/join` semantics — the only allowed self-grant

`disclosure.join()` is the only documented path that bypasses the
`roles.grant()` `self-grant` rejection. The bypass is justified because:

- the target bundle is always `observer` (the lowest); no trigger
  rights are gained, no quota is consumed;
- the action emits a dedicated `disclosure.joined` audit event (not
  `grant.issued`) so operators can distinguish self-onboarding from
  delegated grants in the chain;
- the entry's `granted_by` is set to the user's own uid, making the
  self-origin explicit on disk (no fake "system" attribution);
- the entry is indefinite by default — observer is the *least*
  privileged role and survives operator inattention without harm;
  the user can always `/leave`.

A user who is already a member or admin gets `already-elevated` from
`/join` — promotion to a higher role only happens through the owner's
`/grant`, not through self-service.

### Card rendering helpers (for the daemon integration in Phase 4)

`in_chat_commands.js` exports three helpers the daemons will call on
the next-message gate-zero:

```js
const seen = inChatCmds.disclosureHasSeen({ channel, chatKey, uid });
if (!seen) {
  const card = inChatCmds.disclosureCardText({
    channel, ownerLabel: '<owner display name>',
    hasObserverTranscript: getObserverVisibility(...) === 'transcript',
    lang: 'de',
  });
  await safeSend(card);
  inChatCmds.disclosureMarkSeen({
    channel, chatKey, uid, action: 'pending',
  });
}
```

The daemon then proceeds to its existing `readOnlyOk` / `authOk`
pipeline — the disclosure is purely additive, never blocks.

### What you, as Claude Code, must NOT do

- Don't extend `/join` to grant `member` or `admin` directly. The
  whole reason self-grant is OK for `observer` is that it's the
  least-privileged bundle. Self-grant of any trigger-bearing role
  reopens the very gap Layer 18's authority matrix closes.
- Don't show the card more than once per (channel, chat, uid). The
  store is the ground truth; re-showing the card on every message
  would (a) spam the participant, (b) overwhelm the audit chain,
  and (c) defeat the legal "informed once" baseline.
- Don't make the card length cap soft. `MAX_CARD_CHARS = 1500` is
  the contract every channel honours. WhatsApp, Discord and
  Telegram all happily render up to that; raising it produces
  scroll-cliffs on phones, which is exactly the audience the
  card targets.
- Don't suppress `disclosure.shown` for "already-known" uids. Every
  first-contact write goes to the chain — that is the legal record
  of "we informed this person on this date." Loud silent failures
  are fine; silent skip-because-we-saw-them-elsewhere is not.
- Don't move `/pass` to a quieter audit path. Even though `/pass`
  takes no action it is the user's explicit *informed-and-declined*
  decision; the chain entry is the proof that they had the chance
  to opt in.
- Don't allow `/join` to override an existing role downwards. A
  member typing `/join` should hit `already-elevated` and bounce —
  silently downgrading them to observer would be a UX trap (and a
  potential mid-session privilege loss).

### Per-subtask E2E

`shared/test_disclosure.py` covers 11 cases (55 assertions): seen
round-trip, owner-implicit (no audit, no store), action transitions,
card text DE / EN with length cap and transcript paragraph, `/join`
self-service, `/join` denials (owner-already, already-elevated),
`/pass` ack-without-grant, `list_seen`, CLI subcommand round-trip,
audit chain integrity with `verify_chain`, cross-module verification
that `/join` actually creates a `roles.observer` entry visible to
`roles.list_roles`.

`shared/js/test_disclosure_dispatcher.js` covers 11 sub-cases (27
assertions): owner-side `/join` /pass redirects, non-disclosure text
returns `null`, missing-uid daemon-bug hint, `/join` as stranger
registers as observer, idempotent `/join` returns already-observer,
`/join` as owner-via-readonly returns owner-already, `/pass` as fresh
user acknowledges, `/pass` as owner returns owner-already,
`disclosureCardText` DE + EN rendering, `disclosureHasSeen` /
`disclosureMarkSeen` round-trip, owner is implicit-seen.

Both wired into `run-all-tests.sh` (suites 72 of 72).

## Layer 20 — Quota + audit-view (delegated-budget visibility)

Layer 18 (roles) lets the owner delegate trigger-rights to non-whitelist
users. Layer 20 puts a **budget** on that delegation so a delegated
member cannot silently burn the owner's LLM-spend, hit a rate-limit-
induced outage, or DOS the bot through volume. It also surfaces the
unified hash chain as scoped per-user / per-chat slices through
`/audit me` and `/audit chat`.

Two modules:

  * `quota.py` — per-(channel, chat, uid) message + token counters with
    rolling-24-hour reset and per-bundle defaults.
  * `audit_view.py` — read-only filter over the unified hash chain;
    pairs with the `audit_self` (every bundle) and `audit_chat`
    (owner / admin) capabilities from Layer 18.

### Per-bundle default limits

| Bundle    | Messages/day | Tokens/day |
|-----------|--------------|------------|
| owner     | unlimited    | unlimited  |
| admin     | 500          | 100,000    |
| member    | 100          | 20,000     |
| observer  | 0 (no-trigger; observers don't reach the gate) |

Per-(channel, chat, uid) overrides via `quota.set_limit()` /
`/quota set <uid> <msgs|keep|clear> <tokens|keep|clear>`. `keep` keeps
the existing override (or bundle default if none); `clear` reverts to
the bundle default.

### Storage

Single JSON file per (channel, chat) at
`<corvin_home>/global/quota/<safe_channel>__<safe_chat>.json`. Same
`.lock` sidecar + atomic-replace pattern as `roles.py` / `consent.py` /
`disclosure.py`.

### Rolling 24h window

The window resets relative to the user's `day_anchor` (first counted
action of the period), not to wall-clock midnight. A user who only ever
triggers the bot in the evening has their window roll smoothly forward
instead of resetting at 00:00 local. Roll-overs emit `quota.reset`
audit events with `reason="window-rollover"` so operators can correlate
sudden counter drops in `/quota all`.

### check / record split (DON'T merge them)

The daemon flow is **always**:

```
result = quota.check(channel, chat_key, uid)
if not result["allowed"]:
    refuse(result["reason"])
    return
ok = run_bridge_turn(...)
if ok:
    quota.record(channel, chat_key, uid, tokens=ok.token_count)
```

Record is the *post-flight* accounting. **Failed runs do not consume
budget** — the user shouldn't pay for our bugs. A merged
`check_and_record` would silently burn budget on every internal
exception.

### Audit chain

`quota.recorded` (INFO, on every successful turn), `quota.over_limit`
(WARNING, on denial), `quota.set_limit` (INFO, on operator override),
`quota.reset` (INFO, on operator reset OR window roll-over). All land
in the unified chain at `<corvin_home>/global/forge/audit.jsonl`.
`voice-audit verify` covers them.

### Slash-commands

```
/quota                  caller's own usage
/quota <uid>            owner/admin: another user's usage
/quota all              owner/admin: all users in chat
/quota set <uid> <msgs|keep|clear> <tokens|keep|clear>   owner/admin
/quota reset <uid>      owner/admin
/audit                  shorthand for /audit me 20
/audit me [<n>] [<prefix>]    caller's events (last N, optional event_type prefix)
/audit chat [<n>] [<prefix>]  owner/admin: chat-scoped events
```

`/audit me` admits events whose details name the caller in any of:
`uid`, `target`, `grantor`, `granted_by`, `revoker`, `revoked_by`,
`user`. The four-way match lets a user see grants AT them and grants
BY them in a single view — useful for "what happened to me lately?"
without needing two queries.

### audit_view contract: read-only, never writes

`audit_view.py` MUST NOT write to the chain. The L20/15 test asserts
that `view_me` + `view_chat` calls don't change the audit file's size.
A future "search-and-redact" feature would belong in a separate
`audit_redact.py` module with its own audit events, not here.

### What you, as Claude Code, must NOT do

- Don't merge `check` and `record` into one call. The split is the
  load-bearing mechanism that makes failed runs free for the user.
  A merged version forces accounting on the request side and
  silently burns budget on every internal exception. The
  `failed_run_no_consume` test (L20/5) is the regression gate.
- Don't make `set_limit` reach into `roles.py` to override the
  bundle catalog. The per-bundle defaults in `DEFAULT_LIMITS` are
  the published contract; per-uid overrides go in the *quota*
  store. A roles-level "this admin gets 5x quota" would couple
  two orthogonal concepts and break the mental model.
- Don't widen `/audit` to cross-chat queries. The `chat_key`
  filter is the operator's sanity preserver — without it, a
  noisy cross-chat audit dump would drown the actual signal a
  member needs to debug their own day.
- Don't drop `quota.over_limit` events on the floor in the daemon.
  The audit event is the only mechanism by which an operator can
  notice a budget-related failure pattern; silencing it converts
  a recoverable user-visible refusal into invisible breakage.
- Don't auto-promote / auto-demote based on quota usage. Quota
  enforcement is a **refusal** layer, not a role-mutation layer.
  A user who blew through their messages should hit a polite
  refusal, not silently lose their member role — quota and roles
  have different blast radiuses and should stay separate.
- Don't treat `audit_view` results as authoritative for security
  decisions. The chain is the source of truth; the view is a
  cache-free read that may include events from before a settings
  edit. Use `verify_chain` for integrity checks, not the view.

### Per-subtask E2E

`shared/test_quota.py` covers 15 cases (72 assertions): owner-bypass,
observer denial, member round-trip, messages-exceeded with audit,
tokens-exceeded with audit, failed-runs-don't-burn-budget,
set_limit override + clear, reset + idempotency, window rollover with
audit, list_usage, CLI subcommand round-trip, audit_view.view_me with
4-way uid match, audit_view.view_chat with prefix filter,
summarize_event single-line render, hash-chain integrity + view-only
read invariant.

`shared/js/test_quota_dispatcher.js` covers 15 sub-cases (34
assertions): dispatch routing for /quota and /audit, owner-bypass
display, member self-view with cap rendering, member-denied other-quota,
owner-sees-other, /quota all with full list, member-denied /quota all,
/quota set + verify, member-denied /quota set, /quota reset + verify,
/audit me as owner shows grant events, /audit me as member shows own
events, /audit chat as owner, member-denied /audit chat with capability
hint, /audit chat with prefix filter.

Both wired into `run-all-tests.sh` (suites 74 of 74).

## Layer 21 — Curated proposal stack (multi-user input, owner-go)

Layer 16 Phase 2 (`observer_visibility = "transcript"`) already lets a
chat collect read-only-sender messages into a buffer that auto-prepends
to the next OWNER turn. Useful, but **passive**: the owner sees what
landed only after the LLM has already replied.

Layer 21 is the **active**, **curated** variant. Multiple users
(members, observers, even the owner) submit content via `/propose`;
the stack accumulates without auto-triggering. The owner reviews via
`/proposals`, drops entries via `/proposal rm <id>`, and explicitly
fires the LLM with `/go [steering]`. At that moment the stack is
consumed atomically and folded into the prompt together with the
owner's optional steering text.

### Storage

Single JSON file per (channel, chat) at
`<corvin_home>/global/proposals/<safe_channel>__<safe_chat>.json`.
Same `.lock` sidecar + atomic-replace pattern as
`consent.py` / `roles.py` / `disclosure.py`.

Limits: `MAX_STACK_SIZE = 50` (oldest dropped on overflow),
`MAX_TEXT_CHARS = 2000` per entry (truncated). Proposals do not
expire by themselves — explicit operator action via `/proposal clear`
or implicit consume via `/go`.

### Audit chain

`proposal.added` (INFO, on every `/propose`), `proposal.removed`
(INFO, on `/proposal rm`), `proposal.cleared` (INFO, on
`/proposal clear` or implicit clear), `proposal.executed` (INFO with
count + from_uids + owner_text length, on `/go`). All land in the
unified hash chain at `<corvin_home>/global/forge/audit.jsonl`.
`voice-audit verify` covers them.

### Slash-commands

```
/propose <text>         everyone (read-only-side too) — append to stack
/proposals              owner/admin: list current stack
/proposal rm <id>       owner/admin: drop one entry
/proposal clear         owner/admin: empty stack without triggering
/go [steering]          owner/admin: consume stack + trigger LLM
```

### Daemon contract

`/go` cannot be handled inside `dispatch()` because dispatch only
returns reply text — it cannot synthesise a new inbox message that
triggers the LLM. The daemon (currently only Discord) handles `/go`
explicitly:

1. Match `/go(?:\s+(.+))?$` BEFORE `inChatCmds.dispatch`.
2. Call `inChatCmds.proposalsBuildGoPayload(ctx, ownerText)`.
3. If `payload.allowed === false`: reply with `payload.reply`, return.
4. If `payload.allowed === true`: send `payload.ack`, then
   `writeInbox({ ..., text: payload.prompt })` so the adapter triggers
   claude with stack + owner-steering as one combined prompt.

The atomic consume + clear happens inside `proposal.consume_for_go()`
during the JS helper call — by the time the inbox write goes out, the
stack file is already empty. A retry / parallel `/go` would see an
empty stack.

### Read-only-side `/propose`

`dispatchReadOnlyProposal(ctx)` is exposed for daemons that want
non-whitelist senders to add proposals without first being granted a
role. Mirror of `dispatchReadOnlyConsent` / `dispatchReadOnlyDisclosure`.
The dispatcher records the caller's effective role (most likely
`observer` or `none`) as `from_role` on the proposal entry.

### What you, as Claude Code, must NOT do

- Don't add `/go` to `dispatch()`. It cannot work there — dispatch
  can only return reply text, not write a new inbox message. The
  test (`L21/13–15`) verifies that `proposalsBuildGoPayload` is the
  single supported entry point.
- Don't merge stack consumption into the LLM call. The atomic
  consume happens BEFORE the inbox write, so a retry / parallel /go
  cannot double-consume. Splitting the consume into "read, send to
  LLM, then clear on success" reopens the double-trigger window.
- Don't expire proposals by TTL. The user is brain-storming with
  the stack — surprise mid-night cleanup destroys collaborative
  work. Explicit `/proposal clear` or implicit `/go` are the only
  removal paths.
- Don't widen `MAX_TEXT_CHARS` above 2000 without rethinking the
  prompt budget. A 50-entry stack at 2000 chars each = 100 KB
  prepended to the prompt; exceeding context windows. The current
  cap leaves room for the actual conversation.
- Don't make the stack quota-charged at submission. Submission is
  free; only the consuming `/go` charges the OWNER's quota (one
  message, plus the tokens claude actually consumes). Members
  should not pay for ideas that the owner ignores.

### Per-subtask E2E

`shared/test_proposal.py` covers 11 cases (60 assertions): add basics
(empty / valid / truncation / stack-cap drop oldest), list + get
round-trip, remove existing + missing, clear + idempotency,
consume_for_go atomic with audit, format_for_prompt with content +
empty stack, status fields, id collision avoidance, CLI subcommand
round-trip, audit chain integrity with hash-chain verify.

`shared/js/test_proposal_dispatcher.js` covers 16 sub-cases (38
assertions): dispatch routing for /propose /proposals /proposal,
usage hints, owner add, member add, owner /proposals listing, member
denial of /proposals, /proposal rm with id parsing + idempotency,
/proposal clear, member denial of /proposal sub-commands,
read-only-side dispatcher routing + usage + valid add,
proposalsBuildGoPayload empty + populated + owner-text-only paths,
member /go denial.

Both wired into `run-all-tests.sh` (suites 76 of 76).

## Layer 28 — Conversation Recall + User Modeling (ADR-0016)

Per-tenant cross-session memory. Two complementary mechanisms,
both layered on the existing per-tenant tree, both **opt-in per
chat_profile**, both routed through the unified audit hash chain.

### A) Conversation Recall (Phase 28.1) — FTS5 redacted turn-pair index

- **Storage:** `<tenant_home>/global/memory/recall.db` (mode `0o600`,
  SQLite WAL).  Two tables: `turns` (one row per turn-pair, metadata
  + redacted text) + `turns_fts` (virtual FTS5 over user/assistant
  text, `tokenize='unicode61 remove_diacritics 2'`).
- **Text-mode PII redactor** in `conversation_recall.py` —
  email / IBAN / credit_card / us_ssn / ch_ahv / phone. Pattern set
  aligned with the Layer 24 (`pii_detector.py`) value-regex backend
  so a future text-mode API of the detector can absorb us. Every
  match folds to `<redacted:<class>>` BEFORE the row hits disk.
  Original text never persists.
- **Public API** in `bridges/shared/conversation_recall.py`:
  `index_turn(channel, chat_key, *, user_text, assistant_text,
   msg_id, persona, run_id, ts=None, tenant_id=None)`,
  `recall(query, *, channel, chat_key, since, until, limit=20,
   caller_persona, tenant_id)`,
  `forget(channel, chat_key, before_ts, tenant_id)`,
  `redact_text(text) -> (redacted, counts)`,
  `is_recall_permitted_for_persona(profile) -> bool`.
- **Adapter integration:** `process_one()` in `adapter.py` calls
  `index_turn()` directly AFTER auto-grade and BEFORE the local
  announce. Default-on; flip
  `chat_profile.conversation_recall_indexing_enabled = false` to
  opt out. The hook is best-effort: every failure path emits
  `memory.indexing_failed` into the chain and returns silently —
  the bridge turn never fails because indexing did.
- **Audit events** (registered in `EVENT_SEVERITY`, metadata only):
  `memory.turn_indexed`, `memory.recall_query`,
  `memory.indexing_failed`. Per-event `_AUDIT_ALLOWED_FIELDS`
  set; extra keys silently dropped.

### B) User Modeling (Phase 28.2) — distilled per-chat profile

- **Storage:**
  `<tenant_home>/global/memory/user_model/<safe_channel>__<safe_chat>.json`,
  mode `0o600`. Schema-versioned (`apiVersion: corvin/v1`,
  `kind: UserModel`).
- **Schema (curated, no free-form keys):**
  `communication_style` (scalar, ≤ 400 chars), plus five list[str]
  fields — `preferences`, `recurring_topics`, `goals`, `patterns`,
  `do_not_assume`, each ≤ 10 entries × ≤ 200 chars/entry.
  `_validate_spec()` coerces-or-drops; non-string values dropped,
  over-long entries truncated, unknown keys silently removed.
  The distiller is instructed to emit OBSERVABLE patterns only
  ("asks for trade-offs explicitly" — yes; "is detail-oriented" —
  no).
- **Distill pipeline:** counter-driven in `adapter.process_one()` —
  every `chat_profile.user_model_distill_every_n_turns` (default 50)
  successful turns the adapter spawns a worker thread that runs
  `_user_model_distill_async(channel, chat_key)`. The async helper
  serialises behind `_user_model_distill_guard` (one distill at a
  time per process) and calls `user_model.distill(...)`, which:
    1. loads the previous model (or `UserModel.empty`),
    2. fetches the last `max_turns=30` redacted turns from
       `conversation_recall.recall(...)`,
    3. shells out to `claude -p --max-turns 1 --no-tools` with a
       fixed distiller prompt (Max-Abo-native; **zero Anthropic
       SDK calls** — the AST check `case_no_anthropic_sdk_import`
       in `test_user_model.py` is the regression gate),
    4. tolerantly parses the first `{...}` object from stdout,
    5. validates + diffs against the previous spec, saves +
       emits `memory.user_model_distilled` with the list of
       changed FIELD NAMES (never values).
- **Adapter-inject:** `_resolve_spawn_inputs()` reads
  `user_model.load(channel, chat_key)` when
  `chat_profile.user_model_enabled is True` and appends the
  `<user_context>...</user_context>` block AS THE LAST entry of
  the system prompt — most-recent-instruction-wins rule
  (mirroring `summarize.py::SELF_CHECK_BLOCK` ordering).
- **Persona-ACL:** `memory_recall_enabled: true` on the persona JSON
  gates MCP / slash-command callers of `recall(...)`. Bundle
  defaults: `assistant`, `research`, `coder` → `true`. Every
  other persona → `false`. The adapter-internal indexing hook
  in `process_one()` does NOT consult this — only the
  dispatcher edges (future MCP server `corvin-memory` /
  slash-command `/recall`) do.
- **Audit events** (`_AUDIT_ALLOWED_FIELDS`):
  `memory.user_model_distilled` (changed_fields + distill_count
  + wall_clock_s), `memory.user_model_distill_failed` (reason +
  200-char error), `memory.user_model_forgotten`. NEVER the spec
  values.

### Path-gate protection

`<corvin_home>/**/memory/**` joined the protected-path set in
`operator/voice/hooks/path_gate.py` (the `"memory" in rel_parts`
check). Direct `Write` / `Edit` / `Bash` writes to the recall DB,
the user-model JSON, or any future memory artefact are denied by
the layer-10 hook regardless of permission mode. All writes go
through the `conversation_recall.index_turn(...)` /
`user_model.save(...)` Python API, which is reached only from
adapter code or the operator CLI.

### GDPR Right to Erasure (designed; slash-command in Phase 28.4)

`conversation_recall.forget(*, channel, chat_key, before_ts,
 tenant_id)` deletes matching rows; the AFTER-DELETE trigger
cascades into the FTS5 virtual table. `user_model.forget(channel,
 chat_key)` deletes the JSON + emits
`memory.user_model_forgotten`. The audit event records WHEN the
forget happened and the matching filter, but NEVER the deleted
text — DSGVO Art. 17 requires deleting the data, not the audit
record of the deletion. The daily retention sweep + `/forget`
slash-command land in Phase 28.4.

### What you, as Claude Code, must NOT do (Layer 28)

- **Don't put raw text into any audit-event detail field.** The
  `_AUDIT_ALLOWED_FIELDS` allow-lists in `conversation_recall.py`
  and `user_model.py` are the structural guards; the
  `test_no_raw_text_in_audit` / `test_audit_metadata_only`
  regression gates fail the suite if a future edit smuggles a
  user/assistant string into the chain. Mirror of L23 / L25
  metadata-only rule.
- **Don't index raw text before redaction.** `index_turn()` calls
  `redact_text()` BEFORE the INSERT. A "skip redaction in fast
  path" optimisation is the entire DSGVO Art. 5(1)(c) gap this
  layer exists to close.
- **Don't widen `recall(...)` to cross tenants.** The
  `tenant_id` argument resolves the DB path BEFORE the SQLite
  open call; there is no multi-tenant aggregation method by
  design. Future "operator-side cross-tenant analytics" belongs
  in a separate ADR with its own auth gate, not as an extension
  here.
- **Don't auto-enable `user_model_enabled` across bundle personas.**
  GDPR Art. 6 requires explicit lawful basis for processing
  personality data. The operator flips the flag per chat in
  `chat_profiles[<chat>].user_model_enabled = true`. Bundle-
  persona defaults stay false; flipping them silently converts
  "opt-in feature" to "ambient surveillance".
- **Don't add `import anthropic` (or any LLM SDK) to
  `conversation_recall.py` or `user_model.py`.** The AST lints
  `case_no_anthropic_sdk_import` in both test suites walk the
  source and fail on a forbidden import. The cost contract is
  operator-subscription-native via the `claude -p` subprocess
  pattern (mirror of `dialectic.py` cli mode + `user_style.py`
  judge pattern).
- **Don't catch the distiller's `judge-unparseable` /
  `judge-timeout` / `recall-empty` paths silently in
  `_user_model_distill_async`.** The helper already wraps every
  branch in `try / except Exception: pass`, but the wrapped
  failure paths emit `memory.user_model_distill_failed` from
  inside `user_model.distill(...)` BEFORE returning. Removing
  the inner audit emit drops the operator's only signal that
  distill is silently flat-lining.
- **Don't move the `<user_context>` block off the LAST position
  in `_resolve_spawn_inputs`.** The most-recent-instruction-wins
  rule is load-bearing; sliding the block above `personal_tools`
  or `user_style` lets a persona-tone addendum (which lands
  after) override the user-context. The
  `test_block_lands_last` case is the regression gate.
- **Don't bypass `is_recall_permitted_for_persona(profile)` at
  the future MCP / slash-command edge.** The persona-ACL is the
  structural defence against a `forge` persona reading the
  owner's recall history mid-tool-run. Adapter-internal callers
  (process_one indexing, /forget) do NOT consult the ACL — only
  the dispatcher edges that serve potentially-untrusted recall
  queries.

### References

- `Corvin-ADR: decisions/0016-conversation-recall-and-user-modeling.md` —
  the design ADR
- `operator/bridges/shared/conversation_recall.py` — recall
  storage + index + redactor + persona-ACL helper
- `operator/bridges/shared/user_model.py` — schema +
  load/save + distiller + render_block
- `operator/bridges/shared/test_conversation_recall.py` —
  13-case E2E (PII redaction, FTS5 escape, audit metadata-only,
  per-tenant isolation, forget cascade, no-SDK lint)
- `operator/bridges/shared/test_user_model.py` — 18-case
  E2E (schema, distiller stub, judge failure modes, audit
  field-allowlist, render_block, persona-ACL, no-SDK lint)
- `operator/bridges/shared/test_adapter_recall.py` — 8-case
  E2E for the adapter wiring (user_context-block-LAST,
  distill-scheduler, indexing-gate)
- `operator/voice/hooks/path_gate.py` — `memory` rel-part check
- `operator/forge/forge/security_events.py` — 6 new
  `memory.*` event types in `EVENT_SEVERITY`
- L23 (voice-transcribe) — metadata-only-audit precedent
- L24 (PII detector) — regex backend reused here as text mode
- L26 (user-style learner) — closed-loop bullet pipeline;
  same `claude -p` cost-contract pattern

