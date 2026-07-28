// in_chat_commands.js — Bridge-overgreifende In-Chat-Commands.
//
// Wed von jedem daemon (whatsapp/telegram/discord/slack) called, BEVOR
// die Message an den Adapter weitergereicht wed. Wenn der Text einer der
// Commands ist (/help, /personas, /persona <name>, /whoami, /skills),
// liefern we einen Reply-Text back und der daemon antwortet sofort —
// without Claude zu involvieren.
//
// daemon-Aufruf:
//   const cmd = inChatCmds.dispatch({
//     text:        "<message>",
//     channel:     "whatsapp",      // oder telegram/discord/slack
//     chatKey:     "<chat_id>",
//     isOwner:     true,            // Owner-Check (siehe unten)
//     settingsFile:"<abs path>",    // bridges/<channel>/settings.json
//   });
//   if (cmd) { await safeSend(..., cmd.reply); continue; }
//
// Owner-Semantik:
//   - WhatsApp: m.key.fromMe (nur eigene devices des Owner-Accounts).
//   - Andere: Whitelist-Match (alle Whitelist-Sender gelten als Owner —
//     Bridge-Whitelist IST das securitys-Modell).
//   Schreib-Commands (`/persona <name>`) erfordern isOwner=true.

const fs = require('fs');
const path = require('path');
// Resolves the operator SOURCE tree, which is where the Python CLIs below live.
// Not `__dirname/..`: a daemon spawned by bridge_manager runs from
// <corvin_home>/bridges/<channel>/ with only shared/js mirrored beside it, so
// `__dirname/..` there is a directory with no Python in it at all. See
// bridge_paths.js::operatorRoot for the full failure mode.
const bridgePaths = require('./bridge_paths');

const HERE = __dirname;
const COWORK_BUNDLE_DIR = path.resolve(HERE, '../../../cowork/personas');
const COWORK_USER_DIR = path.join(
  process.env.COWORK_USER_DIR || path.join(process.env.HOME || '/tmp', '.config/claude-cowork'),
  'personas',
);

function coworkInstalled() {
  return fs.existsSync(COWORK_BUNDLE_DIR);
}

function listPersonas() {
  // Returns [{name, description, zero_config, needs_oauth, needs_keys, _source}]
  // User-Dir shadows Bundle (gleicher Name).
  const seen = new Map();
  const dirs = [
    [COWORK_BUNDLE_DIR, 'bundle'],
    [COWORK_USER_DIR, 'user'],
  ];
  for (const [dir, kind] of dirs) {
    if (!fs.existsSync(dir)) continue;
    let files;
    try { files = fs.readdirSync(dir); } catch { continue; }
    for (const f of files.sort()) {
      if (!f.endsWith('.json')) continue;
      const name = f.replace(/\.json$/, '');
      try {
        const raw = fs.readFileSync(path.join(dir, f), 'utf8');
        const data = JSON.parse(raw);
        if (typeof data !== 'object' || !data) continue;
        data.name = data.name || name;
        data._source = kind;
        seen.set(name, data);  // user wins (kommt nach bundle)
      } catch { /* ignore corrupt persona files */ }
    }
  }
  return [...seen.values()].sort((a, b) => a.name.localeCompare(b.name));
}

function getPersona(name) {
  const all = listPersonas();
  return all.find(p => p.name === name) || null;
}

function readSettings(settingsFile) {
  try { return JSON.parse(fs.readFileSync(settingsFile, 'utf8')); }
  catch { return {}; }
}

function writeSettings(settingsFile, obj) {
  const tmp = settingsFile + '.tmp';
  fs.writeFileSync(tmp, JSON.stringify(obj, null, 2));
  fs.renameSync(tmp, settingsFile);
}

function currentPersonaForChat(settingsFile, chatKey) {
  const s = readSettings(settingsFile);
  const profiles = s.chat_profiles || {};
  const exact = profiles[chatKey];
  if (exact && typeof exact === 'object' && exact.persona) return exact.persona;
  const def = profiles.default;
  if (def && typeof def === 'object' && def.persona) return def.persona;
  return null;
}

function bindPersona(settingsFile, chatKey, persona) {
  const s = readSettings(settingsFile);
  if (!s.chat_profiles || typeof s.chat_profiles !== 'object') s.chat_profiles = {};
  if (!s.chat_profiles[chatKey] || typeof s.chat_profiles[chatKey] !== 'object') {
    s.chat_profiles[chatKey] = {};
  }
  s.chat_profiles[chatKey].persona = persona;
  writeSettings(settingsFile, s);
}

function unbindPersona(settingsFile, chatKey) {
  const s = readSettings(settingsFile);
  const p = s.chat_profiles && s.chat_profiles[chatKey];
  if (!p || typeof p !== 'object' || !('persona' in p)) return false;
  delete p.persona;
  if (Object.keys(p).length === 0) delete s.chat_profiles[chatKey];
  writeSettings(settingsFile, s);
  return true;
}

// ── Audience (per-chat owner-only vs all) ─────────────────────────────────
//
// audience semantics — applied by the daemon's authOk() before the message
// even reaches the inbox:
//   "owner" (default): only senders in the bridge whitelist may talk to the
//                      bot (existing behaviour). Default everywhere.
//   "all":             skip the whitelist for THIS chat — anyone in the
//                      group/channel may interact. Set per chat by the
//                      owner via /all on. Bot/self filters (is_bot,
//                      fromMe, bot_id) still apply, so external bots and
//                      the bot's own echoes never trigger a reply.
function getAudience(settingsFile, chatKey) {
  const s = readSettings(settingsFile);
  const profiles = s.chat_profiles || {};
  const p = profiles[chatKey];
  if (p && typeof p === 'object' && (p.audience === 'all' || p.audience === 'owner')) {
    return p.audience;
  }
  const def = profiles.default;
  if (def && typeof def === 'object' && (def.audience === 'all' || def.audience === 'owner')) {
    return def.audience;
  }
  return 'owner';
}

// Observer visibility (Layer 16, Phase 2 — visibility-vs-capability).
// Default 'off': read-only senders are dropped before the inbox.
// 'transcript': read-only sender messages are written as side-channel
// envelopes into a per-chat ring buffer; the next OWNER turn prepends
// them as a clearly framed context block, then clears the buffer. The
// daemon flow stays gate-first — only owners trigger a claude run.
function getObserverVisibility(settingsFile, chatKey) {
  const s = readSettings(settingsFile);
  const profiles = s.chat_profiles || {};
  const valid = (v) => v === 'transcript' || v === 'off';
  const p = profiles[chatKey];
  if (p && typeof p === 'object' && valid(p.observer_visibility)) {
    return p.observer_visibility;
  }
  const def = profiles.default;
  if (def && typeof def === 'object' && valid(def.observer_visibility)) {
    return def.observer_visibility;
  }
  return 'off';
}

function setAudience(settingsFile, chatKey, mode) {
  if (mode !== 'owner' && mode !== 'all') {
    throw new Error(`invalid audience mode: ${mode}`);
  }
  const s = readSettings(settingsFile);
  if (!s.chat_profiles || typeof s.chat_profiles !== 'object') s.chat_profiles = {};
  if (!s.chat_profiles[chatKey] || typeof s.chat_profiles[chatKey] !== 'object') {
    s.chat_profiles[chatKey] = {};
  }
  s.chat_profiles[chatKey].audience = mode;
  writeSettings(settingsFile, s);
}

// /debug — self-test channel that lets the agent push messages back into
// the messenger via `python3 phase3_cli.py debug send <text>` (gated by
// the per-chat `debug: true` flag set here, plus a 10/min rate limit and
// a CORVIN_DEBUG_DEPTH guard against
// runaway echo loops).
function getDebugEnabled(settingsFile, chatKey) {
  const s = readSettings(settingsFile);
  const profiles = s.chat_profiles || {};
  const p = profiles[chatKey];
  if (p && typeof p === 'object' && p.debug === true) return true;
  return false;
}

function setDebugEnabled(settingsFile, chatKey, enabled) {
  const s = readSettings(settingsFile);
  if (!s.chat_profiles || typeof s.chat_profiles !== 'object') s.chat_profiles = {};
  if (!s.chat_profiles[chatKey] || typeof s.chat_profiles[chatKey] !== 'object') {
    s.chat_profiles[chatKey] = {};
  }
  if (enabled) {
    s.chat_profiles[chatKey].debug = true;
  } else {
    delete s.chat_profiles[chatKey].debug;
    if (Object.keys(s.chat_profiles[chatKey]).length === 0) {
      delete s.chat_profiles[chatKey];
    }
  }
  writeSettings(settingsFile, s);
}

// ── Reply-Builder ─────────────────────────────────────────────────────────

const HELP_TEXT = (currentPersona, channel) => `What you can do in this chat:

AI control:
  /on, /off            turn AI on/off for this chat (WhatsApp) — aliases: /open, /close (SPG)
  /status              is the AI active? (compact context overview: goal, persona, schedule, LDD)
  /stop, /cancel       abort the running task immediately (SIGTERM)
  /btw <text>          inject a follow-up note while the task is still running
                       (e.g.  /btw and please also check the env file)
  /reset, /new, /clear new session context for this chat
  /debug, /debug on/off  show tool calls live
  /all                 audience for this chat (default: owner only)
  /all on              let everyone in this chat talk to the bot
  /all off             back to owner-only (whitelist)
  /settings, /einstellungen, /config
                       one-shot dump of paths + session + system state
  /lang [<code>|clear|list]
                       set/show/clear this chat's reply language (BCP-47),
                       drives voice summaries, audience block, disclosure card

Session Participation Gate (ADR-0166 — owner controls who may interact):
  /invite <uid> [duration]  owner: admit a guest (30s|5m|1h|24h|7d, default: session)
  /uninvite <uid>, /kick <uid>  owner: remove a guest
  /who                  list who's currently admitted

Personas (cowork roles, per chat):
  /personas            list all available cowork personas
  /persona <name>      pin a persona to this chat (e.g. coder, browser, research)
  /persona reset       back to default (coder)
  /whoami, /wer        current persona + tools + LDD config of this chat

Roles & teamwork (Layer 18 — capability bundles):
  /role                your own role + capabilities
  /role <uid>          another user's role (owner/admin only)
  /roles               full chat overview (owner/admin only)
  /grant <uid> <bundle> [<ttl>] [<reason>]
                       owner/admin: delegate authority
                       bundles: admin | member | observer (NOT owner — intrinsic)
                       ttl: 7d default · 30s..30d · "never" for indefinite
                       e.g.  /grant 12345 member 7d "vacation cover"
  /revoke <uid>        owner/admin: drop a granted role
  /leave               give up your own granted role (owners can't — whitelist edit)

Self-onboarding (Layer 19 — bot-disclosure card):
  /join                self-register as observer (read-only-side)
  /pass                acknowledge the disclosure card without taking action

Visibility & consent (Layer 16 Phase 4 — observer transcript):
  /consent on          durable consent (read-only senders only)
  /consent <duration>  time-bounded: 30s | 5m | 1h | 7d (max 30d)
  /consent off         revoke
  /consent status      own consent state
  /consent list        owner: list active consents in this chat
  /consent revoke <uid>  owner: force-revoke
  /share <text>        one-shot: admit a single message
                       (chat must have observer_visibility = "transcript")

Quotas & audit (Layer 20):
  /quota               your own usage (messages + tokens today)
  /quota <uid>         owner/admin: another user's usage
  /quota all           owner/admin: list all users in chat
  /quota set <uid> <msgs|keep|clear> <tokens|keep|clear>
                       owner/admin: per-(chat, uid) override (use "keep" to skip a field,
                       "clear" to revert to bundle default)
  /quota reset <uid>   owner/admin: zero the rolling-24h counters
  /audit               same as /audit me 20
  /audit me [<n>] [<prefix>]    your own events (last N, optional event_type prefix)
  /audit chat [<n>] [<prefix>]  owner/admin: chat-wide events

Curated proposal stack (Layer 21 — collect, sieve, then go):
  /propose <text>      add an idea to the chat's proposal stack (anyone)
                       (read-only-side too — visibility lasts until /go)
  /proposals           owner/admin: list current stack
  /proposal rm <id>    owner/admin: drop one entry
  /proposal clear      owner/admin: empty stack without triggering
  /go [steering]       owner/admin: atomic consume + trigger AI with stack
                       e.g.  /go fasse die Vorschläge zusammen

Operator elevation (Layer 16 — destructive MCP gate):
  /auth-up <pin>       owner: short-lived elevation for forge_promote / skill_promote
  /auth-down           owner: revoke elevation early
  /auth-status         owner: show TTL remaining

Schedule reminders:
  /schedule list                     show pending tasks for this chat
  /schedule add <when>::<text>       e.g.  /schedule add in 1h::standup ping
                                     or    /schedule add 0 9 * * 1-5::weekday morning brief
  /schedule rm <id>                  remove a task by id

User profile (bridge-wide, injected into every reply):
  /profile show                      print current profile
  /profile set <key>=<value>         e.g.  /profile set name=Silvio
                                     or    /profile set tone="concise, du-form"
  /profile get <key>
  /profile rm <key>
  /profile reset                     wipe the whole profile

Voice listener-profile (steers HOW the read-aloud explains things):
  /voice-user-show                   show the active listener profile
  /voice-user-set <k>=<v>            level | jargon | style | background |
                                     metaphors | domains | learning
                                     e.g.  /voice-user-set level=expert
                                           /voice-user-set jargon=4
                                           /voice-user-set learning=2
  /voice-user-clear                  remove the listener profile
  /voice-user-preview [de|en]        preview the audience block
  /voice-user-help                   field-by-field reference

Long-term memory (lazy-loaded by Claude when relevant):
  /memory list                       all topics with one-line summaries
  /memory show <topic>               full body of a topic
  /memory write <topic> <text>       create/overwrite a topic
  /memory append <topic> <text>      append to an existing topic
  /memory forget <topic>             delete a topic

Secrets vault (tool-gated, audit-logged):
  /vault list                        inventory only — no values
  /vault get <name>                  fetch a value (auditable)
  /vault set <name>=<value>          add/overwrite an item
                                     flags:  --kind X  --tags a,b
                                             --locked  --encrypted
  /vault unlock <name>               open a locked item for 5 min
  /vault forget <name>               remove an item
  /vault audit [N]                   last N audit-log lines

LDD (loss-driven development) layer toggles:
  /ldd-status                        master + per-layer state + cascade hints
  /ldd-on, /ldd-off                  global default-on / kill-switch
  /ldd-set <layer> <on|off>          per-layer toggle
  /ldd-preset <name>                 default | strict | quick | off
  /dialectic-on, /dialectic-off      dialectic decision-points global toggle
  /dialectic-status                  show modes + thresholds + counters
  /dialectic-set <site> <fast|skill|cli|off>
  /dialectic-show on|off             reply-footer toggle

Delegation, workflows & objectives:
  /engine [name]        owner: pin which worker CLI runs delegated work
                       (claude | codex | opencode | hermes | copilot — see
                       /engine aliases); omit to show. NOT the native/acs/tde
                       delegation mode: that is one operator setting
                       (Settings → AI Engines) and has no chat command.
  /workflow list|show <n>|validate <n>|run <n> [k=v …]|help
                       AWP workflow bridge (Layer 26 — DAG + delegation)
  /workflows            alias for /workflow list
  /goal [<text>|show|clear]
                       sticky session objective injected into every LLM turn
  /objective list|add <high|medium|low> <text>|...
                       owner: user-defined learning objectives (ADR-0163)

Compliance (GDPR / EU AI Act):
  /privacy, /datenschutz  information right (GDPR Art. 13/14)
  /decision-review, /decision  admin: human-oversight review (EU AI Act Art. 14)

Personal tools (Layer 27 — your permanent me.* forge library):
  /tools                 list your saved tools
  /tool save <name> [as <alias>]  save the most recent forge tool
  /tool rm|show <name>   delete / inspect a saved tool

Process & resource control (Phase 3 layers):
  /ps [-a]             active claude sessions (process table)
  /kill <id> [-9]      terminate a session (SIGTERM / SIGKILL)
  /sig <id> <SIGNAL>   send custom signal: KILL | PLAN | SUMMARIZE | CONTEXT_DROP | QUIET | RESUME
  /nice <id> <prio>    adjust session priority
  /pipe list|create|write|read|rm|meta   inter-session pipes
  /svc list|deps       service manager view
  /budget show|policy  context budget for this chat

A2A agent network (Layer 38 — agent-to-agent):
  /agents              list all known agents (local + remote)
  /a2a <name> <msg>    send an instruction to a named remote agent
  /a2a-label <id> <name>  give an agent a friendly name (e.g. "Hetzner Server")
  Set this agent's name: corvin-instance-id label "MyName"

Plugin Builder (ADR-0253 — interview-driven plugin design):
  /plugin-builder       start (or continue) an interview; ends in an Idea Doc,
                       Architecture Concept, ADR, Build Plan and a code scaffold
  /plugin-builder status  show the active interview's current phase + question
  /plugin-builder cancel  stop the interview, write nothing

Diagnostics:
  /skills              what can I do (in this persona)?
  /welcome, /willkommen, /start, /hi
                       Corvin intro card (text + voice-note on WhatsApp)
  /help, /hilfe        this overview

Currently in ${channel}/${currentPersona || 'coder (default)'}.

Two role systems coexist:
  • cowork persona (/persona, /whoami)  — what KIND of agent runs (coder, browser…)
  • capability bundle (/role, /grant)    — WHO may trigger it (owner/admin/member/observer)

Tip: anything that isn't a slash command goes straight to Claude. You can
write "find me the cheapest train to Munich" — and the right persona handles it.

Full multi-user reference: docs/rights-and-teamwork.md`;

function helpReply(ctx) {
  const cur = currentPersonaForChat(ctx.settingsFile, ctx.chatKey);
  return HELP_TEXT(cur, ctx.channel);
}

function personasReply(_ctx) {
  if (!coworkInstalled()) {
    return '⚠️ cowork-Plugin nicht installiert. Nur Default-Coder available.\n' +
           'Install: claude plugin install cowork@corvin-voice-local';
  }
  const all = listPersonas();
  if (all.length === 0) return 'Keine Personas gefunden.';
  const lines = ['🎭 Available Personas:\n'];
  for (const p of all) {
    const ready = p.zero_config ? '✓' : '⚙️';
    const desc = (p.description || '').split('\n')[0];
    lines.push(`${ready} ${p.name} — ${desc}`);
  }
  lines.push('');
  lines.push('✓ = sofort nutzbar    ⚙️ = braucht User-Setup (siehe README)');
  lines.push('Wechseln: /persona <name>');
  return lines.join('\n');
}

function personaReply(ctx, args) {
  if (!coworkInstalled()) {
    return '⚠️ cowork-Plugin nicht installiert.';
  }
  if (!ctx.isOwner) {
    return '🔒 Nur der Owner darf Personas wechseln.';
  }
  const arg = (args || '').trim();
  if (!arg) {
    const cur = currentPersonaForChat(ctx.settingsFile, ctx.chatKey);
    return `Aktuelle Rolle: ${cur || 'coder (default)'}\nWechseln: /persona <name>\nListe: /personas`;
  }
  if (arg === 'reset' || arg === 'off' || arg === 'default') {
    const removed = unbindPersona(ctx.settingsFile, ctx.chatKey);
    return removed
      ? '✓ Persona-Bindung removed. Default-Coder ist wieder aktiv.'
      : 'Dieser Chat hatte keine Persona-Bindung — Default-Coder bleibt aktiv.';
  }
  const persona = getPersona(arg);
  if (!persona) {
    const known = listPersonas().map(p => p.name).join(', ');
    return `❓ Persona "${arg}" nicht gefunden.\nVerfügbar: ${known}`;
  }
  bindPersona(ctx.settingsFile, ctx.chatKey, arg);
  let reply = `✓ Dieser Chat ist jetzt: ${arg}\n${persona.description || ''}`;
  if (!persona.zero_config) {
    const oa = (persona.needs_oauth || []).join(', ');
    const keys = (persona.needs_keys || []).join(', ');
    reply += `\n\n⚙️ Setup-Note: braucht ${oa ? 'OAuth=' + oa : ''}${oa && keys ? ' / ' : ''}${keys ? 'keys=' + keys : ''}.`;
    if (persona.setup_hint) reply += `\n${persona.setup_hint}`;
  }
  reply += '\n\nNächste message in diesem Chat geht over die neue Rolle.';
  return reply;
}

function whoamiReply(ctx) {
  const cur = currentPersonaForChat(ctx.settingsFile, ctx.chatKey) || 'coder';
  const persona = getPersona(cur);
  const lines = [`🪪 Rolle: ${cur}`];
  if (persona) {
    if (persona.description) lines.push(persona.description);
    const tools = (persona.allowed_tools || []);
    if (tools.length) lines.push('Tools: ' + tools.slice(0, 8).join(', ') + (tools.length > 8 ? '…' : ''));
    const mcps = Object.keys(persona.mcp_servers || {});
    if (mcps.length) lines.push('MCP: ' + mcps.join(', '));
    const dirs = persona.add_dirs || [];
    if (dirs.length) lines.push('Workspace: ' + dirs.join(', '));
    // Layer-14 effective LDD profile snapshot. Surface only when the
    // persona actually declares an LDD configuration (preset / layers /
    // master) — otherwise the line would just be noise on personas that
    // inherit the global default. We summarise to keep `/whoami` short.
    const lddSummary = formatPersonaLddSummary(persona);
    if (lddSummary) lines.push(lddSummary);
  } else {
    lines.push('default behaviour: bypass-permissions, alle Tools.');
  }
  lines.push(`Channel: ${ctx.channel} · Chat: ${ctx.chatKey}`);
  return lines.join('\n');
}

