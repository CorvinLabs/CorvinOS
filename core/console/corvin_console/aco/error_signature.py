"""ACO — error-signature extraction + PII scrubbing (ADR-0179).

The privacy-and-localization foundation of telemetry-driven repair. Turns a raw
log/traceback into a *stable, secret-safe signature*:

  * **stable** — keyed on exception type + repo-relative file + function (NOT line
    numbers, NOT message values), so the same bug recurs to the same signature
    across versions and machines, and we can COUNT it.
  * **secret-safe** — the message template is scrubbed of every PII/secret shape
    (emails, IDs, home paths, tokens, numbers) BEFORE it leaves a machine. Nothing
    that crosses the telemetry channel can carry a prompt, a transcript, or a
    name. This is the GDPR floor for ADR-0179.
  * **localized** — installed-package paths (``corvin_console/aco/x.py`` from a
    foreign user's ``site-packages``) are mapped back to repo-relative paths
    (``core/console/corvin_console/aco/x.py``) so a diagnosis can name the file to
    edit. A frame we cannot map → not localizable → report-only (never a patch).

Pure functions only — no I/O, no network. Trivially testable.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

# Installed top-level package → repo-relative source root. Lets us turn a foreign
# machine's site-packages traceback into a path that exists in THIS repo.
_PKG_TO_REPO: dict[str, str] = {
    "corvin_console": "core/console/corvin_console",
    "corvin_gateway": "core/gateway/corvin_gateway",
    "corvin_mcp": "core/mcp/corvin_mcp",
    "forge": "operator/forge",
    "agents": "operator/bridges/shared/agents",
}
# Segments that are already repo-relative roots (in-tree runs, not installed).
_REPO_ROOTS = ("core/", "operator/", "shared/", "ops/")
# Match a repo root ONLY at a path boundary (start-of-string or right after '/'),
# so a substring like ".../encore/x.py" cannot false-match "core/" (F11).
_REPO_ROOT_RE = re.compile(
    r"(?:^|/)(" + "|".join(re.escape(r) for r in _REPO_ROOTS) + r")"
)

# ── PII / secret scrubbing ──────────────────────────────────────────────────────
# Order matters: most-specific first. Every pattern collapses a value to a typed
# placeholder so only the STRUCTURAL shape of a message survives.
# Order matters: most-specific first so specific shapes aren't eaten by the
# generic hex/number collapses at the end.
_SCRUB: list[tuple[re.Pattern, str]] = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "<email>"),
    (re.compile(r"(?i)\b(token|secret|key|password|passwd|bearer|authorization|auth|api[_-]?key)\b"
                r"\s*[:=]?\s*\S+"), "<credential>"),            # keyword + optional sep + value
    (re.compile(r"\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]+"), "<jwt>"),
    (re.compile(r"\b(?:sk|pk|rk|ghp|gho|ghs|xox[baprs]|AKIA|ASIA)[_-][A-Za-z0-9_-]{8,}"),
     "<token>"),                                               # sk_live_, ghp_, slack, aws…
    (re.compile(r"\\\\[^\s'\"]+"), "<unc-path>"),              # windows UNC \\server\share
    (re.compile(r"[A-Za-z]:\\[^\s'\"]+"), "<path>"),           # windows drive path
    (re.compile(r"~?/(?:home|Users|root|opt|tmp|var|etc|srv|mnt)/[^\s'\"]*"), "<path>"),
    (re.compile(r"~/[^\s'\"]*"), "<path>"),                    # ~/...
    (re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"), "<uuid>"),
    (re.compile(r"\b(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}\b"), "<mac>"),
    (re.compile(r"\b(?:[0-9a-fA-F]{0,4}:){2,}[0-9a-fA-F]{0,4}\b"), "<ipv6>"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "<ip>"),
    (re.compile(r"\b\d{12,}\b"), "<snowflake>"),               # discord-id-ish (before hex)
    (re.compile(r"\b[0-9a-fA-F]{16,}\b"), "<hex>"),            # long hex / hashes
    (re.compile(r"\b[A-Za-z0-9_\-+/]{24,}={0,2}\b"), "<blob>"),  # long base64-ish secret
    (re.compile(r"\b\d+\b"), "<n>"),                           # any remaining number
]


def scrub(text: str, *, max_len: int = 200) -> str:
    """Collapse every PII/secret/value shape to a typed placeholder. Idempotent
    enough for safety; the goal is that NOTHING value-bearing survives."""
    if not text:
        return ""
    out = text
    for pat, repl in _SCRUB:
        out = pat.sub(repl, out)
    out = re.sub(r"\s+", " ", out).strip()
    return out[:max_len]


def to_repo_path(raw_path: str) -> str | None:
    """Map an absolute/installed path to a repo-relative source path, or None if
    it is not a CorvinOS source file we can localize."""
    if not raw_path:
        return None
    norm = raw_path.replace("\\", "/")
    # already repo-relative? (anchored at a '/' boundary — see _REPO_ROOT_RE)
    m = _REPO_ROOT_RE.search(norm)
    if m:
        return norm[m.start(1):]
    # installed package → repo root
    segs = norm.split("/")
    for i, seg in enumerate(segs):
        if seg in _PKG_TO_REPO and i + 1 < len(segs):
            return _PKG_TO_REPO[seg] + "/" + "/".join(segs[i + 1:])
    return None


# ── signature ─────────────────────────────────────────────────────────────────

@dataclass
class ErrorSignature:
    signature: str               # stable hash (exc_type|repo_file|func)
    exc_type: str
    message_template: str        # scrubbed — safe to transmit
    top_repo_file: str | None    # localization target (repo-relative) or None
    func: str
    frames: list[str] = field(default_factory=list)  # repo-relative "file:func" frames
    localized: bool = False

    def to_dict(self) -> dict:
        return {
            "signature": self.signature, "exc_type": self.exc_type,
            "message_template": self.message_template, "top_repo_file": self.top_repo_file,
            "func": self.func, "frames": self.frames, "localized": self.localized,
        }


_FRAME_RE = re.compile(r'File "([^"]+)", line (\d+), in (\S+)')
# A final exception line:  "corvin_console.aco.X.SomeError: message here"
_EXC_RE = re.compile(r"^(?P<type>[A-Za-z_][\w.]*(?:Error|Exception|Warning|Timeout)):"
                     r"\s*(?P<msg>.*)$")


def _hash(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


def parse_tracebacks(log_text: str) -> list[ErrorSignature]:
    """Extract one ErrorSignature per Python traceback in the log. The signature
    is keyed on the DEEPEST repo frame (where the bug most likely lives)."""
    sigs: list[ErrorSignature] = []
    if not log_text:
        return sigs
    blocks = re.split(r"(?m)^(?=Traceback \(most recent call last\):)", log_text)
    for block in blocks:
        if "Traceback (most recent call last):" not in block:
            continue
        frames_raw = _FRAME_RE.findall(block)
        if not frames_raw:
            continue
        repo_frames: list[tuple[str, str]] = []
        for path, _ln, func in frames_raw:
            rp = to_repo_path(path)
            if rp:
                repo_frames.append((rp, func))
        # exception type + message = first matching exc line after the frames
        exc_type, msg = "UnknownError", ""
        for line in reversed(block.splitlines()):
            m = _EXC_RE.match(line.strip())
            if m:
                exc_type = m.group("type").split(".")[-1]
                msg = m.group("msg")
                break
        top_file, func = (repo_frames[-1] if repo_frames else (None, "?"))
        sig = ErrorSignature(
            signature=_hash(exc_type, top_file or "?", func),
            exc_type=exc_type,
            message_template=scrub(msg),
            top_repo_file=top_file,
            func=func,
            frames=[f"{f}:{fn}" for f, fn in repo_frames],
            localized=top_file is not None,
        )
        sigs.append(sig)
    return sigs


def parse_error_lines(log_text: str) -> list[ErrorSignature]:
    """Fallback for logs WITHOUT tracebacks: bare ``ERROR`` lines. These are never
    localizable (no frame) → they can only ever be report-only signals."""
    sigs: list[ErrorSignature] = []
    for line in (log_text or "").splitlines():
        if re.search(r"\b(ERROR|CRITICAL)\b", line) and "Traceback" not in line:
            tmpl = scrub(line)
            sigs.append(ErrorSignature(
                signature=_hash("logline", tmpl[:60]), exc_type="LogError",
                message_template=tmpl, top_repo_file=None, func="?", localized=False))
    return sigs


def extract_signatures(log_text: str) -> list[ErrorSignature]:
    """All signatures in a log: tracebacks (localizable) + bare error lines."""
    return parse_tracebacks(log_text) + parse_error_lines(log_text)
