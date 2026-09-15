// logger.js — uniform debug logger for all bridge daemons.
//
// Mirrors operator/bridges/shared/debug_logging.py so a single
// `CORVIN_DEBUG=1` env-flag toggles verbosity across the whole stack.
// Stays dependency-free (Node-stdlib only) so it can be required from
// any bridge daemon without pulling in npm side-effects.
//
// Env (all optional; sane defaults):
//   CORVIN_DEBUG       1/true/on → debug level; 0/off (default OFF) → info
//   CORVIN_LOG_LEVEL   debug|info|warn|error  (overrides CORVIN_DEBUG)
//   CORVIN_LOG_FILE    explicit log path; default <corvin_home>/logs/corvin.log
//   CORVIN_LOG_STDERR  1 (default) → also log to stderr
//   CORVIN_LOG_REDACT  1 (default) → mask secret-looking strings
//   CORVIN_LOG_BODY_CAP chars of content kept by bodyExcerpt (default 200)
//
//
// Compliance contract (CLAUDE.md §Compliance): never log message bodies,
// transcripts, or secret values. Use bodyExcerpt() for content flavour
// (capped + redacted). For voice transcription metadata, log channel +
// duration only — never the transcript text.

const fs = require("fs");
const path = require("path");
const os = require("os");

const LEVELS = { debug: 10, info: 20, warn: 30, error: 40 };

function envTruthy(name, fallback) {
  const raw = process.env[name];
  if (raw === undefined || raw === null) return fallback;
  return ["1", "true", "yes", "on", "y"].includes(String(raw).trim().toLowerCase());
}

function resolveEnv(canonical, legacy) {
  if (process.env[canonical] !== undefined) return process.env[canonical];
  if (legacy && process.env[legacy] !== undefined) return process.env[legacy];
  return undefined;
}

function resolveLevel() {
  const explicit = process.env["CORVIN_LOG_LEVEL"];
  if (explicit && LEVELS[String(explicit).trim().toLowerCase()] !== undefined) {
    return String(explicit).trim().toLowerCase();
  }
  return envTruthy("CORVIN_DEBUG", false) ? "debug" : "info";
}

function resolveCorvinHome() {
  const env = process.env["CORVIN_HOME"];
  if (env) return env;
  // Walk up to find a sibling .corvin; fall back to ~/.corvin.
  let here = __dirname;
  for (let i = 0; i < 8; i++) {
    const a = path.join(here, ".corvin");
    if (fs.existsSync(a)) return a;
    const parent = path.dirname(here);
    if (parent === here) break;
    here = parent;
  }
  return path.join(os.homedir(), ".corvin");
}

function resolveLogFile() {
  const explicit = process.env["CORVIN_LOG_FILE"];
  if (explicit) return explicit;
  return path.join(resolveCorvinHome(), "logs", "corvin.log");
}