function formatPersonaLddSummary(persona) {
  const preset   = persona.ldd_preset;
  const layers   = (persona.ldd_layers && typeof persona.ldd_layers === 'object') ? persona.ldd_layers : null;
  const enabled  = (typeof persona.ldd_enabled === 'boolean') ? persona.ldd_enabled : null;
  if (!preset && !layers && enabled === null) return null;
  const parts = ['LDD: '];
  if (preset)            parts.push('preset=' + preset);
  if (enabled === true)  parts.push((preset ? ' ' : '') + 'master=on');
  if (enabled === false) parts.push((preset ? ' ' : '') + 'master=off');
  if (layers) {
    const onLayers  = Object.entries(layers).filter(([, v]) => v === true).map(([k]) => k);
    const offLayers = Object.entries(layers).filter(([, v]) => v === false).map(([k]) => k);
    if (onLayers.length)  parts.push('  +' + onLayers.join(','));
    if (offLayers.length) parts.push('  -' + offLayers.join(','));
  }
  return parts.join('').trim();
}

function skillsReply(ctx) {
  // Hier könnten we die Slash-Skills von Claude Code listen, aber die
  // hängen von der Persona ab und sind im daemon nicht available.
  // Stattdessen: zeige In-Chat-Commands (statisch) + Persona-Tools (dynamisch).
  const cur = currentPersonaForChat(ctx.settingsFile, ctx.chatKey) || 'coder';
  const persona = getPersona(cur);
  const lines = ['🛠 Was du jetzt machen kannst:\n'];
  lines.push('In-Chat-Commands:');
  lines.push('  /help · /personas · /persona <name> · /whoami · /skills');
  lines.push('  /on · /off · /status · /stop · /btw · /reset · /debug');
  lines.push('');
  lines.push(`Aktuelle Rolle: ${cur}`);
  if (persona) {
    if (persona.allowed_tools && persona.allowed_tools.length) {
      lines.push('Tools: ' + persona.allowed_tools.join(', '));
    } else {
      lines.push('Tools: alle (bypass-permissions)');
    }
    if (persona.mcp_servers && Object.keys(persona.mcp_servers).length) {
      lines.push('MCP-Server: ' + Object.keys(persona.mcp_servers).join(', '));
    }
    if (persona.append_system) {
      lines.push('System-Prompt-Fokus: ' + persona.append_system.split('\n')[0].slice(0, 140));
    }
  }
  lines.push('');
  lines.push('Alles andere (kein Slash-Command) geht direkt an Claude.');
  return lines.join('\n');
}

// ── Dispatcher ────────────────────────────────────────────────────────────

const HELP_TRIGGERS = new Set(['/help', '/hilfe', '/cowork', '/?']);
const PERSONAS_TRIGGERS = new Set(['/personas', '/rollen']);
// `/role` was previously an alias for /whoami; with Layer-18 it owns the
// capability-bundle role lookup. /whoami / /wer keep the persona meaning.
const WHOAMI_TRIGGERS = new Set(['/whoami', '/wer']);
const SKILLS_TRIGGERS = new Set(['/skills', '/was', '/abilities']);
const RESET_TRIGGERS = new Set(['/new', '/clear', '/reset']);
const WELCOME_TRIGGERS = new Set(['/welcome', '/willkommen', '/start', '/hi']);

const WELCOME_TEXT_DE = `🦅 *CorvinOS — Voice to Action*

Sprich. Dein System handelt.

CorvinOS ist kein Chatbot — es ist dein persönliches KI-Betriebssystem. Es verbindet deine Agenten, Tools, Kanäle und Daten zu einem System, das für dich handelt — auf jedem Kanal, in deiner Sprache.

🌐 *corvin-labs.com* — alle Infos, Demo & Preise

*Was CorvinOS für dich erledigt:*
• *Ein System, alle Kanäle:* WhatsApp, Discord, Telegram, Slack, Mail — ein Assistent, ein gemeinsames Gedächtnis, überall.
• *Experten auf Abruf:* Coder, Researcher, Postfach-Manager — du nennst den Namen, sie übernehmen.
• *Agenten, die zusammenarbeiten:* Sie übergeben sich Aufgaben und koordinieren sich selbst. Du gibst nur das Ziel vor.
• *Tools, die sich selbst bauen:* CorvinOS erstellt bei Bedarf eigene, sichere Werkzeuge — direkt aus deiner Anfrage.
• *Mitten in deinem Alltag:* Musik, Mails, Termine, Dateien, Datenbanken — alles im Chat, ohne App-Wechsel.
• *Deine Stimme zählt:* Sprach-Eingabe, Sprach-Antwort — auf jedem Gerät.
• *Deine Daten, dein Schutz:* nach EU AI Act und DSGVO gebaut — lückenloses Audit, kein Kleingedrucktes.

*Loslegen:* /help für alle Befehle · einfach schreiben oder sprechen → CorvinOS handelt
*Mehr erfahren:* https://corvin-labs.com`;

const WELCOME_TEXT_EN = `🦅 *CorvinOS — Voice to Action*

Speak. Your system acts.

CorvinOS isn't a chatbot — it's your personal AI operating system. It connects your agents, tools, channels and data into one system that gets things done for you — on every channel, in your language.

🌐 *corvin-labs.com* — info, demo & pricing

*What CorvinOS does for you:*
• *One system, every channel:* WhatsApp, Discord, Telegram, Slack, email — one assistant, one shared memory, everywhere.
• *Experts on call:* coder, researcher, inbox manager — name one and it takes over.
• *Agents that work together:* they hand off tasks and coordinate themselves. You just set the goal.
• *Tools that build themselves:* CorvinOS forges its own safe tools on demand, straight from your request.
• *Woven into your day:* music, email, meetings, files, databases — all in chat, no app-switching.
• *Your voice counts:* voice input, voice replies — on any device.
• *Your data, your protection:* built to EU AI Act and GDPR — full audit trail, no fine print.

*Get started:* /help for all commands · just type or speak → CorvinOS acts
*Learn more:* https://corvin-labs.com`;

function welcomeReply(ctx) {
  // Channel-aware light-touch tweak: WhatsApp formatting uses *bold*, Telegram /
  // Slack render markdown differently, but the bold-stars degrade gracefully on
  // every channel. No per-channel fork needed.
  const lang = (ctx && typeof ctx.lang === 'string' && ctx.lang.toLowerCase().startsWith('en'))
    ? 'en'
    : 'de';
  return lang === 'en' ? WELCOME_TEXT_EN : WELCOME_TEXT_DE;
}

// Path to the helper CLIs — resolved relative to this file so it works
// independent of where the daemon is launched from. (`path` is already
// required at the top of this file.)
const { spawnSync } = require('child_process');
const SCHEDULE_CLI = bridgePaths.voiceScript('schedule_cli.py');
const PROFILE_CLI  = bridgePaths.voiceScript('profile_cli.py');
// i18n — `/lang` slash-command. Validates BCP-47 codes via i18n.normalise()
// and persists to profile.display_language. Tiny wrapper, intentionally
// separate from /profile so the user-facing command stays terse.
const LANG_CLI     = bridgePaths.voiceScript('lang_cli.py');
const MEMORY_CLI   = bridgePaths.voiceScript('memory_cli.py');
const VAULT_CLI    = bridgePaths.voiceScript('vault_cli.py');
const SESSION_RESET_CLI = bridgePaths.bridgeSharedPy('session_reset.py');
const DIALECTIC_CLI = bridgePaths.bridgeSharedPy('dialectic.py');
// Layer-29 companion — per-chat worker-engine preference. Slash-command
// `/engine` shells out to engine_switch.py so the JS layer never has to
// re-implement the alias resolver / validator / audit emit. The Python
// module is the single source of truth.
const ENGINE_SWITCH_CLI = bridgePaths.bridgeSharedPy('engine_switch.py');
const LDD_CLI = bridgePaths.bridgeSharedPy('ldd.py');
// Layer-17/18/19/20 unified Phase-3 CLI wrapper (process table, pipe
// registry, service manager, context budget — all in one entry point).
const PHASE3_CLI = bridgePaths.bridgeSharedPy('phase3_cli.py');
// Layer-17 — per-user observer-transcript consent gate. CLI wraps the
// shared/consent.py module; reachable from read-only senders too via
// dispatchReadOnlyConsent() so a Mitleser can grant consent without
// having owner privileges.
const CONSENT_CLI = bridgePaths.bridgeSharedPy('consent.py');
// Layer-18 — capability-bundle role system (owner / admin / member /
// observer). CLI wraps shared/roles.py; reachable from owner + admin
// via /grant /revoke /role /roles /leave. Identity is bound to the
// caller's platform uid (ctx.uid) the same way consent does it.
const ROLES_CLI = bridgePaths.bridgeSharedPy('roles.py');
// Layer-19 — bot-disclosure card + /join self-service. CLI wraps
// shared/disclosure.py; /join + /pass are reachable from read-only-side
// senders via dispatchReadOnlyDisclosure(), the daemon should call it
// alongside dispatchReadOnlyConsent before maybeForwardAsObserver.
const DISCLOSURE_CLI = bridgePaths.bridgeSharedPy('disclosure.py');
// Layer-20 — quota + audit-view. /quota for self + owner/admin operator
// flows; /audit me / /audit chat for capability-gated visibility into
// the unified hash chain.
const QUOTA_CLI = bridgePaths.bridgeSharedPy('quota.py');
const AUDIT_VIEW_CLI = bridgePaths.bridgeSharedPy('audit_view.py');
// Layer-21 — curated proposal stack. /propose adds, /proposals lists,
// /proposal rm + /proposal clear curate, /go consumes + triggers LLM.
const PROPOSAL_CLI = bridgePaths.bridgeSharedPy('proposal.py');

// Session goal — /goal [<text>|clear]. Stored per chat, injected into every LLM turn.
const GOAL_CLI = bridgePaths.bridgeSharedPy('goal.py');

// ULO (ADR-0163 M1) — /objective and /objectives slash commands.
const ULO_CLI = bridgePaths.bridgeSharedPy('ulo.py');
// ADR-0007 multi-tenant axis: ULO objectives are per-tenant. The CLI does NOT
// read the env (no env-fallback by design) — the caller must pass the tenant
// explicitly so the writer (these CLI calls) and the reader (the injection path
// in adapter.py, which uses CORVIN_TENANT_ID) resolve to the SAME store. Without
// this, non-default tenants wrote to _default and objectives never injected
// (cross-tenant leak; security review 2026-06-27). Defaults to "_default", which
// matches the injection path's own default, so single-tenant installs are
// unchanged.
const _uloTenantArgs = () => ['--tenant-id', process.env.CORVIN_TENANT_ID || '_default'];

// Layer-38 A2A agent naming — /agents and /a2a <name> <instruction>.
const A2A_CLI = bridgePaths.voiceScript('corvin_a2a.py');

// ADR-0073 G-010 / G-016 — decision registry + privacy notice.
const DECISION_REGISTRY_CLI = bridgePaths.bridgeSharedPy('decision_registry.py');

// ADR-0166 — Session Participation Gate (SPG).
const SPG_CLI = bridgePaths.bridgeSharedPy('spg.py');

// Layer-27 personal tools — /tool save / /tool rm / /tools.
// Operator-side curation of the user's permanent me.* forge library.
const PERSONAL_TOOLS_CLI = bridgePaths.bridgeSharedPy('personal_tools.py');

// `/settings` (aliases /einstellungen, /config) — single-message dump
// of paths, session-scoped config, and system-scoped config. Aggregator
// lives in bridges/shared/settings_view.py; the CLI sub-command is
// `render <channel> <chat_key> [--uid <uid>] [--lang de|en]`.
const SETTINGS_CLI = bridgePaths.bridgeSharedPy('settings_view.py');

// Layer-26 — AWP workflow bridge. The python package lives at
// core/workflows/corvin_workflows; PYTHONPATH must point at the
// plugin root so `python3 -m corvin_workflows ...` resolves. Sub-commands:
//   list                           — list bundled workflows
//   show <name>                    — dump the resolved YAML
//   validate <name>                — run R1..R10
//   run <name> [key=value ...]     — execute (Stub-Engine MVP, no LLM cost)
const WORKFLOWS_PKG_ROOT = path.resolve(
  __dirname, '..', '..', '..', '..', 'corvin-workflows'
);

// Minimal POSIX-shell-style splitter. Honours single and double quotes so
// `/workflow schedule add "0 9 * * *" news_sentiment_research ticker=NVDA`
// keeps the cron expression as one token. Backslash escapes are not
// supported — overkill for the slash-command surface.
function _shellSplit(s) {
  const out = [];
  let cur = '';
  let quote = null;  // null | "'" | '"'
  for (let i = 0; i < s.length; i += 1) {
    const c = s[i];
    if (quote) {
      if (c === quote) { quote = null; continue; }
      cur += c;
    } else if (c === '"' || c === "'") {
      quote = c;
    } else if (/\s/.test(c)) {
      if (cur) { out.push(cur); cur = ''; }
    } else {
      cur += c;
    }
  }
  if (cur) out.push(cur);
  return out;
}

// Layer-16 v2 — PIN-Elevation. The JS side wraps auth_elevation.js; the
// Python pre-tool-hook reads the same store and gates destructive MCP tools
// (forge_promote, skill_promote). chat_key in the store mirrors the
// adapter's CORVIN_CHANNEL_ID —
// `<bridge>:<sanitized chat_key>` — so the hook and the slash-command see
// the same identifier.
const _authElevation = (() => {
  try { return require('./auth_elevation'); }
  catch (_) { return null; }
})();

function _channelId(channel, chatKey) {
  // Mirror adapter._build_spawn_env: '/' and '\' are replaced with '_'.
  const safe = String(chatKey || '').replace(/[\\\/]/g, '_');
  return `${channel || ''}:${safe}`;
}

function profileReply(ctx, sub, rest) {
  // sub: "show" (default) | "get" | "set" | "rm" | "unset" | "reset"
  const s = (sub || 'show').toLowerCase();
  const args = ['python3', PROFILE_CLI, s];
  if (rest) {
    // Pass the rest as a single argument so phrases like
    // `tone=concise, du-form` survive without quoting weirdness.
    args.push(rest);
  }
  const r = spawnSync(args[0], args.slice(1), { encoding: 'utf8' });
  const out = (r.stdout || '') + (r.stderr ? '\n' + r.stderr : '');
  return { reply: out.trim() || '(no output)', kind: 'profile' };
}

// Layer-12 listener-profile commands — convenience wrapper around the
// existing profile_cli.py. The short keys (level, jargon, style,
// background, metaphors, domains) map onto the canonical
// voice_audience_<key> entries in profile.json, so the underlying storage
// stays unified with the rest of the bridge profile.
const VOICE_USER_KEYS = ['level', 'jargon', 'style', 'background', 'metaphors', 'domains', 'learning', 'chat_render'];
const VOICE_USER_HELP = `Voice listener-profile commands:

  /voice-user-show              show the active listener profile
  /voice-user-set <k>=<v>       set one of: ${VOICE_USER_KEYS.join(', ')}
  /voice-user-clear             remove the listener profile
  /voice-user-preview [de|en]   preview the audience block as it would land in the prompt

Field meanings:
  level=novice|intermediate|expert         comprehension depth
  jargon=0..5                              0 = translate every term, 5 = keep all jargon
  style=concise|verbose|example-driven     how detailed the read-aloud is
  background=<free text, ≤200 chars>       e.g. "Senior Go-Dev, neu in React"
  metaphors=on|off                         allow analogies in explanations
  domains=python,postgres,...              comma-list where jargon may stay untranslated
  learning=0..3                            0 = off, 1 = brief gloss, 2 = teach a concept, 3 = teach + recap
  chat_render=on|off                       off (default) = audience block is TTS-only; on = also append to chat-text reply

The profile lives in ~/.config/corvin-voice/profile.json under
voice_audience_* keys. It steers HOW the voice summarizer translates
cryptic content (stack traces, CLI output) for the listener; it never
overrides the faithfulness or completeness rules in the base prompt.`;

function voiceUserShow() {
  const r = spawnSync('python3', [PROFILE_CLI, 'show'], { encoding: 'utf8' });
  const lines = (r.stdout || '').split('\n');
  const audience = lines.filter(l => /voice_audience_/.test(l));
  if (audience.length === 0) {
    return '(no listener profile set yet — try `/voice-user-set level=expert`)';
  }
  // Strip the "voice_audience_" prefix and the leading "  " indent for readability.
  const pretty = audience.map(l => l.replace(/^\s*voice_audience_/, '  '));
  return 'Listener profile (layer 12):\n' + pretty.join('\n');
}

function voiceUserSet(rest) {
  if (!rest) return 'Usage: /voice-user-set <key>=<value>\n\n' + VOICE_USER_HELP;
  const eq = rest.indexOf('=');
  if (eq === -1) return 'Error: expected `key=value`. Example: /voice-user-set level=expert';
  const key = rest.slice(0, eq).trim().toLowerCase();
  const val = rest.slice(eq + 1).trim();
  if (!VOICE_USER_KEYS.includes(key)) {
    return `Error: unknown key "${key}". Allowed: ${VOICE_USER_KEYS.join(', ')}`;
  }
  const fullKey = 'voice_audience_' + key;
  const r = spawnSync('python3', [PROFILE_CLI, 'set', `${fullKey}=${val}`], { encoding: 'utf8' });
  const out = (r.stdout || '') + (r.stderr ? '\n' + r.stderr : '');
  return out.trim() || '(no output)';
}

function voiceUserClear() {
  // Only report fields that were actually set — profile_cli.py rm prints
  // "Removed" unconditionally, so we have to gate on `get` first.
  const removed = [];
  for (const k of VOICE_USER_KEYS) {
    const fullKey = 'voice_audience_' + k;
    const probe = spawnSync('python3', [PROFILE_CLI, 'get', fullKey], { encoding: 'utf8' });
    const present = !((probe.stdout || '').includes('(no value set'));
    if (!present) continue;
    spawnSync('python3', [PROFILE_CLI, 'rm', fullKey], { encoding: 'utf8' });
    removed.push(k);
  }
  if (removed.length === 0) return '(listener profile was already empty)';
  return 'Cleared listener-profile fields: ' + removed.join(', ');
}

function voiceUserPreview(rest) {
  // rest may be "de" or "en" — default "de".
  const lang = (rest || 'de').toLowerCase().slice(0, 2);
  // profile.py lives at bridges/shared/profile.py (one level up from
  // bridges/shared/js/ where this file is).
  const SHARED = path.resolve(__dirname, '..');
  const py = `import sys; sys.path.insert(0, "${SHARED}"); ` +
             `import profile; sys.stdout.write(profile.for_tts_audience("${lang === 'en' ? 'en' : 'de'}"))`;
  const r = spawnSync('python3', ['-c', py], { encoding: 'utf8' });
  const out = (r.stdout || '').trim();
  if (!out) return '(listener profile empty — nothing to preview)';
  return `AUDIENCE block (lang=${lang}):\n\n${out}`;
}

function voiceUserReply(ctx, sub, rest) {
  switch ((sub || '').toLowerCase()) {
    case 'show':    return { reply: voiceUserShow(),         kind: 'voice-user' };
    case 'set':     return { reply: voiceUserSet(rest),      kind: 'voice-user' };
    case 'clear':   return { reply: voiceUserClear(),        kind: 'voice-user' };
    case 'preview': return { reply: voiceUserPreview(rest),  kind: 'voice-user' };
    case 'help':
    case '':        return { reply: VOICE_USER_HELP,         kind: 'voice-user' };
    default:        return { reply: `Unknown sub-command: /voice-user-${sub}\n\n${VOICE_USER_HELP}`, kind: 'voice-user' };
  }
}

