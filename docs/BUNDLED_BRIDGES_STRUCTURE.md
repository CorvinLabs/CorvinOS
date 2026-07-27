# Bundled Bridges: Supervised Node Daemons

**Date:** 2026-07-27
**Status (2026-07-27):** the seven supervisor classes exist, are tested, and are now
**declared by the boot path** — `bootstrap._bundled_bridge_declarations()` injects them
from `bridges/registry_entries.py` when the feature flag `bridge_supervisor_plugins`
(default **off**) is on. Until that date a second, independent switch was required and
nothing in the shipped tree wrote it, so none was ever loaded. The declaration in
`spec.plugins.installed` is not: the shipped `tenant.corvin.yaml` template has no bridge
block and no code writes one. So "shipped dark" understates it — this is **shipped, dark,
and undeclared**.
**ADRs:** ADR-0238 (bridges as supervised plugins), ADR-0243 (the `boot_layer` axis)
**Code:** `core/plugins/corvin_plugins/bridges/`, `operator/bridges/bridge_manager.py`
**Tests:** `core/plugins/tests/test_bridge_supervisor.py` — **78 passing**, measured
2026-07-27

---

## Correction: bridges are Node, not Python

ADR-0238 and ADR-0242 originally described the bridges as Python modules under
`adapters/<name>_adapter` that would be refactored into `CorvinPlugin` classes,
and an earlier revision of *this* document described a
`core/core_plugins/tier_2_bundled/discord_bridge/` tree with a `discord.py`
client. **None of that exists.** The verified state of the repository is:

```
operator/bridges/
├─ bridge.sh                 ← the operator entry point in daily use
├─ bridge_manager.py         ← cross-platform Python launcher (Windows + wheel)
├─ shared/
│  ├─ adapter.py             ← the Python half: polls inbox, runs the turn, writes outbox
│  └─ js/                    ← shared JS the daemons require()
├─ discord/daemon.js
├─ telegram/daemon.js
├─ whatsapp/daemon.js
├─ slack/daemon.js
├─ email/daemon.js
├─ signal/daemon.js
└─ teams/daemon.js
```

Seven **Node.js daemons** plus one Python adapter, talking over a file queue
(`<corvin_home>/bridges/shared/{inbox,outbox,processed}`). `adapters/discord_adapter`
never existed. The ADRs have been corrected; this document describes what was
actually built.

## Why there is no Node rewrite

Rewriting seven working transports in Python to satisfy a plugin protocol would
trade a shipped, battle-tested message layer — Baileys pairing, Discord gateway
reconnects, Slack socket mode, IMAP IDLE — for a green field, and every one of
those has already cost incidents to get right. The plugin protocol wants
`on_load` / `on_unload` / `health_check`; a **process supervisor** satisfies that
contract exactly as well as a rewrite would, and it keeps the daemons the thing
they already are.

So Phase 5 ships a Python **supervisor plugin per bridge**. It starts, stops and
health-checks the existing `daemon.js` as a subprocess. Not one line of
`daemon.js` changed.

---

## The supervisor plugin

`corvin_plugins.bridges.supervisor.BridgeSupervisorPlugin` is generic and
parameterised by channel name. Seven thin subclasses bind the name so a tenant
config can reference a stable class path:

| Channel  | Class                   | `plugin_id`        |
|----------|-------------------------|--------------------|
| discord  | `DiscordBridgePlugin`   | `discord-bridge`   |
| telegram | `TelegramBridgePlugin`  | `telegram-bridge`  |
| whatsapp | `WhatsAppBridgePlugin`  | `whatsapp-bridge`  |
| slack    | `SlackBridgePlugin`     | `slack-bridge`     |
| email    | `EmailBridgePlugin`     | `email-bridge`     |
| signal   | `SignalBridgePlugin`    | `signal-bridge`    |
| teams    | `TeamsBridgePlugin`     | `teams-bridge`     |

All of them are `plugin_type = "bridge_channel"` and `boot_layer = "bundled"`.
Bundled is **disableable** (`can_disable()` is true for every boot layer except
`compliance`) — a messenger transport is not a compliance mechanism and an
operator must be able to switch it off.

The supervisor deliberately does **not** register with `ctx.channel_registry`:
that registry expects an object that can send and receive messages. A process
supervisor cannot, and handing it one would give callers a channel that silently
drops everything.