// ── Redaction ────────────────────────────────────────────────────────
const SECRET_PATTERNS = [
  // JWTs
  [/eyJ[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{8,}/g, "[REDACTED_JWT]"],
  // common provider keys
  [/\b(sk-(?:ant-)?[A-Za-z0-9_\-]{20,})\b/g, "[REDACTED_KEY]"],
  [/\bgh[pousr]_[A-Za-z0-9]{20,}\b/g, "[REDACTED_KEY]"],
  [/\bxox[abprs]-[A-Za-z0-9\-]{10,}\b/g, "[REDACTED_KEY]"],
  [/\bAKIA[0-9A-Z]{16}\b/g, "[REDACTED_KEY]"],
  [/\bAIza[0-9A-Za-z_\-]{20,}\b/g, "[REDACTED_KEY]"],
  // Bearer / Authorization
  [/\bauthorization\s*[:=]\s*(?:bearer|token|basic)?\s*[^\s"',}]+/gi, "Authorization: [REDACTED]"],
  [/\bbearer\s+[A-Za-z0-9_\-\.=]{8,}/gi, "Bearer [REDACTED]"],
  // key=value envelopes
  [/(['"]?(?:api[_-]?key|password|token|secret|auth)['"]?\s*[:=]\s*['"]?)([^'",}\s]+)/gi, "$1[REDACTED]"],
];

function redact(text) {
  if (text === null || text === undefined) return "";
  let s = typeof text === "string" ? text : tryJSON(text);
  if (!envTruthy("CORVIN_LOG_REDACT", true)) return s;
  for (const [pat, repl] of SECRET_PATTERNS) {
    s = s.replace(pat, repl);
  }
  return s;
}

function tryJSON(obj) {
  try {
    return JSON.stringify(obj);
  } catch {
    return String(obj);
  }
}

function bodyExcerpt(text, cap) {
  if (text === null || text === undefined) return "";
  let s = typeof text === "string" ? text : tryJSON(text);
  if (cap === undefined) {
    cap = parseInt(process.env["CORVIN_LOG_BODY_CAP"] || "200", 10);
    if (Number.isNaN(cap)) cap = 200;
  }
  s = redact(s);
  if (s.length <= cap) return s;
  return `${s.slice(0, cap)}…(+${s.length - cap} more chars)`;
}

// ── File sink (lazy + best-effort) ───────────────────────────────────
let _fileStream = null;
let _fileStreamPath = null;

function getFileStream() {
  const target = resolveLogFile();
  if (_fileStream && _fileStreamPath === target) return _fileStream;
  try {
    fs.mkdirSync(path.dirname(target), { recursive: true });
    _fileStream = fs.createWriteStream(target, { flags: "a", encoding: "utf-8" });
    _fileStreamPath = target;
  } catch (e) {
    // File-sink failure must never block the daemon. Stderr-only mode is fine.
    process.stderr.write(`[logger] file sink init failed: ${e.message}\n`);
    _fileStream = null;
  }
  return _fileStream;
}

// ── Core logger factory ──────────────────────────────────────────────
function emit(tag, level, args) {
  const cur = resolveLevel();
  if (LEVELS[level] < LEVELS[cur]) return;
  const ts = new Date().toISOString();
  const parts = args.map((a) =>
    typeof a === "string" ? a : tryJSON(a)
  );
  let line = `${ts} [${level.toUpperCase()}] [${tag}] ${parts.join(" ")}`;
  if (envTruthy("CORVIN_LOG_REDACT", true)) {
    for (const [pat, repl] of SECRET_PATTERNS) line = line.replace(pat, repl);
  }
  if (envTruthy("CORVIN_LOG_STDERR", true)) {
    // Daemons read stdout for IPC framing; stderr is the safe channel.
    process.stderr.write(line + "\n");
  }
  const stream = getFileStream();
  if (stream) {
    try { stream.write(line + "\n"); } catch (_) { /* best-effort */ }
  }
}

function makeLogger(tag) {
  // Backward compat: the old call-form is `log(...)` → INFO level. We
  // keep that behaviour by making the returned function default to INFO
  // and expose `.debug/.info/.warn/.error` as properties so daemons can
  // upgrade gradually.
  const fn = function log(...args) { emit(tag, "info", args); };
  fn.debug = (...args) => emit(tag, "debug", args);
  fn.info  = (...args) => emit(tag, "info",  args);
  fn.warn  = (...args) => emit(tag, "warn",  args);
  fn.error = (...args) => emit(tag, "error", args);
  fn.tag = tag;
  return fn;
}

function describe() {
  return {
    level: resolveLevel(),
    file: resolveLogFile(),
    stderr: envTruthy("CORVIN_LOG_STDERR", true),
    redact: envTruthy("CORVIN_LOG_REDACT", true),
  };
}

function isDebugEnabled() {
  return resolveLevel() === "debug";
}

module.exports = {
  makeLogger,
  redact,
  bodyExcerpt,
  describe,
  isDebugEnabled,
  currentLogFile: resolveLogFile,
};