function vaultReply(ctx, sub, rest) {
  // Defense-in-depth: the vault holds BYOK secrets. Never rely solely on the
  // dispatch path being owner-only — guard explicitly so an SPG-admitted guest
  // (ctx.isOwner === false) can never read/modify secrets even if a future
  // call site forgets to gate (security review 2026-06-27).
  if (!ctx.isOwner) {
    return { reply: '🔒 /vault is owner-only — secret storage is a privileged operation.', kind: 'vault-denied' };
  }
  // sub: "list" (default) | "get" | "set" | "unlock" | "forget" | "rm" | "audit"
  const s = (sub || 'list').toLowerCase();
  const args = ['python3', VAULT_CLI, s];
  // For most sub-commands the rest of the line goes through verbatim,
  // but `set` accepts CLI flags and a `name=value` token, so we shell-split
  // it cheaply (single-pass). Quotes around values are preserved.
  if (rest) {
    if (s === 'set' || s === 'audit' || s === 'unlock') {
      // Naive split on whitespace, but keep quoted segments together.
      const parts = rest.match(/(?:"[^"]*"|'[^']*'|[^\s])+/g) || [];
      for (const p of parts) {
        // Strip surrounding quotes that we kept above.
        const u = (p.startsWith('"') && p.endsWith('"')) ||
                  (p.startsWith("'") && p.endsWith("'"))
          ? p.slice(1, -1) : p;
        args.push(u);
      }
    } else {
      args.push(rest);
    }
  }
  // Surface the chat id to the audit log via env var so we don't pollute
  // the user-facing CLI signature.
  const env = { ...process.env, VAULT_AUDIT_SOURCE: `${ctx.channel || '?'}/${ctx.chatKey || '?'}` };
  const r = spawnSync(args[0], args.slice(1), { encoding: 'utf8', env });
  const out = (r.stdout || '') + (r.stderr ? '\n' + r.stderr : '');
  return { reply: out.trim() || '(no output)', kind: 'vault' };
}

// i18n — /lang slash-command implementation.
// Tail can be empty (= show), a BCP-47 code, "clear"/"reset", or "list".
// The Python CLI returns JSON; we render a friendly line per case.
function langReply(ctx, tail) {
  const t = (tail || '').trim();
  // Determine current language so we can render the reply in it (so a
  // user who is on `de` keeps seeing German confirmations).
  const showRes = spawnSync('python3', [LANG_CLI, 'show'],
                            { encoding: 'utf8', timeout: 5000 });
  let currentCode = 'en';
  try { currentCode = (JSON.parse(showRes.stdout || '{}').code) || 'en'; }
  catch { currentCode = 'en'; }

  if (!t) {
    // /lang → show
    let j; try { j = JSON.parse(showRes.stdout || '{}'); } catch { j = {}; }
    if (!j.ok) return { reply: '/lang: failed to read profile', kind: 'lang-error' };
    const key = j.set ? 'lang.current' : 'lang.current_unset';
    return { reply: _tString(key, currentCode, { name: j.name, code: j.code }),
             kind: j.set ? 'lang-show' : 'lang-show-unset' };
  }

  const low = t.toLowerCase();
  if (low === 'clear' || low === 'reset' || low === 'off') {
    spawnSync('python3', [LANG_CLI, 'clear'], { encoding: 'utf8', timeout: 5000 });
    return { reply: _tString('lang.cleared', currentCode), kind: 'lang-clear' };
  }
  if (low === 'list') {
    const r = spawnSync('python3', [LANG_CLI, 'list'],
                        { encoding: 'utf8', timeout: 5000 });
    let j; try { j = JSON.parse(r.stdout || '{}'); } catch { j = {}; }
    const codes = (j.codes || []).map(c => `  ${c.code} — ${c.name}`).join('\n');
    return { reply: _tString('lang.list_header', currentCode) + '\n' + codes,
             kind: 'lang-list' };
  }
  if (low === 'help' || low === '?') {
    return { reply: _tString('lang.usage', currentCode), kind: 'lang-help' };
  }

  // Anything else → try to set it
  const r = spawnSync('python3', [LANG_CLI, 'set', t],
                      { encoding: 'utf8', timeout: 5000 });
  let j; try { j = JSON.parse(r.stdout || '{}'); } catch { j = {}; }
  if (!j.ok) {
    if (j.reason === 'unknown') {
      return { reply: _tString('lang.unknown', currentCode, { raw: j.raw || t }),
               kind: 'lang-unknown' };
    }
    return { reply: `/lang failed: ${(r.stderr || r.stdout || '').slice(0, 200)}`,
             kind: 'lang-error' };
  }
  // Render the success message in the NEW language so the user gets
  // immediate visual confirmation that the switch took.
  return { reply: _tString('lang.set_ok', j.code,
                           { name: j.name, code: j.code }),
           kind: 'lang-set' };
}

// Look up a UI string by key in the i18n bundles. We use a tiny embedded
// resolver here instead of a full Python round-trip to keep the
// slash-command latency in the millisecond range.
function _tString(key, lang, fmt) {
  fmt = fmt || {};
  const bundleDir = path.join(bridgePaths.operatorRoot(), 'voice', 'i18n');
  const candidates = [String(lang || 'en')];
  const base = String(lang || 'en').split('-')[0];
  if (base !== candidates[0]) candidates.push(base);
  if (!candidates.includes('en')) candidates.push('en');
  for (const c of candidates) {
    let bundle;
    try {
      bundle = JSON.parse(require('fs').readFileSync(
        path.join(bundleDir, `${c}.json`), 'utf8'));
    } catch { continue; }
    const parts = key.split('.');
    let cur = bundle;
    for (const p of parts) {
      if (!cur || typeof cur !== 'object') { cur = null; break; }
      cur = cur[p];
    }
    if (typeof cur === 'string') {
      return cur.replace(/\{(\w+)\}/g, (m, k) => fmt[k] !== undefined ? String(fmt[k]) : m);
    }
  }
  return key;
}

function memoryReply(ctx, sub, rest) {
  // sub: "list" (default) | "show <t>" | "write <t> <text>" | "append <t> <text>" | "forget <t>"
  const s = (sub || 'list').toLowerCase();
  const args = ['python3', MEMORY_CLI, s];
  if (s === 'show' || s === 'read' || s === 'forget' || s === 'rm' || s === 'delete') {
    if (rest) args.push(rest);
  } else if (s === 'write' || s === 'set' || s === 'append') {
    // First token is the topic, the rest is the body. Split here so the CLI
    // sees a clean (topic, text...) argv.
    const sp = (rest || '').search(/\s/);
    if (sp === -1) {
      args.push(rest || '');
    } else {
      args.push(rest.slice(0, sp));
      args.push(rest.slice(sp + 1).trim());
    }
  }
  const r = spawnSync(args[0], args.slice(1), { encoding: 'utf8' });
  const out = (r.stdout || '') + (r.stderr ? '\n' + r.stderr : '');
  return { reply: out.trim() || '(no output)', kind: 'memory' };
}

function allReply(ctx, arg) {
  // /all                — show current audience for this chat
  // /all on  | open     — open this chat to everyone (owner-only action)
  // /all off | owner    — restrict back to whitelist (owner-only action)
  const a = (arg || '').trim().toLowerCase();
  if (a === '' || a === 'status' || a === 'show') {
    const cur = getAudience(ctx.settingsFile, ctx.chatKey);
    return { reply: cur === 'all'
      ? 'Audience: all — everyone in this chat may talk to the bot.\nLock back: /all off'
      : 'Audience: owner — only the whitelist may talk to the bot.\nOpen up: /all on',
      kind: 'all' };
  }
  if (!ctx.isOwner) {
    return { reply: 'Only the owner can change audience for this chat.', kind: 'all' };
  }
  let mode;
  if (a === 'on' || a === 'all' || a === 'open') mode = 'all';
  else if (a === 'off' || a === 'owner' || a === 'lock' || a === 'close') mode = 'owner';
  else {
    return { reply: `Unknown /all argument: ${arg}\nTry: /all  ·  /all on  ·  /all off`, kind: 'all' };
  }
  setAudience(ctx.settingsFile, ctx.chatKey, mode);
  return { reply: mode === 'all'
    ? '✓ Audience: all — anyone in this chat can now talk to the bot.\nBot-self/external-bot loops are still filtered out.'
    : '✓ Audience: owner — only the whitelist may talk to the bot now.',
    kind: 'all' };
}

function debugReply(ctx, arg) {
  // /debug                — show current debug state for this chat
  // /debug on             — enable: lets phase3_cli.py debug send write to outbox
  // /debug off            — disable
  // /debug status         — alias for ''
  const a = (arg || '').trim().toLowerCase();
  if (a === '' || a === 'status' || a === 'show') {
    const cur = getDebugEnabled(ctx.settingsFile, ctx.chatKey);
    return {
      reply: cur
        ? '🐛 Debug: ON — Claude Code may push messages back to this chat for self-testing (10/min rate limit, depth-3 cap, audited).\nDisable: /debug off'
        : '🐛 Debug: OFF — bot replies only to your messages.\nEnable: /debug on',
      kind: 'debug',
    };
  }
  if (!ctx.isOwner) {
    return { reply: 'Only the owner can toggle debug.', kind: 'debug' };
  }
  if (a === 'on' || a === 'enable') {
    setDebugEnabled(ctx.settingsFile, ctx.chatKey, true);
    return {
      reply: '✓ Debug: ON.\nClaude can now self-test by writing outbox messages directly:\n  python3 operator/bridges/shared/phase3_cli.py debug send "<text>"\nGuards: 10 messages / 60 s, depth-3 cap (CORVIN_DEBUG_DEPTH), audit-logged as bridge.debug_message.',
      kind: 'debug',
    };
  }
  if (a === 'off' || a === 'disable') {
    setDebugEnabled(ctx.settingsFile, ctx.chatKey, false);
    return { reply: '✓ Debug: OFF — direct outbox writes blocked again.',
             kind: 'debug' };
  }
  return { reply: `Unknown /debug argument: ${arg}\nTry: /debug  ·  /debug on  ·  /debug off`,
           kind: 'debug' };
}


function resetReply(ctx) {
  // Wipes everything bound to this chat: skills (canonical + slot mirror),
  // forge tools, the session workspace dir, and the voice state. Audit-first
  // so the action is always traceable. Idempotent — re-running on an
  // already-reset chat is fine.
  const args = [
    'python3', SESSION_RESET_CLI,
    '--channel', String(ctx.channel || ''),
    '--chat-id', String(ctx.chatKey || ''),
    '--reason', 'manual',
  ];
  // 30 s wall-clock cap covers slow disks + small skill churn; the
  // reset itself is normally sub-second.
  const r = spawnSync(args[0], args.slice(1), {
    encoding: 'utf8', timeout: 30000,
  });
  if (r.error || r.status !== 0) {
    const err = (r.stderr && r.stderr.trim()) || (r.error && r.error.message) || `exit ${r.status}`;
    return { reply: `session reset failed: ${err.slice(0, 400)}`, kind: 'reset-error' };
  }
  let out;
  try {
    out = JSON.parse((r.stdout || '').trim());
  } catch (e) {
    return { reply: `session reset: malformed JSON (${e.message || e})\n${(r.stdout || '').slice(0, 400)}`, kind: 'reset-error' };
  }
  const lines = ['Session reset.'];
  lines.push(`- skills removed: ${out.skills_removed} (slot mirrors: ${out.slot_mirrors_removed})`);
  lines.push(`- forge tools removed: ${out.forge_tools_removed}`);
  lines.push(`- voice state cleared: ${out.voice_state_removed ? 'yes' : 'no'}`);
  if (out.audit_event_id) {
    lines.push(`- audit event: ${out.audit_event_type} (${String(out.audit_event_id).slice(0, 16)})`);
  } else {
    lines.push(`- audit event: ${out.audit_event_type} (audit chain unavailable)`);
  }
  if (Array.isArray(out.failures) && out.failures.length) {
    lines.push(`- ${out.failures.length} non-fatal warning(s): ${out.failures.slice(0, 2).join(' | ')}`);
  }
  lines.push('');
  lines.push('Project files in this chat\'s session dir are kept; only Claude\'s memory was cleared.');
  return { reply: lines.join('\n'), kind: 'reset' };
}

function scheduleReply(ctx, sub, rest) {
  // sub: "list" | "add" | "rm" | empty (= list this chat)
  const s = (sub || 'list').toLowerCase();
  const args = ['python3', SCHEDULE_CLI, s];
  if (s === 'list') {
    args.push(ctx.channel || '', String(ctx.chatKey || ''));
  } else if (s === 'add') {
    args.push(ctx.channel || '', String(ctx.chatKey || ''),
              String(ctx.chatKey || ''), rest || '');
  } else if (s === 'rm') {
    args.push(rest || '');
  } else {
    return { reply: `Unknown /schedule sub-command: ${sub}\n` +
                    `Try: /schedule list | /schedule add <when>::<text> | /schedule rm <id>`,
             kind: 'schedule-error' };
  }
  // spawnSync because we want to return a synchronous reply text.
  const r = spawnSync(args[0], args.slice(1), { encoding: 'utf8' });
  const out = (r.stdout || '') + (r.stderr ? '\n' + r.stderr : '');
  return { reply: out.trim() || '(no output)', kind: 'schedule' };
}

// /objective — User-Defined Learning Objectives (ADR-0163 M1).
// Sub-commands: add <priority> <text> | list | pause <id> | resume <id> | delete <id>
function objectiveReply(ctx, sub, rest) {
  const chan = String(ctx.channel || '');
  const chat = String(ctx.chatKey || '');

  // Mutating subcommands steer the shared per-chat system prompt (ULO injection),
  // so they are owner-only — an SPG-admitted guest must not be able to add/alter
  // learning objectives (least-privilege + injection vector; security review
  // 2026-06-27). `list` stays readable for any admitted participant.
  const _mutating = sub && !['list'].includes(String(sub).toLowerCase());
  if (_mutating && !ctx.isOwner) {
    return { reply: '🔒 /objective changes are owner-only — learning objectives steer the shared chat behaviour.', kind: 'objective-denied' };
  }

  if (!sub || sub === 'list') {
    const r = spawnSync('python3', [ULO_CLI, ..._uloTenantArgs(), 'list', chan, chat], { encoding: 'utf8', timeout: 5000 });
    let obj;
    try { obj = JSON.parse(r.stdout || '{}'); } catch { return { reply: '⚠️ Failed to load objectives.', kind: 'objective_list' }; }
    if (!obj || !obj.ok) return { reply: `⚠️ ${(obj && obj.error) || 'Unknown error'}`, kind: 'objective_list' };
    if (!obj.objectives || obj.objectives.length === 0) {
      return {
        reply: '**Learning Objectives** — none set\n\nAdd one with `/objective add <high|medium|low> <text>`',
        kind: 'objective_list',
      };
    }
    const lines = [`**Learning Objectives** (${obj.active_count}/${obj.count} active)\n`];
    for (const o of obj.objectives) {
      const status = o.active ? '✅' : '⏸';
      const cr = o.compliance_rate != null ? `  ${Math.round(o.compliance_rate * 100)}% compliant` : '';
      lines.push(`${status} \`${o.id}\` **[${o.priority}]** ${o.text}${cr}`);
    }
    lines.push('\n_Use `/objective pause <id>` · `/objective resume <id>` · `/objective delete <id>`_');
    return { reply: lines.join('\n'), kind: 'objective_list' };
  }

  if (sub === 'add') {
    const parts = rest ? rest.trim().split(/\s+/) : [];
    if (parts.length < 2) {
      return {
        reply: '**Usage:** `/objective add <high|medium|low> <your objective text>`\n_Example:_ `/objective add high Always reply in German`',
        kind: 'objective_add',
      };
    }
    const priority = parts[0].toLowerCase();
    const text = parts.slice(1).join(' ');
    const r = spawnSync('python3', [ULO_CLI, ..._uloTenantArgs(), 'add', chan, chat, priority, text], { encoding: 'utf8', timeout: 5000 });
    let obj;
    try { obj = JSON.parse(r.stdout || '{}'); } catch { return { reply: '⚠️ Failed to add objective.', kind: 'objective_add' }; }
    if (!obj || !obj.ok) return { reply: `⚠️ ${(obj && obj.error) || 'Unknown error'}`, kind: 'objective_add' };
    return {
      reply: `✅ Objective added: \`${obj.objective.id}\`\n> ${obj.objective.text}`,
      kind: 'objective_add',
    };
  }

  if (sub === 'pause' || sub === 'resume') {
    const id = rest ? rest.trim().split(/\s+/)[0] : '';
    if (!id) return { reply: `**Usage:** \`/objective ${sub} <id>\``, kind: `objective_${sub}` };
    const r = spawnSync('python3', [ULO_CLI, ..._uloTenantArgs(), sub, chan, chat, id], { encoding: 'utf8', timeout: 5000 });
    let obj;
    try { obj = JSON.parse(r.stdout || '{}'); } catch { return { reply: `⚠️ Failed to ${sub} objective.`, kind: `objective_${sub}` }; }
    if (!obj || !obj.ok) return { reply: `⚠️ ${(obj && obj.error) || 'Unknown error'}`, kind: `objective_${sub}` };
    const icon = sub === 'pause' ? '⏸' : '▶️';
    return { reply: `${icon} Objective \`${id}\` ${sub}d.`, kind: `objective_${sub}` };
  }

  if (sub === 'delete' || sub === 'rm') {
    const id = rest ? rest.trim().split(/\s+/)[0] : '';
    if (!id) return { reply: '**Usage:** `/objective delete <id>`', kind: 'objective_delete' };
    const r = spawnSync('python3', [ULO_CLI, ..._uloTenantArgs(), 'delete', chan, chat, id], { encoding: 'utf8', timeout: 5000 });
    let obj;
    try { obj = JSON.parse(r.stdout || '{}'); } catch { return { reply: '⚠️ Failed to delete objective.', kind: 'objective_delete' }; }
    if (!obj || !obj.ok) return { reply: `⚠️ ${(obj && obj.error) || 'Unknown error'}`, kind: 'objective_delete' };
    return { reply: `🗑️ Objective \`${id}\` deleted.`, kind: 'objective_delete' };
  }

  const help = [
    '**Learning Objectives — help**\n',
    '`/objective list`              — show all objectives',
    '`/objective add <pri> <text>` — add (priority: high/medium/low)',
    '`/objective pause <id>`        — temporarily disable',
    '`/objective resume <id>`       — re-enable',
    '`/objective delete <id>`       — permanently remove',
  ].join('\n');
  return { reply: help, kind: 'objective_help' };
}

// /status — quick session context overview: goal, persona, scheduled tasks, LDD, engine.
function statusReply(ctx) {
  const lines = [];

  // Active goal
  const gr = spawnSync('python3', [GOAL_CLI, 'get',
                                    String(ctx.channel || ''), String(ctx.chatKey || '')],
                        { encoding: 'utf8', timeout: 5000 });
  let goal = null;
  try { const j = JSON.parse(gr.stdout); if (j.ok && j.goal) goal = j.goal; } catch {}
  lines.push(`🎯 **Goal:** ${goal ? `\`${goal}\`` : '_(none set — use /goal <text>)_'}`);

  // Active persona
  const persona = currentPersonaForChat(ctx.settingsFile, ctx.chatKey) || 'assistant';
  lines.push(`👾 **Persona:** \`${persona}\``);

  // Scheduled tasks for this chat
  const sr = spawnSync('python3', [SCHEDULE_CLI, 'list',
                                    String(ctx.channel || ''), String(ctx.chatKey || '')],
                        { encoding: 'utf8', timeout: 5000 });
  const schedOut = (sr.stdout || '').trim();
  lines.push(`📅 **Schedule:** ${schedOut || '_(no scheduled tasks)_'}`);

  // LDD status (compact)
  const lr = spawnSync('python3', [LDD_CLI, 'status'], { encoding: 'utf8', timeout: 5000 });
  const lddOut = (lr.stdout || '').trim().split('\n')[0] || 'unknown';
  lines.push(`🔁 **LDD:** ${lddOut}`);

  // ULO — active learning objectives (M5)
  const ur = spawnSync('python3', [ULO_CLI, ..._uloTenantArgs(), 'list',
                                    String(ctx.channel || ''), String(ctx.chatKey || '')],
                        { encoding: 'utf8', timeout: 5000 });
  let uloLine = '_(none — use /objective add)_';
  try {
    const uj = JSON.parse(ur.stdout || '{}');
    if (uj && uj.ok && uj.active_count > 0) {
      uloLine = `${uj.active_count} active (${uj.count - uj.active_count} paused) — /objectives to manage`;
    }
  } catch {}
  lines.push(`🎓 **Objectives:** ${uloLine}`);

  return { reply: '**Session Status**\n\n' + lines.join('\n'), kind: 'status' };
}

function lddReply(ctx, sub, rest) {
  // sub: "" (status) | "on" | "off" | "status" | "set" | "preset"
  // /ldd-on/off/status are direct heads; /ldd-set <layer> <on|off>
  // and /ldd-preset <name> carry sub as the rest.
  const args = ['python3', LDD_CLI];
  if (sub) args.push(sub);
  if (rest) {
    const parts = rest.split(/\s+/).filter(Boolean);
    args.push(...parts);
  }
  const r = spawnSync(args[0], args.slice(1), { encoding: 'utf8' });
  const out = (r.stdout || '') + (r.stderr ? '\n' + r.stderr : '');
  return { reply: out.trim() || '(no output)', kind: 'ldd' };
}

