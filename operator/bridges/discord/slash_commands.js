// slash_commands.js — Discord application-command registration for the
// bridge.
//
// Why this exists: Discord's client-side picker blocks any /-prefixed
// message with "/<name> isn't available in this environment" when no
// matching application command is registered. The text-based dispatch
// in daemon.js never even gets the message. By registering each bridge
// slash-command as a CHAT_INPUT application command, the picker accepts
// them, the user can send them, and we route the resulting interaction
// through the same dispatch chain as a plain text message.
//
// Design:
//   - Every command takes ONE optional STRING option called `args`.
//     This keeps the command schema uniform and lets us reconstruct the
//     equivalent text payload (`/<cmd> <args>`) for the existing
//     dispatcher in shared/js/in_chat_commands.js.
//   - Registration runs once on `clientReady` — we use the GLOBAL command
//     scope (`client.application.commands.set(...)`) so it covers DMs and
//     every guild the bot is in, including future joins. First-time
//     propagation can take up to ~1h; subsequent updates are typically
//     within a minute.
//   - Set DISCORD_GUILD_IDS=<id1>,<id2> to register per-guild instead
//     (instant updates — useful during development).
//
// We ONLY register the slash-commands the user is most likely to type.
// The text path still works for everything else (e.g. /? or /:foo) —
// register-everything would just clutter the picker.