All process knowledge — home resolution, runtime-vs-source dir, `service.env`
merge, Node discovery, the systemd probe — is **borrowed from**
`bridge_manager.py` instead of reimplemented. A second copy of "where does this
daemon live" is exactly the reader≠writer split that has already cost this repo
two incidents.

---

## Enabling a bridge supervisor

Two independent switches, both required.

### 1. The feature flag (ships off)

`spec.features.bridge_supervisor_plugins` in `tenant.corvin.yaml`, or the
Console **Settings → Features** panel. Default **false**: on a fresh install and
after an upgrade, bridges are managed exactly as they are today. Off is a *quiet*
path — nothing starts, nothing raises, and `health_check()` returns `ok=True`
with `supervisor off (feature flag)`.

The flag is read defensively. If `corvin_console` is not importable at all
(headless core, a wheel shipped without the Console), the flag reads **false**
and the supervisors do nothing. `core/plugins` stays importable without the
Console.

### 2. The declaration — **this is the one nobody has written**

An entry in `spec.plugins.installed`. Writing a plugin into a version-controlled
tenant config *is* the explicit opt-in that `bootstrap_declared` asks for.

**No shipped file contains such an entry.** The template `tenant.corvin.yaml` has no
bridge block, no installer writes one, and there is no Console action that adds one. The
YAML below is what an operator must type by hand today; until they do, every behaviour
described in the rest of this document is unreachable on their install no matter how the
feature flag is set.

```yaml
spec:
  features:
    bridge_supervisor_plugins: true   # ships false — turn on deliberately

  plugins:
    installed:
      - id: discord-bridge
        boot_layer: bundled
        class_path: "corvin_plugins.bridges.supervisor:DiscordBridgePlugin"

      - id: slack-bridge
        boot_layer: bundled
        class_path: "corvin_plugins.bridges.supervisor:SlackBridgePlugin"

      # Park a bridge without deleting its block:
      - id: telegram-bridge
        boot_layer: bundled
        class_path: "corvin_plugins.bridges.supervisor:TelegramBridgePlugin"
        config:
          enabled: false
```

`boot_layer: bundled` is honoured; `boot_layer: core` or `boot_layer: compliance` from a tenant
config is downgraded to `installed` and audited (`plugin.boot_layer_rejected`) —
a tenant config is operator-writable and may not promote itself into a
privileged boot layer.

`corvin_plugins.bridges.declaration_entry(channel)` / `declaration_entries()` generate
these entries so the docs, the Console and the tests read one source rather than three
copies of a dotted path that goes stale on the first rename. Generating an entry is not
the same as shipping one — today only the tests call these.

---

## The start gate

`on_load()` starts a daemon only when **every** condition holds. Each failure is
a quiet no-op with a reason kept for `health_check()`; `on_load()` never raises,
because a bridge that cannot start must cost the operator that bridge, not the
platform boot.

| # | Condition | If not |
|---|---|---|
| 1 | `bridge_supervisor_plugins` is on | quiet, `flag_off` |
| 2 | declaration not switched off (`config.enabled: false`) | quiet, `disabled_in_config` |
| 3 | channel has credentials (`channel_configured()`) | quiet, `not_configured` |
| 4 | no daemon for this channel already running | quiet, `already_running` (adopted) |
| 5 | runtime dir provisioned (`daemon.js` present) | quiet, `not_provisioned` |
| 6 | a usable Node ≥ 20 already on the box (`find_node()`) | quiet, `node_missing` |

Two of those deserve their reasoning spelled out:

**No `npm install` at boot (5).** `on_load()` runs inside the platform boot
sequence. `_materialise_channel()` can spend a minute on `npm install`, and a
supervisor that did that would stall the whole start. Provisioning stays with the
existing `bridge.sh` / Console path.

**`find_node()`, never `ensure_node()` (6).** `ensure_node()` downloads ~25 MB
from nodejs.org. Booting must not trigger a network download.

WhatsApp pairing also stays on the Console path: `channel_configured("whatsapp")`
is false until `auth/creds.json` exists, so an unpaired WhatsApp is a no-op here.
The QR flow is an interactive operator action, not a boot action.

---

## Duplicate start — the load-bearing invariant

Two Discord daemons polling the same outbox answer every message **twice**, and
the second one is invisible to `systemctl stop` — the orphan class found in the
ADR-0215 review. So "already running" is probed before every spawn.

The probe is `bridge_manager.channel_daemon_running(channel)` — added additively
in this phase, next to the existing `_adapter_running_pid()` whose two-stage
design it mirrors. It returns
`{"running": bool, "via": str, "pid": int, "confident": bool}` and layers four
independent signals, each authoritative on its own:

1. **our own handle** from a previous `on_load()` in this process (`via=supervisor`);
2. **`systemctl --user is-active corvin-voice-bridge-<channel>.service`** —
   true for `active`, `activating` *and* `reloading`, which closes the race
   window where a systemd-started daemon has not bound its port yet (`via=systemd`);
3. **a TCP probe** of the channel's well-known local port — WhatsApp's pairing
   port 7891 today (`via=port`);
4. **a system-wide process scan** for a live process whose command line runs
   `<channel>/daemon.js` (`via=process`).

Signal 4 is the generic one and the reason the probe exists at all: a daemon
started by `bridge.sh`, by systemd or by hand writes no pidfile we own. Matching
is on the `/<channel>/daemon.js` tail, separator- and case-normalised, so it
works for a POSIX runtime dir, a Windows runtime dir and a source-tree checkout —
and `tail -f /var/log/discord.log` does not read as a running Discord daemon.

### `confident=False` means refuse, not proceed

"I found nothing" and "I could not look" are different answers. If no enumeration
method is available (no `/proc`, no `pgrep`, no `wmic`), the probe reports
`confident=False` and the supervisor **refuses to start**, saying so in
`health_check()` (`ok=False`, "cannot verify whether a daemon is already
running"). Refusing costs an operator a bridge they can still launch the old way;
guessing wrong costs every user a duplicated conversation.

The same refusal applies when the probe raises, and when a vendored
`bridge_manager.py` predates the probe entirely.

---

## Restart policy: deliberately none

**A crashed daemon is not restarted automatically.** A bounded restart ladder was
considered and rejected:

* an auto-restart against a revoked token becomes a login loop against Discord's
  or Slack's API and gets the bot rate-limited or banned — the failure the
  operator then sees is worse than the one they had;
* systemd already supervises restarts on the path the maintainer actually uses,
  and a second supervisor with its own opinion means two restart loops fighting
  over one daemon;
* every restart would emit an audit event, so a crash-looping bridge would spam
  the hash-chained trail;
* this repo's incident history is specifically about *automatic* lifecycle
  machinery failing silently — a wedged outbox poller delivered nothing for 38
  minutes without a single log line.

Instead a dead daemon is **loud**: `health_check()` returns `ok=False` with the
exit code, which reaches the Console health surface and the audit trail via
`plugin.health_alert`. Recovery is an explicit operator action — disable and
re-enable the plugin, or use `bridge.sh` / systemd.

---

## Shutdown ladder

`on_unload()` is bounded and never raises:

```
SIGTERM (to the process GROUP where the OS has one)
   ↓ wait STOP_GRACE_S = 5 s
SIGKILL
   ↓ wait KILL_REAP_S = 2 s
abandon — audited as how="abandoned", return
```

Total worst case ≈ 7 s. Bounded on purpose: a SIGTERM hang once took down every
live session in this repo, and a supervisor that waits forever on an
unresponsive child reproduces exactly that. The daemon is spawned into its own
process group (`start_new_session=True`) so the signal reaches forked helpers —
a WhatsApp/Baileys daemon forks, and signalling only the parent leaves orphans.

**A daemon adopted from systemd or `bridge.sh` is NOT stopped here.** We did not
start it, its owner is still running, and killing another supervisor's process on
a plugin reload turns a hot-reload into an outage.

---

## Health semantics

`ok=False` is reserved for "this should be running and is not". Expected
not-started configurations stay green — painting them red trains the operator to
ignore the health surface.

| State | `ok` | Message |
|---|---|---|
| `flag_off` | yes | supervisor off (feature flag) |
| `disabled_in_config`, `not_configured`, `not_provisioned`, `node_missing` | yes | no daemon expected (*state*) |
| `already_running`, external daemon alive | yes | daemon running (managed externally) |
| running, uptime < 5 s | yes | daemon starting |
| running, adapter polling the queue | yes | daemon running |
| running, **no adapter** | no | daemon running but no adapter is polling the queue |
| exited | no | daemon exited (code *N*) |
| external daemon vanished | no | externally managed daemon is no longer running |
| `unverifiable` | no | cannot verify whether a daemon is already running — refused to start |
| `manager_missing`, `probe_failed`, `spawn_failed` | no | (reason code) |

Two of these are worth calling out.

**The half bridge.** A daemon with no adapter polling the queue receives every
message and answers none — silent in every log. `bridge_manager.adapter_running_pid()`
(the second additive helper added in this phase) makes it a red health tile
instead. Unknown counts as alive, so an older vendored `bridge_manager` does not
paint every healthy bridge red.

**Zombie reaping.** `health_check()` calls `proc.poll()`, which reaps. Without it
an exited daemon would linger as a zombie and keep reporting healthy.

---

## Secrets

`settings.json` holds bot tokens, IMAP passwords and phone numbers. The
supervisor never reads a credential **value**: `channel_configured()` answers a
boolean, and every log line and audit detail carries only the channel name, a
closed-set reason code and a pid.

Audit events are `bridge.supervisor.{started,stopped,skipped,start_failed}` with
details drawn from a closed key set (`channel`, `plugin_id`, `pid`, `reason`,
`via`, `how`, `error_type`) — asserted by the test suite. Exception **class**
names only, never `str(exc)`: a loader or spawn error routinely quotes a path,
and a path carries the OS username.

Daemon stdout goes to `daemon-start.log` in the daemon's own runtime dir and is
never read back into an audit record or an API response — that output routinely
contains chat text and sender JIDs.

---

## The existing path is unchanged

**`bridge.sh` remains the operator entry point in daily use, and nothing about it
changed.** So do `bridge_manager.py fg`, the systemd units, and the Console
"Start bridge" button. The additions to `bridge_manager.py` are purely additive:
two new public functions (`channel_daemon_running`, `adapter_running_pid`) and
two private helpers. No existing function changed behaviour.

With the flag off — the shipped default — the supervisors are inert and the
system behaves byte-for-byte as it did before Phase 5.

With the flag on, the two paths coexist safely because the supervisor **adopts**
rather than duplicates: if `bridge.sh` or systemd already started a daemon, the
supervisor detects it, does not spawn a second one, reports it as healthy, and
does not kill it on unload.

---

## Current limits

* **No auto-provisioning.** The supervisor will not `npm install` or download
  Node; an unprovisioned bridge is a quiet no-op. Use `bridge.sh` or the Console
  once, then the supervisor can manage it.
* **No auto-restart.** By design, see above.
* **No adapter lifecycle.** The supervisor manages the Node daemon only. The
  Python adapter is a separate process with a separate owner
  (`ensure_adapter_detached()`); the supervisor reports on it but does not start
  it.
* **No channel_registry participation.** A supervisor is not a transport, so
  in-process message routing does not go through it.
* **The shipped `tenant.corvin.yaml` template carries no bridge declarations, and
  nothing else writes one.** The config form above has to be added by the operator (or by
  a later Console Settings action); `spec.plugins.installed` ships empty. This is the
  limit that subsumes all the others: **no supervisor loads on any install today**,
  regardless of the feature flag, so none of the behaviour documented above has ever run
  outside the test suite. `bridges/registry_entries.declaration_entry()` generates the
  block, but generating it is not shipping it.

---

## Test coverage

`core/plugins/tests/test_bridge_supervisor.py` — **78 tests, all passing** (measured
2026-07-27), no real Node process is ever started (`subprocess.Popen` is mocked throughout). Both flag states are
covered, as the feature-flag rule requires.

| Group | What it pins |
|---|---|
| Identity + declaration | all seven channels, `bridge_channel` type, `boot_layer=bundled`, class paths resolve |
| Flag off | nothing starts, nothing raises, no probe runs, health is green and says "off", an unreadable flag is an off flag |
| Start | argv + cwd, own process group, `service.env` merge, audit event, every skip reason |
| Duplicate start | already-running is adopted for all four `via` values, second `on_load` is a no-op, unverifiable/raising/absent probe all refuse |
| Unload | SIGTERM first, SIGKILL only after the grace period, every wait bounded, unkillable child abandoned, external daemon not killed |
| Health | dead daemon not ok, reaping, starting window, half bridge, vanished external daemon, no path in any message |
| Registry | lands on bundled, `can_disable()` true, all seven register side by side |
| Secrets | a fake token in settings appears in no log line and no audit detail on any path |
| `bridge_manager` probe | separator/case normalisation, no cross-channel confusion, signal precedence, `confident` semantics |

Each of the load-bearing behaviours was mutation-checked: dropping the
duplicate-start guard, sending SIGKILL first, failing the flag open, starting on
an unverifiable probe, hiding a dead daemon, and removing the wait timeout each
turn the suite red.