function authReply(ctx, sub, rest) {
  // Layer-16 v2 — PIN-Elevation. Three sub-commands:
  //   up <pin>  grant elevation (10 min default) for destructive MCP tools
  //   down      drop elevation immediately
  //   status    show TTL or "not elevated"
  if (!_authElevation) {
    return { reply: 'auth-elevation module unavailable (forge plugin missing?)',
             kind: 'auth-error' };
  }
  if (!ctx.isOwner) {
    return { reply: 'Only the owner may use /auth-up/down/status.',
             kind: 'auth-denied' };
  }
  const chatKey = _channelId(ctx.channel, ctx.chatKey);
  const settings = readSettings(ctx.settingsFile);
  const settingsPin = settings && settings.pin ? String(settings.pin) : null;

  switch ((sub || '').toLowerCase()) {
    case 'up': {
      const pin = (rest || '').trim();
      if (!pin) {
        return { reply: 'Usage: /auth-up <pin>\n\nThe PIN lives in this channel\'s settings.json.\nElevation lasts 10 minutes (then auto-revoke).',
                 kind: 'auth-up' };
      }
      const result = _authElevation.grant({
        chatKey, pin, settingsPin,
      });
      if (result.ok) {
        const ttl = _authElevation.remainingTtl(chatKey);
        return { reply: `✓ Elevation granted for ${Math.floor(ttl/60)} min ${ttl%60}s.\nDestructive MCP tools (forge_promote, skill_promote) are unlocked.\n/auth-down to revoke early.`,
                 kind: 'auth-up' };
      }
      const why = result.reason === 'no-pin-configured'
        ? 'No PIN is configured for this bridge.\nSet `"pin": "<your-pin>"` in this channel\'s settings.json (then no restart needed; mtime hot-reload).'
        : `Wrong PIN.\nThe PIN is set in this channel's settings.json (\`"pin"\` field).`;
      return { reply: `✗ Elevation refused: ${why}`, kind: 'auth-up-denied' };
    }
    case 'down':
    case 'revoke':
    case 'off': {
      const existed = _authElevation.revoke(chatKey);
      return { reply: existed
        ? '✓ Elevation revoked.'
        : '(no elevation was active)',
        kind: 'auth-down' };
    }
    case 'status':
    case '': {
      const elevated = _authElevation.isElevated(chatKey);
      const ttl = _authElevation.remainingTtl(chatKey);
      if (!elevated) {
        return { reply: 'Not elevated.\nUse /auth-up <pin> to unlock destructive MCP tools (forge_promote, skill_promote) for ~10 min.',
                 kind: 'auth-status' };
      }
      const m = Math.floor(ttl/60), s = ttl%60;
      return { reply: `✓ Elevated — ${m} min ${s}s remaining.\n/auth-down to revoke now.`,
               kind: 'auth-status' };
    }
    default:
      return { reply: `Unknown /auth sub-command: ${sub}\nUse /auth-up <pin>, /auth-down, /auth-status.`,
               kind: 'auth-error' };
  }
}

// ─── Layer-17 — per-user observer-transcript consent gate ──────────────
//
// Owner side (ctx.isOwner === true): /consent list, /consent status [<uid>],
//   /consent revoke <uid>, /consent help. Owners are never themselves the
//   target of the consent gate (the gate triggers in the read-only path),
//   so /consent on / off / <duration> redirect to the help text.
//
// Read-only side (dispatchReadOnlyConsent): the daemon calls this BEFORE
//   maybeForwardAsObserver, so a read-only sender can grant their own
//   consent or admit a single message via /share <text> without ever
//   hitting the silent-drop path. ctx.uid MUST be set by the daemon.

const CONSENT_OWNER_HELP = [
  '/consent commands (owner side):',
  '',
  '  /consent                  this help',
  '  /consent list             list active consents in this chat',
  '  /consent status <uid>     query a specific user\'s status',
  '  /consent revoke <uid>     force-revoke (e.g. when an observer leaves)',
  '',
  'Read-only senders manage their OWN consent via:',
  '  /consent on               durable consent until they /consent off',
  '  /consent <duration>       time-bounded (30s, 5m, 1h, 7d; max 30d)',
  '  /consent off              revoke',
  '  /consent status           own status',
  '  /share <text>             one-shot opt-in for this single message',
  '',
  'Storage: <corvin_home>/global/consent/<channel>__<chat>.json',
  'Audit: every grant/revoke/drop lands in the unified hash chain.',
].join('\n');

const CONSENT_READONLY_HELP = [
  '/consent commands (read-only senders):',
  '',
  '  /consent on               durable consent — your messages will be',
  '                            buffered for the owner\'s next turn',
  '  /consent <duration>       time-bounded: 30s, 5m, 1h, 7d (max 30d)',
  '  /consent off              revoke',
  '  /consent status           show your current status',
  '',
  '  /share <text>             one-shot: admit just this single message,',
  '                            no standing consent stored',
  '',
  'Without consent your messages are silently dropped — that\'s the default.',
].join('\n');

function _runConsentCli(args) {
  const r = spawnSync('python3', [CONSENT_CLI, ...args], {
    encoding: 'utf8', timeout: 5000,
  });
  return {
    stdout: r.stdout || '',
    stderr: r.stderr || '',
    status: r.status,
    error: r.error,
  };
}

function _formatConsentStatus(stdout) {
  let j;
  try { j = JSON.parse(stdout); }
  catch { return `Consent status: (malformed CLI output)\n${stdout.slice(0, 300)}`; }
  if (!j.granted) {
    return `Consent: not granted (reason: ${j.reason || 'no-entry'}).`;
  }
  if (j.mode === 'durable') {
    return 'Consent: ✓ durable (until /consent off).';
  }
  if (j.mode === 'time_bounded') {
    const m = Math.floor((j.remaining_s || 0) / 60);
    const s = (j.remaining_s || 0) % 60;
    return `Consent: ✓ time-bounded — ${m} min ${s}s remaining.`;
  }
  return `Consent: ${j.reason || j.mode || '?'}.`;
}

function consentReply(ctx, sub, rest) {
  const channel = String(ctx.channel || '');
  const chatKey = String(ctx.chatKey || '');
  const subL = (sub || '').toLowerCase();

  if (!subL || subL === 'help') {
    return { reply: CONSENT_OWNER_HELP, kind: 'consent-help' };
  }
  if (subL === 'list') {
    const r = _runConsentCli(['list', channel, chatKey]);
    if (r.status !== 0) {
      return { reply: `consent list failed: ${(r.stderr || r.stdout).slice(0, 300)}`,
               kind: 'consent-error' };
    }
    let j;
    try { j = JSON.parse(r.stdout || '{}'); }
    catch (e) {
      return { reply: `consent list parse error: ${e.message || e}\n${r.stdout.slice(0, 300)}`,
               kind: 'consent-error' };
    }
    if (!j.count) return { reply: '(no active consents in this chat)', kind: 'consent-list' };
    const lines = [`Active consents in this chat (${j.count}):`];
    for (const [uid, e] of Object.entries(j.entries || {})) {
      if (e.mode === 'durable') {
        const when = e.granted_at ? new Date(e.granted_at * 1000).toISOString().slice(0, 16).replace('T', ' ') : '?';
        lines.push(`  ${uid} — durable (granted ${when} via ${e.granted_via || '?'})`);
      } else if (e.mode === 'time_bounded') {
        const remaining = Math.max(0, Math.floor((e.expires_at || 0) - Date.now() / 1000));
        const m = Math.floor(remaining / 60);
        const s = remaining % 60;
        lines.push(`  ${uid} — ${m}m${s}s left (via ${e.granted_via || '?'})`);
      } else {
        lines.push(`  ${uid} — ${e.mode || 'unknown'}`);
      }
    }
    return { reply: lines.join('\n'), kind: 'consent-list' };
  }
  if (subL === 'status') {
    const target = (rest || '').trim();
    if (!target) {
      return { reply: 'Usage: /consent status <uid>\n(Owners do not have their own consent state — the gate is for read-only senders.)',
               kind: 'consent-error' };
    }
    const r = _runConsentCli(['status', channel, chatKey, target]);
    if (r.status !== 0) {
      return { reply: `consent status failed: ${(r.stderr || r.stdout).slice(0, 300)}`,
               kind: 'consent-error' };
    }
    return { reply: _formatConsentStatus(r.stdout), kind: 'consent-status' };
  }
  if (subL === 'revoke') {
    if (!ctx.isOwner) {
      return { reply: 'Only the owner can force-revoke another user.',
               kind: 'consent-denied' };
    }
    const target = (rest || '').trim();
    if (!target) {
      return { reply: 'Usage: /consent revoke <uid>', kind: 'consent-error' };
    }
    const r = _runConsentCli(['off', channel, chatKey, target]);
    if (r.status !== 0) {
      return { reply: `consent revoke failed: ${(r.stderr || r.stdout).slice(0, 300)}`,
               kind: 'consent-error' };
    }
    let j;
    try { j = JSON.parse(r.stdout || '{}'); }
    catch { return { reply: `consent revoke: ${r.stdout.slice(0, 300)}`, kind: 'consent-revoke' }; }
    return { reply: j.existed
      ? `✓ Consent revoked for ${target}.`
      : `(no active consent was on file for ${target})`,
      kind: 'consent-revoke' };
  }
  // Owner typed /consent on / off / <duration> — gentle redirect.
  if (['on', 'off', 'yes', 'no', 'ja', 'nein', 'true', 'false', 'always'].includes(subL)
      || /^\d+[smhd]$/i.test(subL)) {
    return {
      reply: 'Owners are not subject to the consent gate — these forms are for read-only senders only.\n\n' + CONSENT_OWNER_HELP,
      kind: 'consent-info',
    };
  }
  return { reply: `Unknown /consent sub-command: ${sub}\n\n${CONSENT_OWNER_HELP}`,
           kind: 'consent-error' };
}

// Daemon-side hook: ctx must carry { text, channel, chatKey, uid, settingsFile }.
// Returns:
//   null                      — text is not /consent or /share
//   { reply, kind, ... }      — daemon should send `reply` to the user
//   { admitShare, sharePayload, reply, kind } — daemon should ALSO write a
//                                              one-shot `_observer + _share`
//                                              envelope with `sharePayload`.
function dispatchReadOnlyConsent(ctx) {
  if (!ctx || typeof ctx.text !== 'string') return null;
  const raw = ctx.text.trim();
  if (!raw.startsWith('/')) return null;

  const channel = String(ctx.channel || '');
  const chatKey = String(ctx.chatKey || '');
  const uid = String(ctx.uid || '');

  // /share <text> — one-shot opt-in. Mirrors consent.py SHARE_PREFIX_RE.
  const shareMatch = raw.match(/^\/share(?:\s+([\s\S]+))?\s*$/i);
  if (shareMatch) {
    if (!uid) {
      return { reply: '/share: cannot identify your uid — daemon bug.',
               kind: 'consent-share-error' };
    }
    const payload = (shareMatch[1] || '').trim();
    if (!payload) {
      return { reply: 'Usage: /share <text>\nAdmits a single message into the owner\'s next turn without granting standing consent.\nFor durable consent: /consent on (or /consent 1h).',
               kind: 'consent-share-help' };
    }
    // /share only makes sense in chats that have the chat-level transcript
    // flag turned on — otherwise the envelope would be buffered but never
    // consumed at adapter-side. Hint instead of silent admit.
    let mode = 'off';
    try { mode = getObserverVisibility(ctx.settingsFile, chatKey) || 'off'; }
    catch { mode = 'off'; }
    if (mode !== 'transcript') {
      return { reply: 'This chat does not enable observer-transcript — /share has nowhere to land.\nThe owner needs to set `observer_visibility = "transcript"` in this chat\'s profile first.',
               kind: 'consent-share-no-transcript' };
    }
    return {
      kind: 'consent-share-admit',
      admitShare: true,
      sharePayload: payload,
      reply: `✓ /share admitted (one-shot, ${payload.length} chars).`,
    };
  }

  // /consent <args>
  if (!/^\/consent(\s|$)/i.test(raw)) return null;
  const tail = raw.replace(/^\/consent/i, '').trim();
  const tailLower = tail.toLowerCase();

  if (!uid) {
    return { reply: '/consent: cannot identify your uid — daemon bug.',
             kind: 'consent-error' };
  }
  if (!tail || tailLower === 'help') {
    return { reply: CONSENT_READONLY_HELP, kind: 'consent-help' };
  }
  if (tailLower === 'status') {
    const r = _runConsentCli(['status', channel, chatKey, uid]);
    if (r.status !== 0) {
      return { reply: `consent status failed: ${(r.stderr || r.stdout).slice(0, 300)}`,
               kind: 'consent-error' };
    }
    return { reply: _formatConsentStatus(r.stdout), kind: 'consent-status' };
  }
  if (['off', 'no', 'nein', 'false', 'revoke'].includes(tailLower)) {
    const r = _runConsentCli(['off', channel, chatKey, uid]);
    if (r.status !== 0) {
      return { reply: `consent off failed: ${(r.stderr || r.stdout).slice(0, 300)}`,
               kind: 'consent-error' };
    }
    let j;
    try { j = JSON.parse(r.stdout || '{}'); }
    catch { return { reply: `consent off: ${r.stdout.slice(0, 300)}`, kind: 'consent-off' }; }
    return { reply: j.existed
      ? '✓ Consent revoked. Future messages will be dropped again.'
      : '(no active consent was on file)',
      kind: 'consent-off' };
  }
  // /consent on, /consent yes, or /consent <duration>.
  const isDurable = ['on', 'yes', 'ja', 'true', 'always'].includes(tailLower);
  const args = ['on', channel, chatKey, uid];
  if (!isDurable) args.push(tail);
  const r = _runConsentCli(args);
  if (r.status !== 0) {
    return { reply: `consent on failed: ${(r.stderr || r.stdout).slice(0, 400)}`,
             kind: 'consent-error' };
  }
  let j;
  try { j = JSON.parse(r.stdout || '{}'); }
  catch (e) {
    return { reply: `consent on: parse error ${e.message || e}\n${r.stdout.slice(0, 300)}`,
             kind: 'consent-error' };
  }
  if (!j.ok) {
    const hint = j.hint ? '\n' + j.hint : '';
    return { reply: `✗ ${j.error || 'unknown error'}${hint}`,
             kind: 'consent-error' };
  }
  if (j.mode === 'durable') {
    return { reply: '✓ Consent granted (durable).\nYour messages now flow into the owner\'s next turn until you /consent off.',
             kind: 'consent-on' };
  }
  return { reply: `✓ Consent granted (${j.ttl_human || 'time-bounded'}).\nYour messages flow into the owner\'s next turn until expiry or /consent off.`,
           kind: 'consent-on' };
}

// ─── Layer-18 — capability-bundle roles (owner/admin/member/observer) ───
//
// Five slash-commands. The dispatcher passes ctx.uid (set by every daemon
// from the bridge protocol) so identity binding stays platform-enforced —
// the caller cannot grant on someone else's behalf.

function _runRolesCli(args) {
  const r = spawnSync('python3', [ROLES_CLI, ...args], {
    encoding: 'utf8', timeout: 5000,
  });
  return {
    stdout: r.stdout || '',
    stderr: r.stderr || '',
    status: r.status,
    error: r.error,
  };
}

function _callerRole(ctx) {
  // Resolve the caller's effective role via the CLI. Returns one of
  // 'owner' / 'admin' / 'member' / 'observer' / 'none' / 'unknown'.
  // Falls back to 'owner' when ctx.isOwner is true and uid is missing
  // (legacy daemons that haven't started passing ctx.uid yet) — keeps
  // the owner code-path working during the daemon-update window.
  if (!ctx.uid) return ctx.isOwner ? 'owner' : 'unknown';
  const r = _runRolesCli(['role',
    String(ctx.channel || ''),
    String(ctx.chatKey || ''),
    String(ctx.uid)]);
  if (r.status !== 0) return 'unknown';
  try { return JSON.parse(r.stdout).role || 'unknown'; }
  catch { return 'unknown'; }
}

function _splitTail(tail) {
  // Splits "<first> <rest...>" → [first, rest]. Same shape as the existing
  // /schedule / /profile dispatchers use.
  const sp = (tail || '').search(/\s/);
  if (sp === -1) return [(tail || '').trim(), ''];
  return [tail.slice(0, sp).trim(), tail.slice(sp + 1).trim()];
}

const ROLES_HELP_OWNER = [
  '/grant <uid> <bundle> [<ttl>] [<reason>]',
  '    bundle: admin | member | observer',
  '    ttl   : 7d (default) / 1h / 30s / never',
  '    reason: free text (≤200 chars)',
  '/revoke <uid>          drop a granted role',
  '/role                  your own role + capabilities',
  '/role <uid>            another user\'s role',
  '/roles                 list everyone in this chat',
  '/leave                 give up your own granted role (owners cannot)',
  '',
  'Authority rules:',
  '  owner  → may grant admin, member, observer (not owner)',
  '  admin  → may grant member, observer (not admin)',
  '  member → may not grant; may /leave',
  '  observer → may not grant; may /leave',
  '',
  'Storage: <corvin_home>/global/roles/<channel>__<chat>.json',
  'Audit  : every grant/revoke/leave/denial in the unified hash chain.',
].join('\n');

const ROLES_HELP_USER = [
  '/role             show your own role + capabilities',
  '/leave            give up your granted role',
  '',
  'Owners and admins also have:',
  '  /grant <uid> <bundle> [<ttl>] [<reason>]',
  '  /revoke <uid>',
  '  /roles            list everyone in this chat',
].join('\n');

function _formatRoleStatus(j) {
  // Pretty-render the JSON status object from `roles.py role`.
  const lines = [];
  if (j.intrinsic_owner) {
    lines.push(`Role: owner (intrinsic — on the channel whitelist)`);
  } else if (j.role === 'none') {
    lines.push(`Role: none — no granted role in this chat.`);
  } else {
    lines.push(`Role: ${j.role}`);
    if (j.bundle) lines.push(`  bundle    : ${j.bundle}`);
    if (j.granted_by) lines.push(`  granted by: ${j.granted_by}`);
    if (j.granted_at) {
      const when = new Date(j.granted_at * 1000).toISOString().slice(0, 16).replace('T', ' ');
      lines.push(`  granted at: ${when}`);
    }
    if (j.expires_at) {
      const m = Math.floor((j.remaining_s || 0) / 60);
      const h = Math.floor(m / 60);
      const remain = h ? `${h}h${m % 60}m` : `${m}m`;
      lines.push(`  expires in: ${remain}`);
    } else if (j.bundle) {
      lines.push(`  expires   : never`);
    }
    if (j.reason) lines.push(`  reason    : ${j.reason}`);
  }
  if (Array.isArray(j.capabilities) && j.capabilities.length) {
    lines.push(`Capabilities: ${j.capabilities.join(', ')}`);
  }
  return lines.join('\n');
}

function rolesRoleReply(ctx, tail) {
  // /role           → caller's own status
  // /role <uid>     → another user's status (owner/admin can see anyone;
  //                  members and observers can only see themselves)
  const target = (tail || '').trim();
  const callerRole = _callerRole(ctx);

  if (!target) {
    if (!ctx.uid) {
      return { reply: '/role: cannot identify your uid — daemon must pass ctx.uid.', kind: 'role-error' };
    }
    const r = _runRolesCli(['role',
      String(ctx.channel || ''), String(ctx.chatKey || ''), String(ctx.uid)]);
    if (r.status !== 0) {
      return { reply: `role lookup failed: ${(r.stderr || r.stdout).slice(0, 300)}`, kind: 'role-error' };
    }
    let j; try { j = JSON.parse(r.stdout); }
    catch { return { reply: `role parse error\n${r.stdout.slice(0, 300)}`, kind: 'role-error' }; }
    return { reply: _formatRoleStatus(j), kind: 'role' };
  }

  // /role <uid> — privileged lookup
  if (callerRole !== 'owner' && callerRole !== 'admin' && target !== ctx.uid) {
    return { reply: 'Only owner/admin may inspect another user\'s role.', kind: 'role-denied' };
  }
  const r = _runRolesCli(['role',
    String(ctx.channel || ''), String(ctx.chatKey || ''), target]);
  if (r.status !== 0) {
    return { reply: `role lookup failed: ${(r.stderr || r.stdout).slice(0, 300)}`, kind: 'role-error' };
  }
  let j; try { j = JSON.parse(r.stdout); }
  catch { return { reply: `role parse error\n${r.stdout.slice(0, 300)}`, kind: 'role-error' }; }
  const lines = [`Status of ${target}:`, _formatRoleStatus(j)];
  return { reply: lines.join('\n'), kind: 'role' };
}

function rolesRolesReply(ctx) {
  const callerRole = _callerRole(ctx);
  if (callerRole !== 'owner' && callerRole !== 'admin') {
    return { reply: 'Only owner/admin may list all roles in this chat.', kind: 'roles-denied' };
  }
  const r = _runRolesCli(['roles',
    String(ctx.channel || ''), String(ctx.chatKey || '')]);
  if (r.status !== 0) {
    return { reply: `roles list failed: ${(r.stderr || r.stdout).slice(0, 300)}`, kind: 'roles-error' };
  }
  let j; try { j = JSON.parse(r.stdout); }
  catch { return { reply: `roles parse error\n${r.stdout.slice(0, 300)}`, kind: 'roles-error' }; }
  const lines = ['Roles in this chat:'];
  const owners = j.intrinsic_owners || [];
  if (owners.length) {
    lines.push('Owners (whitelist):');
    for (const o of owners) lines.push(`  ${o}`);
  }
  const granted = j.granted || {};
  const keys = Object.keys(granted);
  if (keys.length) {
    lines.push('Granted roles:');
    for (const uid of keys) {
      const e = granted[uid];
      let suffix = '';
      if (e.expires_at) {
        const remaining = Math.max(0, Math.floor((e.expires_at) - Date.now() / 1000));
        const m = Math.floor(remaining / 60);
        const h = Math.floor(m / 60);
        suffix = h ? ` (${h}h${m % 60}m left)` : ` (${m}m left)`;
      } else {
        suffix = ' (no expiry)';
      }
      const reason = e.reason ? `  — ${e.reason}` : '';
      lines.push(`  ${uid} → ${e.bundle}${suffix}${reason}`);
    }
  } else {
    lines.push('Granted roles: (none — only intrinsic owners present)');
  }
  return { reply: lines.join('\n'), kind: 'roles' };
}

