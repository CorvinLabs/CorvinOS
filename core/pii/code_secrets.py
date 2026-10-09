"""
Credential gate for SOURCE / CONFIG text (diffs, file snippets) — ADR-2241.

:mod:`core.pii.sensitive` is tuned for prose (learned fields, skill bodies): its
``credential_assignment`` needs a word boundary before the key, so the shapes real
config files use — ``DB_PASSWORD=…``, ``SECRET_KEY=…``, ``"accessToken": "…"``,
``postgres://app:pw@db``, a PEM body without its BEGIN line — pass it. Widening that
detector instead would change every prose caller (it hid ordinary skill bodies such
as ``{"token": null}`` in review), so source text gets its own gate here, on top of
the value-shaped detectors of :mod:`core.pii.sensitive`.

Same contract as ``sensitive``: a GATE, not a scrubber — callers drop the whole
text on any hit — and FAIL-CLOSED: a scan error raises
:class:`~core.pii.sensitive.PIIDetectionFailedClosed`, never "clean".

Every pattern is linear (no nested quantifiers over overlapping classes); the JWT
matcher is the linear :mod:`core.pii.jwt_scan` one without boundaries, as in
``core.forge_bundle.validate``.
"""
from __future__ import annotations

import re

from core.pii.jwt_scan import LinearJwtPattern
from core.pii.sensitive import PIIDetectionFailedClosed, detect_named_types

# Value-shaped detectors of core.pii.sensitive that hold for code as for prose.
_SENSITIVE_TYPES = frozenset({
    "private_key_block", "aws_access_key", "aws_secret_key", "github_token",
    "github_pat", "slack_token", "google_api_key", "prefixed_secret_key", "bearer_token",
})

# A key NAME holds a credential when it CONTAINS one of these words, anywhere in the
# identifier (DB_PASSWORD, dbPassword, spring.datasource.password, client-secret).
# Matched on whole identifier tokens in Python, not with an [ident]*WORD[ident]* regex:
# that form backtracks quadratically ("passpass…" x20 KB took 1.4 s).
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_.\-]*")
# Matched on the lower-cased identifier alone (short, no backtracking across the line).
# Names that only TALK about a credential (secret_id, token_count, credential_env,
# max_tokens, passed, bypass) are not keys holding one.
_KEY_NAME = re.compile(
    r"password|passwd|passphrase|pwd|(?<![a-z])pass(?![a-z])"
    r"|secret(?![a-z]|_?(?:id|name|ref|env|path|file|store|manager|kind|type)\b)"
    r"|token(?![a-z]|_?(?:count|id|kid|fingerprint|type|url|endpoint|env|name|path|file|budget|limit|usage|len|length|cost)\b)"
    r"|api_?key(?!_?(?:env|name|id|ref|path|file)\b)|access_?key|auth_?key|private_?key"
    r"|credential(?![a-z]|_?(?:env|name|id|ref|path|file|type|kind)\b)")
_PLACEHOLDER = re.compile(r"(?i)(?:none|null|nil|true|false|undefined|changeme|x{3,}|\*+|<[^>]*>|\$\{?\w+\}?)(?![\w])")
# What follows the key, matched AT the key's end (re.match: no scan, no backtracking over it)
_AFTER_QUOTED = re.compile(r"[\"']?[ \t]*(?::|=|=>|:=)[ \t]*[\"'`]")
_AFTER_ENV = re.compile(r"[ \t]*=[ \t]*(?=[^\s\"'`])")
_AFTER_YAML = re.compile(r"[ \t]*:[ \t]+(?=[^\s#{\[|>&*!])")
_AFTER_YAML_BLOCK = re.compile(r"[ \t]*:[ \t]*[|>][-+]?[ \t]*$")
_LINE_LEAD = re.compile(r"[+\- ]?[ \t]*(?:export[ \t]+)?")
# An unquoted value that is code, not a literal: a type (`token: str`,
# `pwd: Optional[str] = None`), a call/subscript (`x = get_secret()`), or — after a
# spaced `=` — a bare name (`password = settings.password`), or a short number (`max_tokens: 100`).
_TYPE_WORD = re.compile(r"(?:str|int|bool|float|bytes|dict|list|tuple|set|Any|Optional|Union|None|"
                        r"string|number|boolean|object|unknown|any|void|never|Array|Record|Promise)\b")
