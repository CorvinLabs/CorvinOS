// settings.js — Hot-Reload-Accessor for eine bridges/<channel>/settings.json.
//
// Read-pathe in daemons MÜSSEN currentSettings() usen, damit
// Whitelist-/Rate-Limit-/Profile-Edits without daemon restart greifen
// (Konvention dokumentiert in CLAUDE.md). Write-pathe may weiterhin
// einen Boot-Snapshot mutaten und over saveSettings() persistieren —
// der next currentSettings()-Aufruf sieht die neue mtime und reads
// fresh.

const fs = require('fs');

function makeSettingsAccessor(settingsFile, logger) {
  let cache = null;
  let cachedMtime = 0;

  function loadSettings() {
    try { return JSON.parse(fs.readFileSync(settingsFile, 'utf8')); }
    catch { return {}; }
  }

  function currentSettings() {
    try {
      const m = fs.statSync(settingsFile).mtimeMs;
      if (m !== cachedMtime) {
        cache = JSON.parse(fs.readFileSync(settingsFile, 'utf8'));
        if (cachedMtime > 0 && logger) logger(`settings.json reloaded (mtime=${m})`);
        cachedMtime = m;
      }
    } catch {
      // file verschwunden / unparsebar → letzten guten Stand behalten.
      // (Bewusste Wahl gegenover "leeres Dict liefern": eine kurz
      // korrupte file darf die Whitelist nicht offen aufmachen.)
    }
    return cache || {};
  }

  function saveSettings(obj) {
    // Atomic write: rename ist auf POSIX-FS atomar — kein halbgeschriebener
    // Zustand kann von einem parallelen Reader gesehen werden.
    const tmp = settingsFile + '.tmp';
    fs.writeFileSync(tmp, JSON.stringify(obj, null, 2));
    fs.renameSync(tmp, settingsFile);
  }

  return { loadSettings, currentSettings, saveSettings };
}

module.exports = { makeSettingsAccessor };