function rolesGrantReply(ctx, tail) {
  // /grant <uid> <bundle> [<ttl>] [<reason...>]
  if (!ctx.uid) {
    return { reply: '/grant: cannot identify your uid — daemon must pass ctx.uid.', kind: 'grant-error' };
  }
  if (!tail || !tail.trim()) {
    return { reply: 'Usage: /grant <uid> <bundle> [<ttl>] [<reason>]\n\n' + ROLES_HELP_OWNER, kind: 'grant-help' };
  }

  // Parse: <uid> <bundle> [<ttl>] [<reason words...>]
  const parts = tail.trim().split(/\s+/);
  if (parts.length < 2) {
    return { reply: 'Usage: /grant <uid> <bundle> [<ttl>] [<reason>]', kind: 'grant-error' };
  }
  const targetUid = parts[0];
  const bundle = parts[1];
  let ttl = null;
  let reasonStart = 2;
  if (parts.length >= 3 && /^(\d+[smhd]|never|forever|inf|infinite|indefinite)$/i.test(parts[2])) {
    ttl = parts[2];
    reasonStart = 3;
  }
  const reason = parts.slice(reasonStart).join(' ');

  // Authority pre-check (the CLI also checks; this gives a clearer error)
  const callerRole = _callerRole(ctx);
  if (callerRole !== 'owner' && callerRole !== 'admin') {
    return { reply: `✗ /grant denied: your role is "${callerRole}". Only owner/admin may grant.`, kind: 'grant-denied' };
  }

  const cliArgs = ['grant',
    String(ctx.channel || ''), String(ctx.chatKey || ''),
    targetUid, bundle, String(ctx.uid)];
  if (ttl !== null) cliArgs.push(ttl);
  else cliArgs.push('7d');  // explicit default; matches roles.DEFAULT_TTL_S
  if (reason) cliArgs.push(reason);

  const r = _runRolesCli(cliArgs);
  let j; try { j = JSON.parse(r.stdout); }
  catch { return { reply: `grant: parse error\n${r.stdout.slice(0, 400)}\n${r.stderr.slice(0, 200)}`, kind: 'grant-error' }; }
  if (!j.ok) {
    const errText = {
      'invalid-uid': 'Invalid uid format.',
      'invalid-bundle': `Unknown bundle "${bundle}". Use admin / member / observer.`,
      'owner-not-grantable': 'Owner role is intrinsic — set via the channel whitelist, not /grant.',
      'self-grant': 'You cannot grant a role to yourself.',
      'insufficient-authority': `Your role ("${callerRole}") cannot grant "${bundle}". Admins may only grant member/observer.`,
      'invalid-ttl': 'Invalid TTL. Use 30s / 5m / 1h / 7d / never.',
    }[j.error] || j.error;
    return { reply: `✗ /grant rejected: ${errText}`, kind: 'grant-denied' };
  }
  return {
    reply: `✓ Granted ${j.bundle} to ${j.target} (expires: ${j.ttl_human}).`,
    kind: 'grant-ok',
  };
}

function rolesRevokeReply(ctx, tail) {
  if (!ctx.uid) {
    return { reply: '/revoke: cannot identify your uid — daemon must pass ctx.uid.', kind: 'revoke-error' };
  }
  const targetUid = (tail || '').trim();
  if (!targetUid) {
    return { reply: 'Usage: /revoke <uid>', kind: 'revoke-error' };
  }
  const callerRole = _callerRole(ctx);
  if (callerRole !== 'owner' && callerRole !== 'admin') {
    return { reply: `✗ /revoke denied: your role is "${callerRole}". Only owner/admin may revoke.`, kind: 'revoke-denied' };
  }
  const r = _runRolesCli(['revoke',
    String(ctx.channel || ''), String(ctx.chatKey || ''),
    targetUid, String(ctx.uid)]);
  let j; try { j = JSON.parse(r.stdout); }
  catch { return { reply: `revoke: parse error\n${r.stdout.slice(0, 300)}`, kind: 'revoke-error' }; }
  if (!j.ok) {
    const errText = {
      'invalid-uid': 'Invalid uid format.',
      'owner-not-revocable': 'Owners cannot be revoked via /revoke — edit the channel whitelist instead.',
      'cannot-revoke-peer': 'Admins cannot revoke other admins (or owners). The owner has to do that.',
      'insufficient-authority': `Your role ("${callerRole}") cannot revoke roles.`,
    }[j.error] || j.error;
    return { reply: `✗ /revoke rejected: ${errText}`, kind: 'revoke-denied' };
  }
  return {
    reply: j.existed
      ? `✓ Revoked role for ${targetUid}.`
      : `(no role was on file for ${targetUid})`,
    kind: 'revoke-ok',
  };
}

function rolesLeaveReply(ctx) {
  if (!ctx.uid) {
    return { reply: '/leave: cannot identify your uid — daemon must pass ctx.uid.', kind: 'leave-error' };
  }
  const r = _runRolesCli(['leave',
    String(ctx.channel || ''), String(ctx.chatKey || ''), String(ctx.uid)]);
  let j; try { j = JSON.parse(r.stdout); }
  catch { return { reply: `leave: parse error\n${r.stdout.slice(0, 300)}`, kind: 'leave-error' }; }
  if (!j.ok) {
    const errText = {
      'owner-cannot-leave': 'Owners cannot /leave — edit the channel whitelist to remove yourself.',
      'no-entry': 'You have no granted role to leave (you may already be unbound).',
      'invalid-uid': 'Invalid uid format.',
    }[j.reason] || j.reason;
    return { reply: `✗ /leave rejected: ${errText}`, kind: 'leave-denied' };
  }
  return { reply: `✓ You left the "${j.prior_bundle}" role in this chat.`, kind: 'leave-ok' };
}

// ─── Layer-19 — bot-disclosure card + /join self-service ───────────────
//
// Two integration sites:
//
//   • dispatchReadOnlyDisclosure(ctx) — the daemon calls this from the
//     read-only branch BEFORE maybeForwardAsObserver, so a non-whitelist
//     uid can /join (self-grant observer) or /pass (acknowledge the
//     card) without ever reaching the silent-drop path.
//
//   • Owner-side dispatch() — owners typing /join or /pass get a
//     friendly redirect explaining who the commands are for.

function _runDisclosureCli(args) {
  const r = spawnSync('python3', [DISCLOSURE_CLI, ...args], {
    encoding: 'utf8', timeout: 5000,
  });
  return {
    stdout: r.stdout || '',
    stderr: r.stderr || '',
    status: r.status,
    error: r.error,
  };
}

// ── ADR-0166 Session Participation Gate (SPG) — owner commands ─────────────
// /on /off /open /close /invite @uid [ttl] /uninvite @uid /who
// All require ctx.isOwner === true. Calls spg.py CLI via spawnSync.

const _SPG_DISCLOSURE = '🤖 This chat now has AI assistance enabled via CorvinOS (EU AI Act Art. 50 disclosure). Type /leave at any time to opt out.';

function _runSpg(channel, chatKey, ...args) {
  // spg.py <cmd> <channel> <chat_key> [extra...]
  const [cmd, ...extra] = args;
  return spawnSync('python3', [SPG_CLI, cmd, channel, chatKey.toString(), ...extra], {
    encoding: 'utf8',
    timeout: 5000,
  });
}

function _spgJson(r) {
  if (r.error) return { error: String(r.error) };
  try { return JSON.parse(r.stdout || '{}'); } catch (e) {
    return { error: (r.stderr || r.stdout || 'parse error').slice(0, 200) };
  }
}

function spgOpenReply(ctx) {
  if (!ctx.isOwner) return { reply: '/open: only owners can change session participation mode.', kind: 'spg-denied' };
  const channel = String(ctx.channel || '');
  const chatKey = String(ctx.chatKey || '');
  const r = _runSpg(channel, chatKey, 'set-mode', 'open', String(ctx.uid || ''));
  const j = _spgJson(r);
  if (j.error) return { reply: `spg error: ${j.error}`, kind: 'spg-error' };
  return {
    reply: `✓ Session opened — all users may now interact.\n\n${_SPG_DISCLOSURE}`,
    kind: 'spg-open',
  };
}

function spgCloseReply(ctx) {
  if (!ctx.isOwner) return { reply: '/close: only owners can change session participation mode.', kind: 'spg-denied' };
  const channel = String(ctx.channel || '');
  const chatKey = String(ctx.chatKey || '');
  const r = _runSpg(channel, chatKey, 'set-mode', 'private', String(ctx.uid || ''));
  const j = _spgJson(r);
  if (j.error) return { reply: `spg error: ${j.error}`, kind: 'spg-error' };
  return { reply: '✓ Session closed — only you can interact now.', kind: 'spg-close' };
}

/** Strip Discord mention formats (<@ID>, <@!ID>) and plain @-prefix to raw uid.
 *  Returns '' for malformed mentions (e.g. '<@>') so callers can reject them. */
function _stripMention(raw) {
  // Discord mention: <@123456789> or <@!123456789> (! = nickname mention)
  const m = raw.match(/^<@!?(\d+)>$/);
  if (m) return m[1];
  // Angle-bracket without valid snowflake — malformed mention, signal with empty string.
  if (raw.startsWith('<@')) return '';
  return raw.replace(/^@/, '');
}

function spgInviteReply(ctx, tail) {
  if (!ctx.isOwner) return { reply: '/invite: only owners can invite guests.', kind: 'spg-denied' };
  if (!tail || !tail.trim()) return { reply: 'Usage: /invite <uid> [duration]\nDuration: 30s, 5m, 1h, 24h, 7d. Default: session lifetime.', kind: 'spg-help' };
  const channel = String(ctx.channel || '');
  const chatKey = String(ctx.chatKey || '');
  const parts = tail.trim().split(/\s+/);
  const uid = _stripMention(parts[0]);
  if (!uid) return { reply: '/invite: could not parse uid — use a valid @mention or user ID.', kind: 'spg-error' };
  const ttl = parts[1] || '';
  const grantor = String(ctx.uid || 'owner');
  // Always pass both ttl and grantor as positional args so Python can read
  // granted_by from extra[2] instead of misidentifying it as extra[1] (TTL).
  // Empty string tells Python: no TTL → session lifetime (parse_ttl skips '').
  const cliArgs = ['add-guest', uid, ttl, grantor];
  const r = _runSpg(channel, chatKey, ...cliArgs);
  const j = _spgJson(r);
  if (j.error) return { reply: `invite error: ${j.error}`, kind: 'spg-error' };
  const ttlMsg = ttl ? ` for ${ttl}` : ' for this session';
  return {
    reply: `✓ ${parts[0]} invited${ttlMsg}.\n\n${_SPG_DISCLOSURE}`,
    kind: 'spg-invited',
  };
}

function spgUninviteReply(ctx, tail) {
  if (!ctx.isOwner) return { reply: '/uninvite: only owners can remove guests.', kind: 'spg-denied' };
  if (!tail || !tail.trim()) return { reply: 'Usage: /uninvite <uid>', kind: 'spg-help' };
  const channel = String(ctx.channel || '');
  const chatKey = String(ctx.chatKey || '');
  const uid = _stripMention(tail.trim().split(/\s+/)[0]);
  const r = _runSpg(channel, chatKey, 'rm-guest', uid);
  const j = _spgJson(r);
  if (j.error) return { reply: `uninvite error: ${j.error}`, kind: 'spg-error' };
  const note = j.existed ? '' : ' (was not in guest list)';
  return { reply: `✓ ${uid} removed${note}.`, kind: 'spg-uninvited' };
}

function spgWhoReply(ctx) {
  if (!ctx.isOwner) return { reply: '/who: only owners can view participation status.', kind: 'spg-denied' };
  const channel = String(ctx.channel || '');
  const chatKey = String(ctx.chatKey || '');
  const r = _runSpg(channel, chatKey, 'list');
  const j = _spgJson(r);
  if (j.error) return { reply: `who error: ${j.error}`, kind: 'spg-error' };
  const mode = j.mode || 'private';
  const guests = j.guests || [];
  const lines = [`Session mode: **${mode}**`];
  if (guests.length === 0) {
    lines.push('No invited guests.');
  } else {
    lines.push(`Guests (${guests.length}):`);
    const now = Date.now() / 1000;
    for (const g of guests) {
      const exp = g.expires_at;
      const rem = exp ? Math.max(0, Math.floor(exp - now)) : null;
      const ttlStr = rem !== null ? ` — ${Math.floor(rem / 60)}m${rem % 60}s left` : ' — session lifetime';
      lines.push(`  ${g.uid_hash || '(unknown)'}${ttlStr}`);
    }
  }
  return { reply: lines.join('\n'), kind: 'spg-who' };
}

function disclosureOwnerJoinReply(_ctx) {
  return {
    reply: 'You are an owner — /join is for new participants who want to register as observers. Owners are intrinsic via the channel whitelist.',
    kind: 'disclosure-info',
  };
}

function disclosureOwnerPassReply(_ctx) {
  return {
    reply: 'You are an owner — /pass is for new participants who want to acknowledge the bot-disclosure card without taking any action.',
    kind: 'disclosure-info',
  };
}

// Daemon-side hook: ctx must carry { text, channel, chatKey, uid, settingsFile }.
// Returns:
//   null               — text is not /join or /pass
//   { reply, kind, ... } — daemon should send `reply` to the user
function dispatchReadOnlyDisclosure(ctx) {
  if (!ctx || typeof ctx.text !== 'string') return null;
  const raw = ctx.text.trim();
  if (!raw.startsWith('/')) return null;

  const channel = String(ctx.channel || '');
  const chatKey = String(ctx.chatKey || '');
  const uid = String(ctx.uid || '');

  const head = raw.split(/\s+/)[0].toLowerCase();
  if (head !== '/join' && head !== '/pass') return null;

  if (!uid) {
    return { reply: `${head}: cannot identify your uid — daemon bug.`,
             kind: 'disclosure-error' };
  }

  if (head === '/join') {
    const r = _runDisclosureCli(['join', channel, chatKey, uid]);
    let j; try { j = JSON.parse(r.stdout || '{}'); }
    catch { return { reply: `join: parse error\n${r.stdout.slice(0, 300)}`, kind: 'disclosure-error' }; }
    if (j.ok) {
      if (j.reason === 'joined') {
        return { reply: '✓ You are now registered as an *observer* in this chat. The owner sees you in /roles and may promote you later. Use /leave to give up the role.',
                 kind: 'disclosure-joined' };
      }
      if (j.reason === 'already-observer') {
        return { reply: '(you are already an observer in this chat)',
                 kind: 'disclosure-joined' };
      }
      return { reply: `✓ ${j.reason}`, kind: 'disclosure-joined' };
    }
    const err = {
      'owner-already': 'You are an owner — no need to /join.',
      'already-elevated': `You already have an elevated role ("${j.current || '?'}"). /join is for new participants only.`,
      'invalid-uid': 'Invalid uid format — daemon bug.',
    }[j.reason] || (j.reason || 'unknown');
    return { reply: `✗ /join rejected: ${err}`, kind: 'disclosure-denied' };
  }

  // /pass
  const r = _runDisclosureCli(['pass', channel, chatKey, uid]);
  let j; try { j = JSON.parse(r.stdout || '{}'); }
  catch { return { reply: `pass: parse error\n${r.stdout.slice(0, 300)}`, kind: 'disclosure-error' }; }
  if (j.ok) {
    return { reply: '✓ Acknowledged. The card will not be shown again. Type /join later if you change your mind, or /leave to drop any state we recorded.',
             kind: 'disclosure-passed' };
  }
  const err = {
    'owner-already': 'You are an owner — no need to /pass.',
    'invalid-uid': 'Invalid uid format — daemon bug.',
  }[j.reason] || (j.reason || 'unknown');
  return { reply: `✗ /pass rejected: ${err}`, kind: 'disclosure-denied' };
}

// Helper for the daemon: render the bot-disclosure card for first-encounter.
// Returns the card text (≤ 1500 chars). Daemon calls this when has_seen()
// returns false, sends the card, then mark_seen(action="pending").
function disclosureCardText({ channel, ownerLabel, hasObserverTranscript, lang }) {
  const args = ['card', String(channel || ''), String(ownerLabel || '(unknown)')];
  // i18n — only `de` keeps the German card, every other code (including
  // legacy `en` and any BCP-47 like zh-Hans / ja / ar / fr) hits the
  // English template. Phase 4 will swap the templates for LLM-generated
  // translations; for now English is the engine-stable pivot.
  const base = String(lang || 'en').split('-')[0].toLowerCase();
  args.push(base === 'de' ? 'de' : 'en');
  args.push(hasObserverTranscript ? 'transcript' : 'no-transcript');
  const r = _runDisclosureCli(args);
  return r.status === 0 ? r.stdout : '';
}

function disclosureHasSeen({ channel, chatKey, uid }) {
  const r = _runDisclosureCli(['state', String(channel || ''),
                                String(chatKey || ''), String(uid || '')]);
  if (r.status !== 0) return false;
  try { return JSON.parse(r.stdout).seen === true; }
  catch { return false; }
}

// V-004: synchronous busy-wait helper — acceptable here because disclosureMarkSeen
// is called at most once per uid per chat and the flock write path rarely fails.
function _sleepMsSync(ms) {
  const deadline = Date.now() + ms;
  while (Date.now() < deadline) { /* spin */ }
}

function disclosureMarkSeen({ channel, chatKey, uid, action }) {
  // V-004/V-011: retry on CLI failure; propagate {ok, error} so callers can log.
  const retryDelaysMs = [100, 300, 1000];
  let lastErr = '';
  for (let attempt = 0; attempt <= retryDelaysMs.length; attempt++) {
    const r = _runDisclosureCli(['seen', String(channel || ''),
                                  String(chatKey || ''), String(uid || ''),
                                  String(action || 'pending')]);
    if (r.status === 0) return { ok: true };
    lastErr = (r.stderr || '').trim() || `exit ${r.status}`;
    if (attempt < retryDelaysMs.length) {
      _sleepMsSync(retryDelaysMs[attempt]);
    }
  }
  // All retries exhausted — CRITICAL to stderr (not audit chain, which may itself be failing).
  process.stderr.write(
    `[disclosure] mark_seen failed after ${retryDelaysMs.length + 1} attempts `
    + `(channel=${channel}): ${lastErr}\n`
  );
  return { ok: false, error: lastErr };
}

// ─── Layer-20 — quota + audit-view (delegated-budget visibility) ───────

function _runQuotaCli(args) {
  const r = spawnSync('python3', [QUOTA_CLI, ...args], {
    encoding: 'utf8', timeout: 5000,
  });
  return { stdout: r.stdout || '', stderr: r.stderr || '',
           status: r.status, error: r.error };
}

function _runAuditViewCli(args) {
  const r = spawnSync('python3', [AUDIT_VIEW_CLI, ...args], {
    encoding: 'utf8', timeout: 5000,
  });
  return { stdout: r.stdout || '', stderr: r.stderr || '',
           status: r.status, error: r.error };
}

function _formatLimit(v) {
  if (v === null || v === undefined) return 'unlimited';
  return String(v);
}

function _formatQuotaUsage(j) {
  const lines = [`Quota for ${j.uid} in this chat (role: ${j.role}):`];
  lines.push(`  Messages today : ${j.messages_today} / ${_formatLimit(j.limit_msgs)}` +
             (j.limit_msgs_overridden ? ' (override)' : ''));
  lines.push(`  Tokens today   : ${j.tokens_today} / ${_formatLimit(j.limit_tokens)}` +
             (j.limit_tokens_overridden ? ' (override)' : ''));
  if (j.window_remaining_s !== undefined && j.window_remaining_s !== null) {
    const m = Math.floor(j.window_remaining_s / 60);
    const h = Math.floor(m / 60);
    lines.push(`  Window resets in: ${h}h${m % 60}m`);
  }
  return lines.join('\n');
}

