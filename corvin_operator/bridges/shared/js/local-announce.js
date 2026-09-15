// local-announce.js — Spawn von earcon/voice/notify-send beim Inbound.
//
// Vorher: ~15 Zeilen pro daemon, leicht unterschiedliche Voice-Intros
// ("Neue message:" / "Discord-message:" / "Slack-message:"). Hier
// zentralisiert mit einheitlicher Form "<Label>-message:".

const path = require('path');
const { spawn } = require('child_process');

/**
 * @param {object} cfg
 * @param {string}   cfg.pluginRoot      — absoluter path zum operator/voice/-Root
 * @param {string}   cfg.channelLabel    — User-sichtbarer Channel-Name ("Telegram")
 * @param {function} cfg.currentSettings — Hot-Reload-Accessor
 * @param {function} [cfg.logger]
 * @returns {function} announce(payload, kind) — best-effort, blockiert nie.
 */
function makeAnnouncer({ pluginRoot, channelLabel, currentSettings, logger }) {
  return function announce(payload, kind) {
    const mode = currentSettings().local_announce_inbound || 'off';
    if (mode === 'off') return;
    const snippet = (payload.text || payload.caption || '').slice(0, 200) || `[${kind}]`;
    try {
      if (mode === 'earcon') {
        spawn('python3', [path.join(pluginRoot, 'scripts', 'earcon.py'), 'play', 'tool'],
          { detached: true, stdio: 'ignore' }).unref();
      } else if (mode === 'voice') {
        const intro = `${channelLabel}-message: ${snippet}`;
        spawn(path.join(pluginRoot, 'scripts', 'speak.sh'),
              ['--lang', 'de', '--text', intro],
              { detached: true, stdio: 'ignore' }).unref();
      } else if (mode === 'text') {
        spawn('notify-send', ['-a', channelLabel, String(payload.from), snippet],
          { detached: true, stdio: 'ignore' }).unref();
      }
    } catch (e) {
      if (logger) logger(`announce failed: ${e.message}`);
    }
  };
}

module.exports = { makeAnnouncer };