const COMMANDS = [
  // ── Layer 13: mid-stream injection ─────────────────────────────────
  { name: 'btw', description: 'Drop a note into the running task (mid-stream).',
    options: [{ name: 'args', description: 'The note to inject', type: 3, required: false }] },

  // ── Task control ───────────────────────────────────────────────────
  { name: 'stop',    description: 'Cancel the running task (SIGTERM).' },
  { name: 'cancel',  description: 'Cancel the running task (alias for /stop).' },
  { name: 'abbruch', description: 'Cancel the running task (German alias).' },
  { name: 'halt',    description: 'Cancel the running task (alias for /stop).' },
  { name: 'new',     description: 'Reset this chat — purge skills + tools + voice state.' },
  { name: 'clear',   description: 'Reset this chat — purge skills + tools + voice state.' },
  { name: 'reset',   description: 'Reset this chat — purge skills + tools + voice state.' },
  { name: 'welcome', description: 'Corvin intro card (text + voice-note).' },
  { name: 'willkommen', description: 'Corvin-Begrüßung auf Deutsch.' },
  { name: 'start',   description: 'Corvin intro card (alias for /welcome).' },
  { name: 'hi',      description: 'Corvin intro card (short alias).' },
  { name: 'on',      description: 'Enable AI for this chat.' },
  { name: 'off',     description: 'Disable AI for this chat.' },
  { name: 'status',  description: 'Show this chat\'s status.' },
  { name: 'debug',   description: 'Toggle debug mode (live tool-use stream).',
    options: [{ name: 'args', description: 'on | off (omit to toggle)', type: 3, required: false }] },
  { name: 'all',     description: 'Audience scope for this chat.',
    options: [{ name: 'args', description: 'on | off | status', type: 3, required: false }] },

  // ── Help / introspection ───────────────────────────────────────────
  { name: 'help',     description: 'Full overview of slash-commands.' },
  { name: 'hilfe',    description: 'Full overview of slash-commands (German).' },
  { name: 'whoami',   description: 'Current persona + capabilities.' },
  { name: 'skills',   description: 'What this role can do right now.' },
  { name: 'personas', description: 'List available cowork personas.' },

  // ── Persona switch ─────────────────────────────────────────────────
  { name: 'persona', description: 'Pin a cowork persona to this chat.',
    options: [{ name: 'args', description: 'Persona name (or "reset")', type: 3, required: false }] },

  // ── Voice / TTS reading mode ───────────────────────────────────────
  { name: 'voice-on',      description: 'Enable read-aloud of replies.' },
  { name: 'voice-off',     description: 'Disable read-aloud of replies.' },
  { name: 'voice-auto',    description: 'Threshold-based read-aloud (default).' },
  { name: 'voice-full',    description: 'Always read every reply in full.' },
  { name: 'voice-summary', description: 'Always summarise replies before TTS.' },
  { name: 'voice-status',  description: 'Show current voice mode + engine.' },
  { name: 'voice-lang',    description: 'Force TTS language.',
    options: [{ name: 'args', description: 'auto | de | en', type: 3, required: false }] },

  // ── Voice listener-profile (Layer 12) ──────────────────────────────
  { name: 'voice-user-show',    description: 'Show the listener profile.' },
  { name: 'voice-user-set',     description: 'Set a listener-profile field (key=value).',
    options: [{ name: 'args', description: 'level=… | jargon=… | learning=… | …', type: 3, required: true }] },
  { name: 'voice-user-clear',   description: 'Remove the listener profile.' },
  { name: 'voice-user-preview', description: 'Preview the AUDIENCE block.',
    options: [{ name: 'args', description: 'de | en (default de)', type: 3, required: false }] },
  { name: 'voice-user-help',    description: 'Field-by-field reference.' },

  // ── Dialectic gates (Layer 11) ─────────────────────────────────────
  { name: 'dialectic-on',     description: 'Global dialectic default-on.' },
  { name: 'dialectic-off',    description: 'Global dialectic kill-switch.' },
  { name: 'dialectic-status', description: 'Show dialectic modes + thresholds.' },
  { name: 'dialectic-set',    description: 'Per-site mode override.',
    options: [{ name: 'args', description: '<site> <fast|skill|cli|off>', type: 3, required: true }] },
  { name: 'dialectic-show',   description: 'Toggle reply-footer rendering.',
    options: [{ name: 'args', description: 'on | off', type: 3, required: false }] },

  // ── LDD layer toggles (Layer 14) ───────────────────────────────────
  { name: 'ldd-on',     description: 'Enable LDD globally (default).' },
  { name: 'ldd-off',    description: 'Kill-switch — every LDD layer off.' },
  { name: 'ldd-status', description: 'Master + per-layer state + cascade hints.' },
  { name: 'ldd-set',    description: 'Per-layer toggle.',
    options: [{ name: 'args', description: '<layer> <on|off>', type: 3, required: true }] },
  { name: 'ldd-preset', description: 'Swap the whole config to a named preset.',
    options: [{ name: 'args', description: 'default | strict | quick | off', type: 3, required: true }] },

  // ── Background task control (Phase 3: Discord Bridge Integration) ───
  { name: 'task', description: 'Start a long-running background task (with Discord notifications).',
    options: [{ name: 'args', description: 'The task instruction', type: 3, required: true }] },

  // ── Profile / memory / vault / schedule ────────────────────────────
  { name: 'profile',  description: 'Bridge-wide profile.',
    options: [{ name: 'args', description: 'show | set k=v | get k | rm k | reset', type: 3, required: false }] },
  { name: 'memory',   description: 'Long-term memory topics.',
    options: [{ name: 'args', description: 'list | show <t> | write <t> <text> | append … | forget <t>', type: 3, required: false }] },
  { name: 'vault',    description: 'Secrets vault (audit-logged).',
    options: [{ name: 'args', description: 'list | get <n> | set n=v | unlock <n> | forget <n> | audit', type: 3, required: false }] },
  { name: 'schedule', description: 'Cron-style reminders into your inbox.',
    options: [{ name: 'args', description: 'list | add <cron> <text> | rm <id>', type: 3, required: false }] },
  { name: 'goal', description: 'Set / show / clear the session goal (injected into every LLM turn).',
    options: [{ name: 'args', description: '<text> | show | clear', type: 3, required: false }] },

  // ── Layer-26 AWP-Workflow-Bridge ───────────────────────────────────
  { name: 'workflow', description: 'AWP workflow bridge (DAG + delegation).',
    options: [{ name: 'args', description: 'list | show <n> | validate <n> | run <n> [key=value …] | help', type: 3, required: false }] },
  { name: 'workflows', description: 'Alias for /workflow list.',
    options: [{ name: 'args', description: '(same as /workflow)', type: 3, required: false }] },

  // ── Layer-38 A2A Agent Naming ───────────────────────────────────────
  { name: 'agents', description: 'List all known A2A agents (local instance + remote endpoints).' },
  { name: 'a2a', description: 'Send an instruction to a named remote agent via A2A.',
    options: [{ name: 'args', description: '<agent-name> <instruction>', type: 3, required: false }] },
  { name: 'a2a-label', description: 'Give a remote agent a friendly name.',
    options: [{ name: 'args', description: '<endpoint-id> <friendly-name>', type: 3, required: true }] },

  // ── ADR-0096 — MCP Plugin Manager ─────────────────────────────────
  { name: 'mcp-install', description: 'Install an MCP tool (npm, github, pip, local).',
    options: [{ name: 'args', description: 'npm:<pkg>[@ver] | github:<o>/<r>[@tag] | pip:<pkg> | local:<path>', type: 3, required: true }] },
  { name: 'mcp-list', description: 'List installed MCP tools and their activation status.' },
  { name: 'mcp-activate', description: 'Activate an MCP tool for this session.',
    options: [{ name: 'args', description: '<tool-id> [session|user|project|tenant]', type: 3, required: true }] },
  { name: 'mcp-status', description: 'Show details of an installed MCP tool.',
    options: [{ name: 'args', description: '<tool-id>', type: 3, required: true }] },
  { name: 'mcp-remove', description: 'Remove an installed MCP tool.',
    options: [{ name: 'args', description: '<tool-id>', type: 3, required: true }] },

  // ── ADR-0253 — Plugin Builder ───────────────────────────────────────
  { name: 'plugin-builder', description: 'Interview-driven plugin design (Idea/Architecture/ADR/Plan + scaffold).',
    options: [{ name: 'args', description: 'status | cancel (omit to start/continue)', type: 3, required: false }] },
];