function quotaReply(ctx, tail) {
  // Sub-commands:
  //   /quota                 → caller's own usage
  //   /quota <uid>           → owner/admin: another user's usage
  //   /quota all             → owner/admin: list all users in chat
  //   /quota set <uid> <msgs|keep|clear> <tokens|keep|clear>  (owner/admin)
  //   /quota reset <uid>     (owner/admin)
  if (!ctx.uid) {
    return { reply: '/quota: cannot identify your uid — daemon must pass ctx.uid.',
             kind: 'quota-error' };
  }
  const callerRole = _callerRole(ctx);
  const t = (tail || '').trim();

  // No args → own usage
  if (!t) {
    const r = _runQuotaCli(['usage', String(ctx.channel || ''),
                             String(ctx.chatKey || ''), String(ctx.uid)]);
    if (r.status !== 0) {
      return { reply: `quota usage failed: ${(r.stderr || r.stdout).slice(0, 300)}`,
               kind: 'quota-error' };
    }
    let j; try { j = JSON.parse(r.stdout); }
    catch { return { reply: `quota parse error\n${r.stdout.slice(0, 300)}`,
                     kind: 'quota-error' }; }
    return { reply: _formatQuotaUsage(j), kind: 'quota' };
  }

  const [first, rest] = _splitTail(t);
  const fl = first.toLowerCase();

  if (fl === 'all') {
    if (callerRole !== 'owner' && callerRole !== 'admin') {
      return { reply: 'Only owner/admin may list all quotas in this chat.',
               kind: 'quota-denied' };
    }
    const r = _runQuotaCli(['list', String(ctx.channel || ''),
                             String(ctx.chatKey || '')]);
    if (r.status !== 0) {
      return { reply: `quota list failed: ${(r.stderr || r.stdout).slice(0, 300)}`,
               kind: 'quota-error' };
    }
    let j; try { j = JSON.parse(r.stdout); }
    catch { return { reply: `quota list parse error\n${r.stdout.slice(0, 300)}`,
                     kind: 'quota-error' }; }
    const entries = Object.entries(j.entries || {});
    if (!entries.length) return { reply: '(no quota entries in this chat)',
                                  kind: 'quota-list' };
    const lines = ['Quota usage in this chat:'];
    for (const [uid, e] of entries) {
      const lm = e.limit_msgs !== undefined ? e.limit_msgs : null;
      const lt = e.limit_tokens !== undefined ? e.limit_tokens : null;
      lines.push(`  ${uid}: ${e.messages || 0} msg / ${e.tokens || 0} tok` +
                 (lm !== null ? ` (msg cap ${lm})` : '') +
                 (lt !== null ? ` (tok cap ${lt})` : ''));
    }
    return { reply: lines.join('\n'), kind: 'quota-list' };
  }

  if (fl === 'set') {
    if (callerRole !== 'owner' && callerRole !== 'admin') {
      return { reply: 'Only owner/admin may set quotas.',
               kind: 'quota-denied' };
    }
    // rest: <uid> <msgs|keep|clear> <tokens|keep|clear>
    const parts = (rest || '').trim().split(/\s+/);
    if (parts.length < 3) {
      return { reply: 'Usage: /quota set <uid> <msgs|keep|clear> <tokens|keep|clear>',
               kind: 'quota-error' };
    }
    const r = _runQuotaCli(['set', String(ctx.channel || ''),
                             String(ctx.chatKey || ''),
                             parts[0], parts[1], parts[2], String(ctx.uid)]);
    if (r.status !== 0) {
      return { reply: `quota set failed: ${(r.stderr || r.stdout).slice(0, 300)}`,
               kind: 'quota-error' };
    }
    return { reply: `✓ Quota updated for ${parts[0]} (msgs=${parts[1]}, tokens=${parts[2]}).`,
             kind: 'quota-set' };
  }

  if (fl === 'reset') {
    if (callerRole !== 'owner' && callerRole !== 'admin') {
      return { reply: 'Only owner/admin may reset quotas.',
               kind: 'quota-denied' };
    }
    const target = (rest || '').trim();
    if (!target) {
      return { reply: 'Usage: /quota reset <uid>', kind: 'quota-error' };
    }
    const r = _runQuotaCli(['reset', String(ctx.channel || ''),
                             String(ctx.chatKey || ''),
                             target, String(ctx.uid)]);
    if (r.status !== 0) {
      return { reply: `quota reset failed: ${(r.stderr || r.stdout).slice(0, 300)}`,
               kind: 'quota-error' };
    }
    let j; try { j = JSON.parse(r.stdout); } catch { j = {}; }
    return { reply: j.existed
      ? `✓ Quota reset for ${target}.`
      : `(no quota entry was on file for ${target})`,
      kind: 'quota-reset' };
  }

  // Default: treat first token as a uid → fetch their usage
  if (callerRole !== 'owner' && callerRole !== 'admin') {
    return { reply: 'Only owner/admin may view another user\'s quota.',
             kind: 'quota-denied' };
  }
  const r = _runQuotaCli(['usage', String(ctx.channel || ''),
                           String(ctx.chatKey || ''), first]);
  if (r.status !== 0) {
    return { reply: `quota usage failed: ${(r.stderr || r.stdout).slice(0, 300)}`,
             kind: 'quota-error' };
  }
  let j; try { j = JSON.parse(r.stdout); }
  catch { return { reply: `quota parse error\n${r.stdout.slice(0, 300)}`,
                   kind: 'quota-error' }; }
  return { reply: _formatQuotaUsage(j), kind: 'quota' };
}

function _formatAuditEvents(j) {
  if (!j.events || !j.events.length) return '(no events match)';
  const lines = [`Audit events (${j.scope}, count: ${j.count}):`];
  for (const ev of j.events) {
    // Render with a Python helper for consistency? No — duplicate the
    // simple summarisation here to avoid a second subprocess per event.
    const ts = ev.ts;
    const when = ts
      ? new Date(ts * 1000).toISOString().slice(5, 16).replace('T', ' ')
      : '??';
    const sev = ev.severity && ev.severity !== 'INFO' ? `[${ev.severity}] ` : '';
    const det = ev.details || {};
    const fragments = [];
    if (det.uid) fragments.push(`uid=${det.uid}`);
    if (det.target && det.target !== det.uid) fragments.push(`target=${det.target}`);
    if (det.bundle) fragments.push(`bundle=${det.bundle}`);
    if (det.reason) fragments.push(`reason=${det.reason}`);
    if (det.metric) fragments.push(`metric=${det.metric}`);
    if (det.action) fragments.push(`action=${det.action}`);
    lines.push(`  ${when} ${sev}${ev.event_type || '?'} ${fragments.join(' ')}`);
  }
  return lines.join('\n');
}

// ─── ADR-0073 G-016 — /privacy (GDPR Art. 13/14 information right) ────────────
//
// Shows the operator-configured privacy notice URL and a summary of
// exercisable GDPR rights. No personal data is displayed.
//
// Sub-commands:
//   /privacy            → show notice URL + rights summary
//   /privacy rights     → list all exercisable rights with commands
//
function privacyReply(ctx, tail) {
  const sub = (tail || '').trim().toLowerCase();

  // Read privacy_notice_url from channel settings (hot-reload safe: read fresh).
  let privacyUrl = '';
  try {
    const settingsPath = path.resolve(__dirname, '..', '..', ctx.channel || 'discord', 'settings.json');
    const raw = require('fs').readFileSync(settingsPath, 'utf8');
    privacyUrl = (JSON.parse(raw).privacy_notice_url || '').trim();
  } catch (_) { /* best-effort */ }

  const urlLine = privacyUrl
    ? `\n📄 **Privacy notice:** ${privacyUrl}`
    : '\n_(No privacy_notice_url configured — contact the operator for the full privacy notice.)_';

  if (sub === 'rights') {
    return {
      reply: [
        '**Your GDPR data rights in this system:**',
        '',
        '• **Right of access (Art. 15):** Ask the operator for a copy of your data.',
        '• **Right to erasure (Art. 17):** your conversation recall auto-expires (session TTL, default 7 days); ask the operator for immediate erasure.',
        '  For full erasure across all stores, the operator runs `corvin-erasure <your-id>`.',
        '• **Right to withdraw consent (Art. 7):** `/consent off` — stops AI processing your messages.',
        '• **Right to restrict processing (Art. 18):** Contact the operator.',
        '• **Right to data portability (Art. 20):** Contact the operator.',
        '• **Right to object (Art. 21):** Contact the operator.',
        '',
        'For automated-decision rights (GDPR Art. 22), ask the operator whether this deployment',
        'makes decisions with significant legal or similar effects.',
        urlLine,
      ].join('\n'),
      kind: 'privacy',
    };
  }

  return {
    reply: [
      '**Privacy information (GDPR Art. 13/14)**',
      '',
      'This AI assistant is operated by: _see operator_name in channel settings_',
      '',
      '**Data processed:** Message text, voice audio (deleted after transcription),',
      'conversation recall (session TTL, default 7 days), behavioral model.',
      '',
      '**Your rights:** Type `/privacy rights` for the full list of exercisable GDPR rights.',
      '**Consent:** Type `/consent off` to withdraw consent and stop AI processing.',
      '**Forget me:** Your conversation recall auto-expires (session TTL, default 7 days); for immediate GDPR Art. 17 erasure, ask the operator (`corvin-erasure`).',
      urlLine,
    ].join('\n'),
    kind: 'privacy',
  };
}


// ─── ADR-0073 G-010 — /decision-review (EU AI Act Art. 13-14 human oversight) ─
//
// Admin-only command to list and review significant AI decisions recorded in the
// session's decision_registry.jsonl.
//
// Sub-commands:
//   /decision-review                         → list pending decisions
//   /decision-review <id>                    → show decision details
//   /decision-review <id> approve|reject     → mark decision reviewed
//
function decisionReviewReply(ctx, tail) {
  const callerRole = _callerRole(ctx);
  if (callerRole !== 'owner' && callerRole !== 'admin') {
    return { reply: 'Only owner/admin may review AI decisions (/decision-review).',
             kind: 'decision-denied' };
  }

  const parts = (tail || '').trim().split(/\s+/).filter(Boolean);
  const decId = parts[0] || '';
  const action = (parts[1] || '').toLowerCase();

  // Build argv for decision_registry.py cli_review()
  const argv = [];
  if (decId) argv.push(decId);
  if (action === 'approve') argv.push('--approve');
  else if (action === 'reject') argv.push('--reject');

  // Session dir is resolved from CORVIN_HOME / sessions / channel / chat_key
  const atHome = process.env.CORVIN_HOME || '';
  if (!atHome) {
    return { reply: 'decision-review: CORVIN_HOME not set — cannot locate session registry.',
             kind: 'decision-error' };
  }

  const safeChannel = (ctx.channel || 'unknown').replace(/[^a-zA-Z0-9_-]/g, '_');
  const safeChatKey = (ctx.chatKey || 'unknown').replace(/[^a-zA-Z0-9_-]/g, '_');
  const sessionDir = path.join(atHome, 'tenants', '_default', 'sessions',
                               `${safeChannel}:${safeChatKey}`);

  const uidHash = (ctx.uid || 'unknown').slice(0, 8);  // abbreviated, not raw UID

  const r = spawnSync('python3', ['-c',
    `import sys; sys.path.insert(0,"${path.resolve(__dirname,'..')}"); ` +
    `from decision_registry import cli_review; ` +
    `from pathlib import Path; ` +
    `print(cli_review(${JSON.stringify(argv)}, session_dir=Path(${JSON.stringify(sessionDir)}), ` +
    `reviewer_hash=${JSON.stringify(uidHash)}))`,
  ], { encoding: 'utf8', timeout: 5000 });

  if (r.status !== 0 || r.stderr) {
    const err = (r.stderr || r.stdout || 'unknown error').slice(0, 300);
    return { reply: `decision-review error: ${err}`, kind: 'decision-error' };
  }
  const reply = (r.stdout || '').trim() || 'No output.';
  return { reply, kind: 'decision-review' };
}


function auditReply(ctx, tail) {
  // Sub-commands:
  //   /audit              → /audit me 20
  //   /audit me [<n>] [<prefix>]
  //   /audit chat [<n>] [<prefix>]
  if (!ctx.uid) {
    return { reply: '/audit: cannot identify your uid — daemon must pass ctx.uid.',
             kind: 'audit-error' };
  }
  const t = (tail || '').trim();
  const [first, rest] = _splitTail(t);
  const sub = first ? first.toLowerCase() : 'me';
  const restParts = (rest || '').trim().split(/\s+/).filter(Boolean);
  const limit = (restParts[0] && /^\d+$/.test(restParts[0])) ? restParts[0] : '20';
  const prefix = (restParts[0] && /^\d+$/.test(restParts[0])) ? (restParts[1] || '') : (restParts[0] || '');

  if (sub === 'me' || sub === '') {
    const r = _runAuditViewCli(['me', String(ctx.channel || ''),
                                 String(ctx.chatKey || ''), String(ctx.uid),
                                 String(limit), prefix]);
    if (r.status !== 0) {
      return { reply: `audit me failed: ${(r.stderr || r.stdout).slice(0, 300)}`,
               kind: 'audit-error' };
    }
    let j; try { j = JSON.parse(r.stdout); }
    catch { return { reply: `audit parse error\n${r.stdout.slice(0, 300)}`,
                     kind: 'audit-error' }; }
    return { reply: _formatAuditEvents(j), kind: 'audit-me' };
  }

  if (sub === 'chat') {
    const callerRole = _callerRole(ctx);
    if (callerRole !== 'owner' && callerRole !== 'admin') {
      return { reply: 'Only owner/admin may view chat-wide audit (audit_chat capability).',
               kind: 'audit-denied' };
    }
    const r = _runAuditViewCli(['chat', String(ctx.channel || ''),
                                 String(ctx.chatKey || ''),
                                 String(limit), prefix]);
    if (r.status !== 0) {
      return { reply: `audit chat failed: ${(r.stderr || r.stdout).slice(0, 300)}`,
               kind: 'audit-error' };
    }
    let j; try { j = JSON.parse(r.stdout); }
    catch { return { reply: `audit parse error\n${r.stdout.slice(0, 300)}`,
                     kind: 'audit-error' }; }
    return { reply: _formatAuditEvents(j), kind: 'audit-chat' };
  }

  return { reply: `Unknown /audit sub-command: ${sub}\nUsage: /audit me [<n>] | /audit chat [<n>]`,
           kind: 'audit-error' };
}

// ─── Layer-21 — curated proposal stack ────────────────────────────────
//
// Three integration points:
//
//   • Owner-side dispatch: /propose, /proposals, /proposal rm|clear are
//     handled in dispatch(). /go is handled by the *daemon* because it
//     needs to write a synthesised inbox message — dispatch() only
//     produces text replies.
//
//   • Read-only-side dispatchReadOnlyProposal(ctx): /propose for
//     non-whitelist users, mirrors dispatchReadOnlyConsent.
//
//   • Daemon helper proposalsBuildGoPayload(ctx, ownerText): atomic
//     consume + format. Daemon writes the returned text as a normal
//     inbox message, so the adapter triggers claude with stack +
//     owner-steering as one combined prompt.

function _runProposalCli(args) {
  const r = spawnSync('python3', [PROPOSAL_CLI, ...args], {
    encoding: 'utf8', timeout: 5000,
  });
  return { stdout: r.stdout || '', stderr: r.stderr || '',
           status: r.status, error: r.error };
}

function _addProposal(channel, chatKey, fromUid, fromRole, text) {
  return _runProposalCli(['add', String(channel || ''),
                          String(chatKey || ''), String(fromUid),
                          String(fromRole || 'unknown'), text]);
}

function proposeReply(ctx, tail) {
  // /propose <text> — Owner-side OR read-only-side both reach this.
  // Owner pings can also be self-notes / brainstorm starters.
  if (!ctx.uid) {
    return { reply: '/propose: cannot identify your uid — daemon must pass ctx.uid.',
             kind: 'propose-error' };
  }
  const body = (tail || '').trim();
  if (!body) {
    return { reply: 'Usage: /propose <text>\n\nDrop an idea / paste / suggestion onto the chat\'s proposal stack. The owner sees /proposals and triggers the AI with /go.',
             kind: 'propose-help' };
  }
  const role = _callerRole(ctx);
  const r = _addProposal(ctx.channel, ctx.chatKey, ctx.uid, role, body);
  let j; try { j = JSON.parse(r.stdout); }
  catch { return { reply: `propose: parse error\n${r.stdout.slice(0, 300)}`,
                   kind: 'propose-error' }; }
  if (!j.ok) {
    const err = {
      'empty-text': 'Propose with non-empty text, please.',
      'missing-from-uid': 'Daemon did not pass ctx.uid.',
    }[j.reason] || j.reason;
    return { reply: `✗ /propose rejected: ${err}`, kind: 'propose-denied' };
  }
  const id = j.entry && j.entry.id;
  const truncated = j.truncated ? ' (truncated to 2000 chars)' : '';
  const dropped = j.dropped
    ? `\n  (stack at cap; oldest entry "${j.dropped.id}" dropped)`
    : '';
  return {
    reply: `✓ Proposal ${id} added (#${j.stack_size} on stack)${truncated}.${dropped}\nOwner can /proposals to review, /go to execute.`,
    kind: 'propose-ok',
  };
}

function goalReply(ctx, tail) {
  // /goal            → show current goal (anyone)
  // /goal clear      → remove goal (owner only)
  // /goal <text>     → set goal (owner only)
  const body = (tail || '').trim();

  if (!body || body.toLowerCase() === 'show') {
    const r = spawnSync('python3', [GOAL_CLI, 'get', String(ctx.channel || ''),
                                     String(ctx.chatKey || '')],
                        { encoding: 'utf8', timeout: 5000 });
    let j; try { j = JSON.parse(r.stdout); }
    catch { return { reply: 'goal: unexpected response from backend', kind: 'goal-error' }; }
    if (!j.ok) return { reply: `goal: ${j.error || 'error'}`, kind: 'goal-error' };
    if (!j.goal) return { reply: 'No session goal set.\n\nUse `/goal <text>` to set one — it will be injected into every LLM turn.', kind: 'goal-empty' };
    return { reply: `**Session goal:**\n> ${j.goal}`, kind: 'goal-show' };
  }

  if (!ctx.isOwner) {
    return { reply: '✗ Only the owner can set or clear the session goal.', kind: 'goal-denied' };
  }

  if (body.toLowerCase() === 'clear') {
    const r = spawnSync('python3', [GOAL_CLI, 'clear', String(ctx.channel || ''),
                                     String(ctx.chatKey || '')],
                        { encoding: 'utf8', timeout: 5000 });
    let j; try { j = JSON.parse(r.stdout); }
    catch { return { reply: 'goal: unexpected response from backend', kind: 'goal-error' }; }
    if (!j.ok) return { reply: `goal: ${j.error || 'error'}`, kind: 'goal-error' };
    return { reply: '✓ Session goal cleared.', kind: 'goal-cleared' };
  }

  // set
  const r = spawnSync('python3', [GOAL_CLI, 'set', String(ctx.channel || ''),
                                   String(ctx.chatKey || ''), body],
                      { encoding: 'utf8', timeout: 5000 });
  let j; try { j = JSON.parse(r.stdout); }
  catch { return { reply: 'goal: unexpected response from backend', kind: 'goal-error' }; }
  if (!j.ok) return { reply: `goal: ${j.error || 'error'}`, kind: 'goal-error' };
  return { reply: `✓ Session goal set:\n> ${j.goal}\n\nIt will be injected into every LLM turn until cleared with \`/goal clear\`.`, kind: 'goal-set' };
}

function proposalsReply(ctx) {
  // /proposals — owner/admin: list the stack
  const callerRole = _callerRole(ctx);
  if (callerRole !== 'owner' && callerRole !== 'admin') {
    return { reply: 'Only owner/admin may list the proposal stack.',
             kind: 'proposals-denied' };
  }
  const r = _runProposalCli(['list', String(ctx.channel || ''),
                              String(ctx.chatKey || '')]);
  if (r.status !== 0) {
    return { reply: `proposals failed: ${(r.stderr || r.stdout).slice(0, 300)}`,
             kind: 'proposals-error' };
  }
  let entries; try { entries = JSON.parse(r.stdout); }
  catch { return { reply: `proposals parse error\n${r.stdout.slice(0, 300)}`,
                   kind: 'proposals-error' }; }
  if (!entries.length) {
    return { reply: '(no proposals on the stack — invite team members with /propose <text>)',
             kind: 'proposals' };
  }
  const lines = [`Proposal stack (${entries.length}):`];
  for (const e of entries) {
    const when = e.ts
      ? new Date(e.ts * 1000).toISOString().slice(5, 16).replace('T', ' ')
      : '??';
    const snippet = (e.text || '').replace(/\n/g, ' ').slice(0, 120);
    const more = (e.text || '').length > 120 ? '…' : '';
    lines.push(`  ${e.id}  ${when}  [${e.from_uid}, ${e.from_role}]  ${snippet}${more}`);
  }
  lines.push('');
  lines.push('Curate: /proposal rm <id>  ·  /proposal clear');
  lines.push('Trigger: /go [optional steering text]');
  return { reply: lines.join('\n'), kind: 'proposals' };
}

