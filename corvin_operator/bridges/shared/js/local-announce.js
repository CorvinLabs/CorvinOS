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
    // A missing program (notify-send / speak.sh on Windows, a stale python
    // path) is reported ASYNCHRONOUSLY as an 'error' event, not thrown -- and
    // an unhandled 'error' event crashed the whole daemon on the next inbound
    // message. Every spawn here therefore gets an error listener.
    const fire = (cmd, args) => {
      const child = spawn(cmd, args, { detached: true, stdio: 'ignore' });
      child.on('error', (e) => { if (logger) logger(`announce failed: ${e.message}`); });
      child.unref();
    };
    try {
      if (mode === 'earcon') {
        fire(require('./python_bin').pythonBin(), [path.join(pluginRoot, 'scripts', 'earcon.py'), 'play', 'tool']);
      } else if (mode === 'voice') {
        const intro = `${channelLabel}-message: ${snippet}`;
        fire(path.join(pluginRoot, 'scripts', 'speak.sh'), ['--lang', 'de', '--text', intro]);
      } else if (mode === 'text') {
        fire('notify-send', ['-a', channelLabel, String(payload.from), snippet]);
      }
    } catch (e) {
      if (logger) logger(`announce failed: ${e.message}`);
    }
  };
}

module.exports = { makeAnnouncer };