/**
 * Convert an interaction event back into the equivalent text payload
 * the existing dispatcher in shared/js/in_chat_commands.js expects.
 *
 * Example: `/btw` with args="und auch X bitte" → "/btw und auch X bitte".
 *          `/whoami` with no args              → "/whoami".
 *          `/voice-user-set` with args="learning=2" → "/voice-user-set learning=2".
 */
function interactionToText(interaction) {
  const cmd = interaction.commandName;
  if (!cmd) return '';
  const argsOpt = interaction.options && typeof interaction.options.getString === 'function'
    ? interaction.options.getString('args')
    : null;
  const args = (argsOpt || '').trim();
  return args ? `/${cmd} ${args}` : `/${cmd}`;
}

/**
 * Register the COMMANDS array with Discord. By default uses the global
 * scope; if DISCORD_GUILD_IDS is set, registers per-guild for instant
 * updates (development convenience).
 *
 * Idempotent: `set()` replaces the previous registration with the new
 * array, so re-running this on every boot is safe.
 */
async function registerCommands(client, log) {
  const guildIdsRaw = process.env.DISCORD_GUILD_IDS || '';
  const guildIds = guildIdsRaw.split(',').map(s => s.trim()).filter(Boolean);
  try {
    if (guildIds.length > 0) {
      for (const gid of guildIds) {
        const guild = await client.guilds.fetch(gid);
        await guild.commands.set(COMMANDS);
        log(`registered ${COMMANDS.length} slash-commands for guild ${gid}`);
      }
    } else {
      await client.application.commands.set(COMMANDS);
      log(`registered ${COMMANDS.length} global slash-commands `
        + `(propagation may take up to 1h on first run)`);
    }
  } catch (e) {
    log(`slash-command registration failed: ${e.message || e}`);
    // Don't crash — text-path dispatch still works for users who
    // bypass the picker (e.g. via paste or non-leading-/ tricks).
  }
}

module.exports = { COMMANDS, interactionToText, registerCommands };