function proposalReply(ctx, tail) {
  // /proposal <sub> [<args>]
  //   /proposal rm <id>
  //   /proposal clear
  if (!ctx.uid) {
    return { reply: '/proposal: cannot identify your uid — daemon must pass ctx.uid.',
             kind: 'proposal-error' };
  }
  const t = (tail || '').trim();
  const [sub, rest] = _splitTail(t);
  const subL = (sub || '').toLowerCase();
  const callerRole = _callerRole(ctx);

  if (!subL) {
    return { reply: 'Usage: /proposal rm <id>  |  /proposal clear', kind: 'proposal-help' };
  }
  if (callerRole !== 'owner' && callerRole !== 'admin') {
    return { reply: 'Only owner/admin may curate the proposal stack.',
             kind: 'proposal-denied' };
  }

  if (subL === 'rm' || subL === 'remove' || subL === 'drop') {
    const pid = (rest || '').trim();
    if (!pid) return { reply: 'Usage: /proposal rm <id>', kind: 'proposal-error' };
    const r = _runProposalCli(['remove', String(ctx.channel || ''),
                                String(ctx.chatKey || ''), pid,
                                String(ctx.uid)]);
    let j; try { j = JSON.parse(r.stdout); } catch { j = {}; }
    return { reply: j.existed
      ? `✓ Removed proposal ${pid}.`
      : `(no proposal ${pid} on the stack)`,
      kind: 'proposal-rm' };
  }

  if (subL === 'clear' || subL === 'reset') {
    const r = _runProposalCli(['clear', String(ctx.channel || ''),
                                String(ctx.chatKey || ''), String(ctx.uid)]);
    let j; try { j = JSON.parse(r.stdout); } catch { j = {}; }
    return { reply: `✓ Stack cleared (${j.removed || 0} removed).`,
             kind: 'proposal-clear' };
  }

  return { reply: `Unknown /proposal sub-command: ${sub}`, kind: 'proposal-error' };
}

// Daemon-side helper for /go. Not exposed via dispatch(); the daemon
// must call this directly because /go SYNTHESISES a new inbox message
// out of the consumed stack + owner_text. dispatch() only returns
// reply text, so it can't do that on its own.
//
// Returns:
//   { allowed: false, reason: <code>, reply: <user-facing> } when /go
//   was rejected (caller is not owner/admin, or stack empty + no owner
//   text).
//
//   { allowed: true, prompt: <combined-text>, count: N,
//     ack: <user-facing reply confirming the trigger> } when the
//   daemon should proceed: write `prompt` to the inbox, send `ack`
//   back to the chat.
function proposalsBuildGoPayload(ctx, ownerText) {
  if (!ctx || !ctx.uid) {
    return { allowed: false, reason: 'no-uid',
             reply: '/go: daemon must pass ctx.uid.' };
  }
  const role = _callerRole(ctx);
  if (role !== 'owner' && role !== 'admin') {
    return { allowed: false, reason: 'insufficient-authority',
             reply: 'Only owner/admin may /go.' };
  }
  const r = _runProposalCli(['consume', String(ctx.channel || ''),
                              String(ctx.chatKey || ''), String(ctx.uid),
                              ownerText || '']);
  if (r.status !== 0) {
    return { allowed: false, reason: 'cli-error',
             reply: `consume failed: ${(r.stderr || r.stdout).slice(0, 300)}` };
  }
  let j; try { j = JSON.parse(r.stdout); }
  catch { return { allowed: false, reason: 'parse-error',
                   reply: `parse error\n${r.stdout.slice(0, 300)}` }; }
  const count = j.count || 0;
  const owner_t = (ownerText || '').trim();
  if (count === 0 && !owner_t) {
    return { allowed: false, reason: 'empty',
             reply: '/go: nothing to do — no proposals on the stack and no steering text. Add proposals first or include a question after /go.' };
  }
  return {
    allowed: true,
    reason: 'ok',
    count,
    prompt: j.prompt,
    ack: count > 0
      ? `▶ Triggered with ${count} proposal${count === 1 ? '' : 's'} from the stack${owner_t ? ' + your steering' : ''}.`
      : `▶ Triggered with your steering only (stack was empty).`,
  };
}

// Read-only-side dispatcher for /propose. Daemon calls this in the
// read-only branch BEFORE maybeForwardAsObserver, alongside
// dispatchReadOnlyConsent and dispatchReadOnlyDisclosure.
function dispatchReadOnlyProposal(ctx) {
  if (!ctx || typeof ctx.text !== 'string') return null;
  const raw = ctx.text.trim();
  if (!/^\/propose(\s|$)/i.test(raw)) return null;

  const tail = raw.replace(/^\/propose\s*/i, '');
  const uid = String(ctx.uid || '');
  if (!uid) {
    return { reply: '/propose: cannot identify your uid — daemon bug.',
             kind: 'propose-error' };
  }
  if (!tail.trim()) {
    return { reply: 'Usage: /propose <text>\n\nYour idea goes onto the chat\'s proposal stack — the owner reviews and triggers the AI with /go. No standing trigger rights needed.',
             kind: 'propose-help' };
  }
  // Read-only sender → role is by definition not owner/admin/member;
  // we still record it (most likely "observer" or "none").
  const role = _callerRole({...ctx, isOwner: false});
  const r = _addProposal(ctx.channel, ctx.chatKey, uid, role, tail);
  let j; try { j = JSON.parse(r.stdout); }
  catch { return { reply: `propose: parse error\n${r.stdout.slice(0, 300)}`,
                   kind: 'propose-error' }; }
  if (!j.ok) {
    return { reply: `✗ /propose rejected: ${j.reason}`, kind: 'propose-denied' };
  }
  return {
    reply: `✓ Proposal ${j.entry.id} added to the stack (#${j.stack_size}). The owner reviews and runs /go.`,
    kind: 'propose-ok',
  };
}

// ─── Phase-3 (Layers 17-20) — process table, pipes, services, budgets ───
//
// Single-shot CLI wrapper handles all four layers. The slash-command
// surface is intentionally small and read-leaning: /ps /pipe /svc /budget
// each take a sub-command + rest as args. Lifecycle-mutating commands
// (kill, sig, nice) and the live-streaming /top variant land in the
// follow-up slice once the adapter populates the registries with real
// session data.

function phase3Reply(ctx, command, sub, rest) {
  // command is one of "ps", "pipe", "svc", "budget".
  // sub is the sub-command (or "" / "list" implied), rest is space-split args.
  const args = ['python3', PHASE3_CLI, command];
  if (sub) args.push(sub);
  if (rest) {
    const parts = rest.split(/\s+/).filter(Boolean);
    args.push(...parts);
  }
  const r = spawnSync(args[0], args.slice(1), { encoding: 'utf8' });
  const out = (r.stdout || '') + (r.stderr ? '\n' + r.stderr : '');
  const trimmed = out.trim() || '(no output)';
  // Wrap in a fenced code block so chat clients render the table aligned
  // (whitespace-preserving) instead of collapsing the column widths.
  return {
    reply: '```\n' + trimmed + '\n```',
    kind: `phase3-${command}`,
  };
}

function dialecticReply(ctx, sub, rest) {
  // sub: "" (status) | "on" | "off" | "status" | "set" | "show"
  // /dialectic-on/off/status are direct heads; /dialectic-set <site> <mode>
  // and /dialectic-show <on|off> carry sub as the rest.
  const args = ['python3', DIALECTIC_CLI];
  if (sub) args.push(sub);
  if (rest) {
    // shell-split on whitespace — set takes <site> <mode>, show takes <on|off>.
    const parts = rest.split(/\s+/).filter(Boolean);
    args.push(...parts);
  }
  const r = spawnSync(args[0], args.slice(1), { encoding: 'utf8' });
  const out = (r.stdout || '') + (r.stderr ? '\n' + r.stderr : '');
  return { reply: out.trim() || '(no output)', kind: 'dialectic' };
}

// Layer-29 companion — `/engine [alias|off]` toggles which worker engine
// the orchestrator persona delegates to. Owner-only (delegation routing
// is a privileged decision). The Python module enforces alias validation,
// engine_id whitelist, and audit emission; this JS wrapper is just a
// trampoline + usage hint.
//
// Usage:
//   /engine                  → show current preference
//   /engine claude           → claude_code
//   /engine codex            → codex_cli
//   /engine opencode         → opencode + local Ollama
//   /engine cloud            → opencode + ollama-cloud
//   /engine off              → clear; orchestrator decides freely
function engineReply(ctx, tail) {
  if (ctx && ctx.isOwner === false) {
    return {
      reply: '🔒 /engine is owner-only — delegation routing is a privileged decision.',
      kind: 'engine',
    };
  }
  const channel = String((ctx && ctx.channel) || '');
  const chatKey = String((ctx && ctx.chatKey) || '');
  const uid = String((ctx && ctx.uid) || '');
  const token = (tail || '').trim().toLowerCase();

  if (!token) {
    const args = ['python3', ENGINE_SWITCH_CLI, 'show', channel, chatKey];
    const r = spawnSync(args[0], args.slice(1), { encoding: 'utf8' });
    const cur = ((r.stdout || '') + (r.stderr || '')).trim() || '(no preference)';
    const help = [
      '🛠️ /engine — worker-engine preference for this chat',
      '',
      `current: ${cur}`,
      '',
      'options:',
      '  /engine claude     — claude_code (default fallback)',
      '  /engine codex      — codex_cli',
      '  /engine opencode   — opencode + local Ollama',
      '  /engine cloud      — opencode + ollama-cloud',
      '  /engine off        — clear; orchestrator decides freely',
      '',
      'Bridge-features (/btw, skill-inject, forge-MCP, recall, …) stay',
      'on Claude Code regardless. This only routes delegated workers.',
    ].join('\n');
    return { reply: help, kind: 'engine' };
  }

  if (token === 'off' || token === 'clear' || token === 'reset' || token === 'none') {
    const args = ['python3', ENGINE_SWITCH_CLI, 'clear', channel, chatKey];
    if (uid) { args.push('--uid', uid); }
    const r = spawnSync(args[0], args.slice(1), { encoding: 'utf8' });
    const out = ((r.stdout || '') + (r.stderr || '')).trim() || '(no output)';
    return { reply: `🛠️ engine override: ${out}`, kind: 'engine' };
  }

  const args = ['python3', ENGINE_SWITCH_CLI, 'set', channel, chatKey, token];
  if (uid) { args.push('--uid', uid); }
  const r = spawnSync(args[0], args.slice(1), { encoding: 'utf8' });
  const out = ((r.stdout || '') + (r.stderr || '')).trim() || '(no output)';
  // Exit code 2 means usage / unknown alias. The Python CLI already
  // printed a helpful diagnostic; surface it verbatim with a soft prefix.
  if (r.status !== 0) {
    return { reply: `⚠️ /engine — ${out}`, kind: 'engine' };
  }
  return { reply: `🛠️ ${out}`, kind: 'engine' };
}

// `/settings` (also /einstellungen, /config) — single-message dump of
// WORKING/PFADE + SESSION + SYSTEM. No sub-commands; everything goes into
// one reply that the user can scroll on the phone. Python aggregator
// (settings_view.py) is the single source of truth so the same output is
// reachable from the CLI for ops.
function settingsReply(ctx) {
  const lang = (ctx && ctx.lang === 'en') ? 'en' : 'de';
  const args = ['python3', SETTINGS_CLI, 'render',
                String(ctx.channel || ''),
                String(ctx.chatKey || '')];
  if (ctx && ctx.uid) { args.push('--uid', String(ctx.uid)); }
  args.push('--lang', lang);
  const r = spawnSync(args[0], args.slice(1), { encoding: 'utf8' });
  const stdout = (r && r.stdout) ? r.stdout.trim() : '';
  const stderr = (r && r.stderr) ? r.stderr.trim() : '';
  if (!stdout && stderr) {
    return { reply: '⚠ /settings: ' + stderr, kind: 'error' };
  }
  return { reply: stdout || '(no output)', kind: 'settings' };
}

// `/workflow <sub> [...args]` — L26 AWP-workflow bridge.
// Shells out to `python3 -m corvin_workflows <sub> ...` and surfaces
// stdout/stderr verbatim. The MVP runs the StubEngine — every workflow
// run is deterministic and costs zero LLM tokens.
function workflowReply(ctx, sub, rest) {
  // Aliases: /workflows → /workflow list
  const knownSubs = new Set(['list', 'show', 'validate', 'run', 'schedule', 'help']);
  if (!sub) sub = 'list';
  if (sub === 'help') {
    return {
      reply:
        '`/workflow` — AWP-Workflow-Bridge (L26)\n' +
        '\n' +
        '`/workflow list`                              — bundled workflows\n' +
        '`/workflow show <name>`                       — YAML dump\n' +
        '`/workflow validate <name>`                   — R1..R10\n' +
        '`/workflow run <name> [key=value ...]`        — execute (Stub-Engine)\n' +
        '`/workflow schedule add <cron> <name> [kv]`   — cron-driven run into this chat\n' +
        '`/workflow schedule list`                     — show scheduled runs for this chat\n' +
        '`/workflow schedule rm <task_id>`             — cancel a scheduled run\n' +
        '\n' +
        'Examples:\n' +
        '`/workflow run news_sentiment_research ticker=NVDA window_days=7`\n' +
        '`/workflow schedule add "0 9 * * *" news_sentiment_research ticker=NVDA`',
      kind: 'workflow',
    };
  }
  if (!knownSubs.has(sub)) {
    return {
      reply: `unknown /workflow sub-command: ${sub} (try /workflow help)`,
      kind: 'error',
    };
  }
  let cliArgs;
  if (sub === 'schedule') {
    // Parse the schedule subtree: add|list|rm. add takes a quoted-or-bare
    // cron expression as its first token, then the workflow name, then
    // key=value inputs. We inject --channel + --chat from the chat ctx so
    // the user doesn't have to type them on every command.
    cliArgs = ['python3', '-m', 'corvin_workflows', 'schedule'];
    const tokens = _shellSplit(rest || '');
    const ssub = (tokens.shift() || '').toLowerCase();
    if (!['add', 'list', 'rm'].includes(ssub)) {
      return {
        reply: 'unknown /workflow schedule sub: ' + (ssub || '(empty)') +
               '\nUse: add <cron> <name> [kv] | list | rm <task_id>',
        kind: 'error',
      };
    }
    cliArgs.push(ssub);
    if (ssub === 'add') {
      // First token is the cron expression (already shell-split, so quoted
      // multi-word cron specs survive as one token). Then the workflow name.
      // Everything else is inputs.
      if (tokens.length < 2) {
        return {
          reply: 'Usage: /workflow schedule add "<cron>" <name> [key=value ...]\n' +
                 'Example: /workflow schedule add "0 9 * * *" news_sentiment_research ticker=NVDA',
          kind: 'error',
        };
      }
      const cronSpec = tokens.shift();
      const wfName = tokens.shift();
      cliArgs.push(cronSpec, wfName, ...tokens,
                   '--channel', String(ctx.channel || ''),
                   '--chat', String(ctx.chatKey || ''),
                   '--sender', String(ctx.uid || 'scheduler'));
    } else if (ssub === 'list') {
      // Filter the user's view to this chat only.
      cliArgs.push('--channel', String(ctx.channel || ''),
                   '--chat', String(ctx.chatKey || ''));
    } else if (ssub === 'rm') {
      if (!tokens[0]) {
        return { reply: 'Usage: /workflow schedule rm <task_id>', kind: 'error' };
      }
      cliArgs.push(tokens[0]);
    }
  } else {
    cliArgs = ['python3', '-m', 'corvin_workflows', sub];
    if (rest) {
      const parts = rest.split(/\s+/).filter(Boolean);
      cliArgs.push(...parts);
    }
  }
  const args = cliArgs;
  const env = Object.assign({}, process.env, {
    PYTHONPATH: WORKFLOWS_PKG_ROOT +
      (process.env.PYTHONPATH ? path.delimiter + process.env.PYTHONPATH : ''),
  });
  const r = spawnSync(args[0], args.slice(1), { encoding: 'utf8', env });
  const stdout = (r && r.stdout) ? r.stdout.trim() : '';
  const stderr = (r && r.stderr) ? r.stderr.trim() : '';
  if (!stdout && stderr) {
    return { reply: '⚠ /workflow: ' + stderr, kind: 'error' };
  }
  // Cap reply at ~3500 chars so phone scroll stays sane; long JSON dumps
  // are truncated with a marker.
  let out = stdout || '(no output)';
  if (out.length > 3500) {
    out = out.slice(0, 3400) + '\n…(truncated, full output via CLI)';
  }
  return { reply: out, kind: 'workflow' };
}

// ── Layer-27 — personal tools (me.* permanent forge library) ───────────

function _runPersonalToolsCli(args) {
  const r = spawnSync('python3', [PERSONAL_TOOLS_CLI, ...args], {
    encoding: 'utf8', timeout: 8000,
  });
  return {
    stdout: r.stdout || '',
    stderr: r.stderr || '',
    status: r.status,
    error:  r.error,
  };
}

function _renderToolsList(tools) {
  if (!tools || !tools.length) {
    return 'No personal tools yet.\n\nForge a tool, then say `/tool save <name>` to keep it across sessions.';
  }
  const lines = ['Your personal tools:'];
  for (const t of tools) {
    const desc = (t.description || '').split('\n')[0].slice(0, 80);
    const calls = t.call_count > 0 ? ` · ${t.call_count} calls` : '';
    lines.push(`  • \`${t.name}\` — ${desc}${calls}`);
  }
  return lines.join('\n');
}

function toolsListReply(_ctx) {
  // /tools — list me.* tools.
  const r = _runPersonalToolsCli(['list']);
  if (r.status !== 0) {
    return { reply: `/tools: CLI error\n${(r.stderr || r.stdout).slice(0, 300)}`,
             kind: 'tools-error' };
  }
  let arr;
  try { arr = JSON.parse(r.stdout); }
  catch { return { reply: '/tools: parse error', kind: 'tools-error' }; }
  return { reply: _renderToolsList(arr), kind: 'tools' };
}

function toolReply(ctx, sub, rest) {
  // /tool <save|rm|show|help> [args]
  const subCmd = (sub || 'help').toLowerCase();

  if (subCmd === 'help' || subCmd === '') {
    return {
      reply: 'Usage:\n' +
             '  /tool save <name> [as <alias>]   — save the most recent forge tool as me.<alias>\n' +
             '  /tool rm <name>                  — delete me.<name>\n' +
             '  /tool show <name>                — show details of me.<name>\n' +
             '  /tools                           — list all your saved tools',
      kind: 'tool-help',
    };
  }

  if (subCmd === 'save') {
    if (!rest) {
      return { reply: 'Usage: /tool save <source-name> [as <personal-name>]',
               kind: 'tool-help' };
    }
    // Parse "<source>" or "<source> as <alias>"
    const m = rest.match(/^(\S+)(?:\s+as\s+(\S+))?$/i);
    if (!m) {
      return { reply: 'Usage: /tool save <source-name> [as <personal-name>]',
               kind: 'tool-help' };
    }
    const source = m[1];
    const alias  = m[2] || null;
    const cliArgs = ['save', source, '--from', 'session'];
    if (alias) cliArgs.push('--as', alias);
    if (ctx && ctx.chatKey) cliArgs.push('--chat-key', String(ctx.chatKey));
    const r = _runPersonalToolsCli(cliArgs);
    let j; try { j = JSON.parse(r.stdout); }
    catch { return { reply: `/tool save: parse error\n${r.stdout.slice(0, 300)}`,
                     kind: 'tool-error' }; }
    if (!j.ok) {
      const err = {
        'invalid-name': 'Invalid name. Use lowercase letters, digits, underscore (max 41 chars).',
        'not-found':    `No tool '${source}' in your current session scope. Forge it first, then save.`,
        'exists':       `'me.${alias || source}' already exists. Add ' --overwrite' via the CLI to replace it.`,
      }[j.error] || (j.msg || j.error || 'unknown error');
      return { reply: `✗ /tool save: ${err}`, kind: 'tool-denied' };
    }
    return { reply: `✓ saved as \`${j.tool.name}\` — survives every session reset.`,
             kind: 'tool-saved' };
  }

  if (subCmd === 'rm') {
    if (!rest) {
      return { reply: 'Usage: /tool rm <name>', kind: 'tool-help' };
    }
    const r = _runPersonalToolsCli(['rm', rest.trim()]);
    let j; try { j = JSON.parse(r.stdout); }
    catch { return { reply: `/tool rm: parse error`, kind: 'tool-error' }; }
    if (!j.ok) {
      return { reply: `✗ /tool rm: '${rest.trim()}' not found.`,
               kind: 'tool-denied' };
    }
    return { reply: `✓ removed \`${rest.trim()}\`.`, kind: 'tool-removed' };
  }

  if (subCmd === 'show') {
    if (!rest) {
      return { reply: 'Usage: /tool show <name>', kind: 'tool-help' };
    }
    const r = _runPersonalToolsCli(['show', rest.trim()]);
    if (r.status !== 0) {
      return { reply: `✗ '${rest.trim()}' not found.`, kind: 'tool-denied' };
    }
    let t; try { t = JSON.parse(r.stdout); }
    catch { return { reply: `/tool show: parse error`, kind: 'tool-error' }; }
    const lines = [
      `\`${t.name}\``,
      `  description: ${t.description}`,
      `  runtime:     ${t.runtime}`,
      `  saved from:  ${t.saved_from_scope || '(unknown)'}`,
      `  calls:       ${t.call_count}`,
    ];
    return { reply: lines.join('\n'), kind: 'tool-show' };
  }

  return { reply: `Unknown /tool subcommand '${subCmd}'. Try /tool help.`,
           kind: 'tool-help' };
}