_CODE_CHARS = re.compile(r"[^\s]*[()\[\]{}]")
_BARE_NAME = re.compile(r"[A-Za-z_][\w.]*(?:\s|$)")
_SHORT_NUMBER = re.compile(r"[\d.]{1,7}[,;]?(?:\s|$)")
_NAME_ARG = re.compile(r"[A-Za-z_][\w.]*[ \t]*[,)]")   # a keyword argument: f(token=tok, ...)


def _is_code(line: str, pos: int, spaced: bool) -> bool:
    return bool(_TYPE_WORD.match(line, pos) or _CODE_CHARS.match(line, pos)
                or _SHORT_NUMBER.match(line, pos) or _NAME_ARG.match(line, pos) or (spaced and _BARE_NAME.match(line, pos)))


def _value_ok(line: str, pos: int) -> bool:
    """A literal of >= 2 chars starts at ``pos`` that is not a placeholder."""
    v = line[pos:pos + 64]
    return len(v.split()[0] if v.split() else "") >= 2 and not _PLACEHOLDER.match(v)


def _credential_key_hit(line: str) -> "str | None":
    lead = _LINE_LEAD.match(line)
    first = lead.end() if lead else 0
    for m in _IDENT.finditer(line):
        if not _KEY_NAME.search(m.group().lower()):
            continue
        end = m.end()
        q = _AFTER_QUOTED.match(line, end)
        if q and _value_ok(line, q.end()):
            return "quoted_credential"
        if m.start() != first:
            continue                     # the unquoted forms are line-anchored
        e = _AFTER_ENV.match(line, end)
        if e and _value_ok(line, e.end()) and not _is_code(line, e.end(), e.group() != "="):
            return "env_credential"
        y = _AFTER_YAML.match(line, end)
        if y and _value_ok(line, y.end()) and not _is_code(line, y.end(), False):
            return "yaml_credential"
        if _AFTER_YAML_BLOCK.match(line, end):
            return "yaml_block_credential"
    return None


_CODE_DETECTORS: list[tuple[str, "re.Pattern[str]"]] = [
    # Password inside a URL:  scheme://user:password@host
    ("url_userinfo_password", re.compile(r"(?i)\b[a-z][a-z0-9+.\-]*://[^\s/:@'\"]*:[^\s/@'\"]{2,}@")),
    # Vendor tokens sensitive.py does not know
    ("vendor_token", re.compile(
        r"\b(?:npm_[A-Za-z0-9]{36}|hf_[A-Za-z0-9]{30,}|glpat-[A-Za-z0-9_\-]{20,}|"
        r"[rs]k_live_[A-Za-z0-9]{16,}|SG\.[\w\-]{16,}\.[\w\-]{16,}|SK[0-9a-f]{32})\b")),
    ("azure_account_key", re.compile(r"(?i)\bAccountKey=[A-Za-z0-9+/]{20,}={0,2}")),
    # A bare base64 line (a PEM / key body whose BEGIN line is outside the snippet)
    ("base64_key_body", re.compile(r"(?m)^[+\- ]?[A-Za-z0-9+/]{60,}={0,2}\s*$")),
]

# .netrc entry:  machine <host> login <user> password <value>  (checked only on such a line)
_NETRC = re.compile(r"\bpassword[ \t]+\S{2,}")
_JWT = LinearJwtPattern(10, 10, 10, start_boundary=False, end_boundary=False)


def detect_code_secrets(text: str) -> list[str]:
    """Names of every credential detector that fired on ``text`` (source/config).

    Fail-closed: any error raises :class:`PIIDetectionFailedClosed`; an empty list
    means "scanned cleanly, nothing found".
    """
    if not isinstance(text, str):
        raise PIIDetectionFailedClosed(f"detect_code_secrets requires str, got {type(text).__name__}")
    try:
        found = list(detect_named_types(text, _SENSITIVE_TYPES))
        for name, pattern in _CODE_DETECTORS:
            if pattern.search(text):
                found.append(name)
        for line in text.split("\n"):
            hit = _credential_key_hit(line)
            if not hit and "machine" in line and _NETRC.search(line):
                hit = "netrc_password"
            if hit:
                found.append(hit)
                break
        if _JWT.search(text) is not None:
            found.append("jwt")
    except PIIDetectionFailedClosed:
        raise
    except Exception as exc:  # noqa: BLE001 — fail closed on ANY scan failure
        raise PIIDetectionFailedClosed(f"code-secret scan failed: {type(exc).__name__}") from exc
    return found