// ─── Layer-38 — A2A Agent Naming ──────────────────────────────────────────

// Path to the remote_endpoints dir (relative to this file's location).
const _A2A_ENDPOINTS_DIR = path.join(bridgePaths.operatorRoot(), 'cowork', 'remote_endpoints');

function _listAgentsText() {
  // Read endpoint files directly for a fast synchronous listing.
  const lines = [];
  try {
    if (require('fs').existsSync(_A2A_ENDPOINTS_DIR)) {
      const files = require('fs').readdirSync(_A2A_ENDPOINTS_DIR)
        .filter(f => f.endsWith('.json')).sort();
      for (const f of files) {
        try {
          const cfg = JSON.parse(require('fs').readFileSync(
            path.join(_A2A_ENDPOINTS_DIR, f), 'utf8'));
          const eid = cfg.endpoint_id || f.replace('.json', '');
          const lbl = cfg.label ? ` "${cfg.label}"` : '';
          const disabled = cfg.enabled === false ? ' *(disabled)*' : '';
          lines.push(`• \`${eid}\`${lbl} — ${cfg.url || '(no url)'}${disabled}`);
        } catch { /* skip corrupt files */ }
      }
    }
  } catch { /* skip on fs error */ }
  return lines.length ? lines.join('\n') : '(no remote agents configured)';
}

function agentsReply(_ctx) {
  // Fast path: read JSON files directly without spawning a subprocess.
  const r = spawnSync('python3', [A2A_CLI, 'agents', '--json'],
                      { encoding: 'utf8', timeout: 5000 });
  if (r.status !== 0 || r.error) {
    // Fallback: read endpoint files directly
    return {
      reply: '**Known A2A agents:**\n\n' + _listAgentsText() +
             '\n\nLabel setzen: `corvin-a2a label-endpoint <id> "<name>"`\n' +
             'Eigenen Namen setzen: `corvin-instance-id label "<name>"`',
      kind: 'agents',
    };
  }
  let j; try { j = JSON.parse(r.stdout || '{}'); } catch { j = {}; }

  const local = j._local || {};
  const localLabel = local.label || '(no label)';
  const localId = (local.instance_id || '?').slice(0, 8);

  const remotes = Object.entries(j).filter(([k]) => k !== '_local');
  const lines = [
    `**A2A Agents**`,
    '',
    `**Local:** \`${localLabel}\`  (id: ${localId}…)`,
    `*Set with:* \`corvin-instance-id label "Mein-Name"\``,
    '',
    remotes.length ? `**Remote agents (${remotes.length}):**` : '**Remote agents:** (none)',
  ];
  for (const [eid, info] of remotes) {
    const lbl = info.label ? ` "${info.label}"` : '';
    const disabled = info.enabled === false ? ' *(disabled)*' : '';
    lines.push(`• \`${eid}\`${lbl} — ${info.url || '(no url)'}${disabled}`);
  }
  lines.push('');
  lines.push('Label setzen: `/a2a-label <id> <name>`  ·  Senden: `/a2a <name> <instruction>`');
  return { reply: lines.join('\n'), kind: 'agents' };
}

function a2aReply(ctx, tail) {
  // /a2a                       → show agents + usage
  // /a2a <name> <instruction>  → send via A2A (owner-only)
  if (!tail || !tail.trim()) {
    return agentsReply(ctx);
  }
  if (!ctx.isOwner) {
    return { reply: '🔒 Nur der Owner kann A2A-Aufgaben senden.', kind: 'a2a-denied' };
  }
  const firstSpace = tail.search(/\s/);
  if (firstSpace === -1) {
    return {
      reply: `Usage: \`/a2a <agent-name> <instruction>\`\n\n${_listAgentsText()}`,
      kind: 'a2a-help',
    };
  }
  const agentName = tail.slice(0, firstSpace).trim();
  const instruction = tail.slice(firstSpace + 1).trim();
  if (!instruction) {
    return { reply: 'Usage: `/a2a <agent-name> <instruction>`', kind: 'a2a-help' };
  }
  const r = spawnSync('python3', [A2A_CLI, 'send', agentName, instruction],
                      { encoding: 'utf8', timeout: 35000 });
  const stderr = (r.stderr || '').trim();
  if (r.error || r.status !== 0) {
    const msg = stderr || (r.stdout || '').slice(0, 400) || `exit ${r.status}`;
    return { reply: `✗ A2A send failed:\n${msg.slice(0, 600)}`, kind: 'a2a-error' };
  }
  let j; try { j = JSON.parse(r.stdout || '{}'); } catch { j = {}; }
  if (!j.ok) {
    return {
      reply: `✗ A2A rejected (${j.status || '?'}):\n${(stderr || r.stdout || '').slice(0, 400)}`,
      kind: 'a2a-rejected',
    };
  }
  const taskShort = (j.task_id || '?').slice(0, 12);
  const dataBlock = j.data && Object.keys(j.data).length
    ? '\n\n**Result:**\n```json\n' + JSON.stringify(j.data, null, 2).slice(0, 600) + '\n```'
    : '';
  return {
    reply: `✓ A2A sent to \`${agentName}\` (task: \`${taskShort}…\`, ${j.duration_ms}ms)${dataBlock}`,
    kind: 'a2a-sent',
  };
}

function a2aLabelReply(ctx, tail) {
  // /a2a-label <endpoint-id> <label>
  if (!ctx.isOwner) {
    return { reply: '🔒 Nur der Owner kann Agenten-Labels setzen.', kind: 'a2a-denied' };
  }
  if (!tail || !tail.trim()) {
    return {
      reply: 'Usage: `/a2a-label <endpoint-id> <friendly-name>`\n' +
             'Example: `/a2a-label hetzner-a "Hetzner Server"`\n\n' +
             _listAgentsText(),
      kind: 'a2a-label-help',
    };
  }
  const firstSpace = tail.search(/\s/);
  if (firstSpace === -1) {
    return { reply: 'Usage: `/a2a-label <endpoint-id> <friendly-name>`', kind: 'a2a-label-help' };
  }
  const endpointId = tail.slice(0, firstSpace).trim();
  const label = tail.slice(firstSpace + 1).trim();
  if (!label) {
    return { reply: 'Usage: `/a2a-label <endpoint-id> <friendly-name>`', kind: 'a2a-label-help' };
  }
  const r = spawnSync('python3', [A2A_CLI, 'label-endpoint', endpointId, label],
                      { encoding: 'utf8', timeout: 5000 });
  if (r.error || r.status !== 0) {
    const msg = (r.stderr || r.stdout || '').trim();
    return { reply: `✗ label-endpoint failed: ${msg.slice(0, 400)}`, kind: 'a2a-label-error' };
  }
  return {
    reply: `✓ Label for \`${endpointId}\` set to **"${label}"**.\n` +
           `Jetzt adressierbar als: \`/a2a "${label}" <instruction>\``,
    kind: 'a2a-label-set',
  };
}

function dispatch(ctx) {
  if (!ctx || typeof ctx.text !== 'string') return null;
  const raw = ctx.text.trim();
  if (!raw.startsWith('/')) return null;

  // First-word match (e.g. "/persona browser" → cmd="/persona", arg="browser")
  const firstSpace = raw.search(/\s/);
  const head = (firstSpace === -1 ? raw : raw.slice(0, firstSpace)).toLowerCase();
  const tail = firstSpace === -1 ? '' : raw.slice(firstSpace + 1).trim();

  try {
    if (RESET_TRIGGERS.has(head)) return resetReply(ctx);
    if (WELCOME_TRIGGERS.has(head)) {
      // /willkommen → DE, everything else (/welcome /start /hi) → EN.
      const welcomeLang = head === '/willkommen' ? 'de' : 'en';
      return { reply: welcomeReply({ ...ctx, lang: welcomeLang }), kind: 'welcome', tts: true, lang: welcomeLang };
    }
    if (HELP_TRIGGERS.has(head)) return { reply: helpReply(ctx), kind: 'help' };
    if (PERSONAS_TRIGGERS.has(head)) return { reply: personasReply(ctx), kind: 'personas' };
    if (head === '/persona') return { reply: personaReply(ctx, tail), kind: 'persona' };
    if (WHOAMI_TRIGGERS.has(head)) return { reply: whoamiReply(ctx), kind: 'whoami' };
    if (SKILLS_TRIGGERS.has(head)) return { reply: skillsReply(ctx), kind: 'skills' };
    // `/settings` — one-shot dump of paths + session + system state.
    if (head === '/settings' || head === '/einstellungen' || head === '/config') {
      return settingsReply(ctx);
    }
    // `/workflow` (Layer 26 — AWP-Workflow-Bridge). Aliases: /workflows.
    if (head === '/workflow' || head === '/workflows') {
      const tailFirstSpace = tail.search(/\s/);
      const sub = (tailFirstSpace === -1 ? tail : tail.slice(0, tailFirstSpace)).toLowerCase();
      const rest = tailFirstSpace === -1 ? '' : tail.slice(tailFirstSpace + 1).trim();
      return workflowReply(ctx, sub, rest);
    }
    if (head === '/schedule') {
      // Tail layout: "<sub> <rest...>"  — sub is the first token, rest the body.
      const tailFirstSpace = tail.search(/\s/);
      const sub = (tailFirstSpace === -1 ? tail : tail.slice(0, tailFirstSpace)).toLowerCase();
      const rest = tailFirstSpace === -1 ? '' : tail.slice(tailFirstSpace + 1).trim();
      return scheduleReply(ctx, sub, rest);
    }
    if (head === '/profile') {
      const tailFirstSpace = tail.search(/\s/);
      const sub = (tailFirstSpace === -1 ? tail : tail.slice(0, tailFirstSpace)).toLowerCase();
      const rest = tailFirstSpace === -1 ? '' : tail.slice(tailFirstSpace + 1).trim();
      return profileReply(ctx, sub, rest);
    }
    // i18n — /lang sets profile.display_language to a BCP-47 code so every
    // downstream LLM-rendered surface (voice summaries, audience block,
    // disclosure card, replies) flips to that language. Sub-commands:
    //   /lang             → show current
    //   /lang <code>      → set
    //   /lang clear|reset → clear
    //   /lang list        → list registered codes
    if (head === '/lang') {
      return langReply(ctx, tail);
    }
    // Layer-12 listener-profile commands. Each /voice-user-<sub> flag
    // dispatches into voiceUserReply with the sub already split off.
    if (head === '/voice-user' || head === '/voice-user-help') {
      return voiceUserReply(ctx, 'help', '');
    }
    if (head === '/voice-user-show')    return voiceUserReply(ctx, 'show', '');
    if (head === '/voice-user-set')     return voiceUserReply(ctx, 'set', tail);
    if (head === '/voice-user-clear')   return voiceUserReply(ctx, 'clear', '');
    if (head === '/voice-user-preview') return voiceUserReply(ctx, 'preview', tail);
    if (head === '/memory') {
      const tailFirstSpace = tail.search(/\s/);
      const sub = (tailFirstSpace === -1 ? tail : tail.slice(0, tailFirstSpace)).toLowerCase();
      const rest = tailFirstSpace === -1 ? '' : tail.slice(tailFirstSpace + 1).trim();
      return memoryReply(ctx, sub, rest);
    }
    if (head === '/vault') {
      const tailFirstSpace = tail.search(/\s/);
      const sub = (tailFirstSpace === -1 ? tail : tail.slice(0, tailFirstSpace)).toLowerCase();
      const rest = tailFirstSpace === -1 ? '' : tail.slice(tailFirstSpace + 1).trim();
      return vaultReply(ctx, sub, rest);
    }
    if (head === '/all') {
      return allReply(ctx, tail);
    }
    // /debug — self-test channel for the agent (toggle the chat_profile
    // flag; the actual outbox-write capability lives in
    // phase3_cli.py's debug subcommand, gated by this flag).
    if (head === '/debug') {
      return debugReply(ctx, tail);
    }
    // Layer-11 native dialectic decision-points: 5 togglable commands.
    if (head === '/dialectic-on') return dialecticReply(ctx, 'on', '');
    if (head === '/dialectic-off') return dialecticReply(ctx, 'off', '');
    if (head === '/dialectic-status') return dialecticReply(ctx, 'status', '');
    if (head === '/dialectic-show') return dialecticReply(ctx, 'show', tail);
    if (head === '/dialectic-set') return dialecticReply(ctx, 'set', tail);
    // Layer-29 companion — /engine pins which worker engine the
    // orchestrator persona delegates to. Bridge OS-features stay on
    // Claude Code regardless.
    if (head === '/engine') return engineReply(ctx, tail);
    // Layer-14 LDD toggles: master + per-layer + presets.
    if (head === '/ldd-on') return lddReply(ctx, 'on', '');
    if (head === '/ldd-off') return lddReply(ctx, 'off', '');
    if (head === '/ldd-status') return lddReply(ctx, 'status', '');
    if (head === '/ldd-set') return lddReply(ctx, 'set', tail);
    if (head === '/ldd-preset') return lddReply(ctx, 'preset', tail);
    // Layer-16 v2 PIN-Elevation: 3 destructive-tool gating commands.
    if (head === '/auth-up')     return authReply(ctx, 'up',     tail);
    if (head === '/auth-down')   return authReply(ctx, 'down',   '');
    if (head === '/auth-status') return authReply(ctx, 'status', '');
    // ADR-0166 — Session Participation Gate (SPG): owner controls who may interact.
    if (head === '/on' || head === '/open') return spgOpenReply(ctx);
    if (head === '/off' || head === '/close') return spgCloseReply(ctx);
    if (head === '/invite') return spgInviteReply(ctx, tail);
    if (head === '/uninvite' || head === '/kick') return spgUninviteReply(ctx, tail);
    if (head === '/who') return spgWhoReply(ctx);
    // Layer-18 — capability-bundle role system.
    if (head === '/role') return rolesRoleReply(ctx, tail);
    if (head === '/roles') return rolesRolesReply(ctx);
    if (head === '/grant') return rolesGrantReply(ctx, tail);
    if (head === '/revoke') return rolesRevokeReply(ctx, tail);
    if (head === '/leave') return rolesLeaveReply(ctx);
    // Layer-19 — bot-disclosure card + /join /pass. Owner-side gives
    // a friendly redirect; the read-only-side dispatcher handles the
    // actual action via dispatchReadOnlyDisclosure().
    if (head === '/join') return disclosureOwnerJoinReply(ctx);
    if (head === '/pass') return disclosureOwnerPassReply(ctx);
    // Layer-20 — quota + audit visibility.
    if (head === '/quota') return quotaReply(ctx, tail);
    if (head === '/audit') return auditReply(ctx, tail);
    // ADR-0073 G-016 — /privacy (GDPR Art. 13/14 information right).
    if (head === '/privacy' || head === '/datenschutz') return privacyReply(ctx, tail);
    // ADR-0073 G-010 — /decision-review (EU AI Act Art. 14 human oversight, admin only).
    if (head === '/decision-review' || head === '/decision') return decisionReviewReply(ctx, tail);
    // Session goal — sticky objective injected into every LLM turn.
    if (head === '/goal') return goalReply(ctx, tail);
    // ULO (ADR-0163 M1) — user-defined learning objectives.
    if (head === '/objective' || head === '/objectives') {
      const [sub, ...restParts] = (tail || '').trim().split(/\s+/);
      return objectiveReply(ctx, sub || '', restParts.join(' '));
    }
    // /status — compact session context overview (goal, persona, schedule, LDD).
    if (head === '/status') return statusReply(ctx);
    // Layer-21 — curated proposal stack (multi-user input, owner-go).
    if (head === '/propose')   return proposeReply(ctx, tail);
    if (head === '/proposals') return proposalsReply(ctx);
    if (head === '/proposal')  return proposalReply(ctx, tail);
    // /go is handled by the daemon (it synthesises a new inbox message);
    // dispatch() can only return reply text, not trigger an LLM call.
    // Layer-17 — observer-transcript consent (owner side).
    if (head === '/consent') {
      const tailFirstSpace = tail.search(/\s/);
      const sub = (tailFirstSpace === -1 ? tail : tail.slice(0, tailFirstSpace)).toLowerCase();
      const rest = tailFirstSpace === -1 ? '' : tail.slice(tailFirstSpace + 1).trim();
      return consentReply(ctx, sub, rest);
    }
    // Layer-17 process model — /ps + /ps -a list active sessions.
    if (head === '/ps') return phase3Reply(ctx, 'ps', '', tail);
    // Layer-17 Phase-4.1 — /kill <session_id> (SIGTERM) and /kill -9
    // (SIGKILL) terminate a session. /nice adjusts priority value.
    if (head === '/kill') return phase3Reply(ctx, 'kill', '', tail);
    if (head === '/nice') return phase3Reply(ctx, 'nice', '', tail);
    // Phase-4.1.5: /sig <session_id> <SIGNAL> sends a custom signal.
    // Valid signals: KILL, PLAN, SUMMARIZE, CONTEXT_DROP, QUIET, RESUME.
    if (head === '/sig' || head === '/signal') return phase3Reply(ctx, 'sig', '', tail);
    // Layer-18 pipes — /pipe <list|create|write|read|rm|meta> [...]
    if (head === '/pipe' || head === '/pipes') {
      const tailFirstSpace = tail.search(/\s/);
      const sub = (tailFirstSpace === -1 ? tail : tail.slice(0, tailFirstSpace)).toLowerCase();
      const rest = tailFirstSpace === -1 ? '' : tail.slice(tailFirstSpace + 1).trim();
      return phase3Reply(ctx, 'pipe', sub || 'list', rest);
    }
    // Layer-19 service manager — /svc <list|deps> [...]
    if (head === '/svc' || head === '/services') {
      const tailFirstSpace = tail.search(/\s/);
      const sub = (tailFirstSpace === -1 ? tail : tail.slice(0, tailFirstSpace)).toLowerCase();
      const rest = tailFirstSpace === -1 ? '' : tail.slice(tailFirstSpace + 1).trim();
      return phase3Reply(ctx, 'svc', sub || 'list', rest);
    }
    // Layer-20 context budget — /budget <show|policy> [...]
    if (head === '/budget') {
      const tailFirstSpace = tail.search(/\s/);
      const sub = (tailFirstSpace === -1 ? tail : tail.slice(0, tailFirstSpace)).toLowerCase();
      const rest = tailFirstSpace === -1 ? '' : tail.slice(tailFirstSpace + 1).trim();
      return phase3Reply(ctx, 'budget', sub || 'show', rest);
    }
    // Layer-27 — personal tools (the user's permanent me.* forge library).
    // /tools lists them; /tool <save|rm|show|help> manages them.
    if (head === '/tools') return toolsListReply(ctx);
    if (head === '/tool') {
      const tailFirstSpace = tail.search(/\s/);
      const sub = (tailFirstSpace === -1 ? tail : tail.slice(0, tailFirstSpace)).toLowerCase();
      const rest = tailFirstSpace === -1 ? '' : tail.slice(tailFirstSpace + 1).trim();
      return toolReply(ctx, sub, rest);
    }
    // Layer-38 — A2A agent naming + send.
    // /agents            list local + remote agents
    // /a2a <name> <msg>  send instruction to a named agent
    // /a2a-label <id> <label>  set a friendly label on an endpoint
    if (head === '/agents') return agentsReply(ctx);
    if (head === '/a2a') return a2aReply(ctx, tail);
    if (head === '/a2a-label') return a2aLabelReply(ctx, tail);
  } catch (e) {
    return { reply: `cowork-cmd error: ${e.message || e}`, kind: 'error' };
  }
  return null;
}

module.exports = {
  dispatch,
  // Layer-17 — daemon-side hook used inside the read-only branch BEFORE
  // maybeForwardAsObserver. Returns null for non-/consent /share text.
  dispatchReadOnlyConsent,
  // Layer-19 — daemon-side hooks: /join + /pass for read-only senders,
  // plus card rendering helpers for the first-encounter disclosure flow.
  dispatchReadOnlyDisclosure,
  disclosureCardText,
  disclosureHasSeen,
  disclosureMarkSeen,
  // Layer-21 — daemon-side hooks: /propose for read-only senders, plus
  // proposalsBuildGoPayload for the daemon's /go handling (consume + format).
  dispatchReadOnlyProposal,
  proposalsBuildGoPayload,
  // public — daemons import these directly to gate authOk()
  getAudience,
  setAudience,
  getObserverVisibility,
  getDebugEnabled,
  setDebugEnabled,
  // ADR-0073 G-016 / G-010 — privacy notice + decision review.
  privacyReply,
  decisionReviewReply,
  // exposed for tests
  _internal: {
    listPersonas, getPersona, currentPersonaForChat,
    bindPersona, unbindPersona, coworkInstalled,
    getAudience, setAudience,
    COWORK_BUNDLE_DIR, COWORK_USER_DIR,
    // Layer-29 companion — exposed so the dispatcher E2E can drive
    // /engine without going through the full dispatch() entry point.
    engineReply,
    ENGINE_SWITCH_CLI,
  },
};
