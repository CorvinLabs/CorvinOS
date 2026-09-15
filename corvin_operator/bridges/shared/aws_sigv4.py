"""Minimal AWS SigV4 request signer + credential/region resolution (stdlib only).

Why this exists: on a Bedrock-authenticated install (``CLAUDE_CODE_USE_BEDROCK=1``)
the models Claude Code can actually reach are the ones Bedrock grants this
account — not the ones ``api.anthropic.com`` lists, and not a list frozen into a
YAML at release time. Asking Bedrock means calling ``bedrock:ListFoundationModels``
and ``bedrock:ListInferenceProfiles``, which require SigV4-signed requests.
CorvinOS has no boto3 and no aws-CLI dependency (verified absent on this host),
and adding one for two GETs is a heavy price, so the signature is computed here.

Scope is deliberately narrow — signed GET only, no retries, no paginator, no
service model. Anything beyond that belongs in boto3, not here.

Credential chain (a documented subset of boto3's, in boto3's order):
  1. ``AWS_ACCESS_KEY_ID`` / ``AWS_SECRET_ACCESS_KEY`` / ``AWS_SESSION_TOKEN`` env
  2. the shared credentials file profile (``~/.aws/credentials``)
  3. the config profile's ``credential_process`` (``~/.aws/config``)

Step 3 runs an operator-configured external helper. That is the mechanism's whole
purpose — it is how a short-lived Bedrock token gets refreshed, and boto3 runs it
unprompted too — but it is a subprocess, so it is only reached when steps 1–2
yield nothing usable, it is bounded by a timeout, and its stdout is parsed as
JSON and never logged. ``sso_*`` and ``role_arn`` profiles are NOT supported:
they need a token exchange this module deliberately does not implement, and are
reported as an unsupported-profile reason rather than a failure.

Never raises on credential resolution: returns ``None`` plus a human reason, so
a caller can degrade to another model source instead of losing the page.

No secret ever reaches a log line, an audit record, or a return value's reason
string — only the credential SOURCE (an enum-ish label) is surfaced.
"""
from __future__ import annotations

import configparser
import datetime as _dt
import hashlib
import hmac
import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

_ALGORITHM = "AWS4-HMAC-SHA256"

#: Bound on the operator-configured ``credential_process`` helper. Long enough
#: for an interactive-less token refresh, short enough that a hung helper cannot
#: pin a console request open.
_CREDENTIAL_PROCESS_TIMEOUT = 30.0

#: Refresh a process-sourced credential this many seconds BEFORE its stated
#: expiry, so a request signed at the boundary is not rejected in flight.
_EXPIRY_SKEW_SECONDS = 120.0


@dataclass(frozen=True)
class Credentials:
    access_key: str
    secret_key: str
    session_token: str = ""
    #: Where the credential came from — "env", "shared_credentials_file",
    #: "credential_process". A label for the operator, never a secret.
    source: str = ""
    #: Unix timestamp this credential stops being valid, 0.0 when unknown
    #: (static keys carry no expiry).
    expires_at: float = 0.0

    def is_expired(self, *, skew: float = _EXPIRY_SKEW_SECONDS) -> bool:
        return bool(self.expires_at) and time.time() >= (self.expires_at - skew)


# ---------------------------------------------------------------------------
# Region + profile
# ---------------------------------------------------------------------------


def active_profile() -> str:
    return os.environ.get("AWS_PROFILE") or os.environ.get("AWS_DEFAULT_PROFILE") or "default"


def _shared_credentials_path() -> Path:
    override = os.environ.get("AWS_SHARED_CREDENTIALS_FILE", "")
    return Path(override).expanduser() if override else Path.home() / ".aws" / "credentials"


def _config_path() -> Path:
    override = os.environ.get("AWS_CONFIG_FILE", "")
    return Path(override).expanduser() if override else Path.home() / ".aws" / "config"


def _read_ini(path: Path) -> configparser.ConfigParser:
    # interpolation=None is load-bearing on Windows: a `credential_process` line
    # holding `%USERPROFILE%\...` makes the default BasicInterpolation raise
    # InterpolationSyntaxError, which would read as "no AWS config" on exactly
    # the hosts that have one.
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(path, encoding="utf-8")
    except Exception:  # noqa: BLE001 — an unparsable file is "no config", not a crash
        pass
    return parser


def _config_section(profile: str) -> dict[str, str]:
    """``~/.aws/config`` names the default profile ``[default]`` but every other
    one ``[profile NAME]`` — a quirk that silently yields an empty section if you
    look up the bare name."""
    parser = _read_ini(_config_path())
    for candidate in (f"profile {profile}", profile):
        if parser.has_section(candidate):
            return dict(parser.items(candidate))
    return {}


def resolve_region() -> str:
    """Resolve the AWS region the way the SDKs do: env first, then the profile.

    Returns "" when no region is configured anywhere — the caller reports that
    as a reason rather than guessing a region and signing for the wrong one.
    """
    for env_var in ("AWS_REGION", "AWS_DEFAULT_REGION"):
        value = os.environ.get(env_var, "").strip()
        if value:
            return value
    return _config_section(active_profile()).get("region", "").strip()


# ---------------------------------------------------------------------------
# Credential resolution
# ---------------------------------------------------------------------------

#: Process-sourced credentials are cached so one page render does not re-run the
#: helper per request. Keyed by profile; invalidated by `is_expired`.
_process_cache: dict[str, Credentials] = {}


def _credentials_from_env() -> Credentials | None:
    access = os.environ.get("AWS_ACCESS_KEY_ID", "").strip()
    secret = os.environ.get("AWS_SECRET_ACCESS_KEY", "").strip()
    if not access or not secret:
        return None
    return Credentials(
        access_key=access,
        secret_key=secret,
        session_token=os.environ.get("AWS_SESSION_TOKEN", "").strip(),
        source="env",
    )


def _credentials_from_file(profile: str) -> Credentials | None:
    parser = _read_ini(_shared_credentials_path())
    if not parser.has_section(profile):
        return None
    section = dict(parser.items(profile))
    access = (section.get("aws_access_key_id") or "").strip()
    secret = (section.get("aws_secret_access_key") or "").strip()
    if not access or not secret:
        return None
    return Credentials(
        access_key=access,
        secret_key=secret,
        session_token=(section.get("aws_session_token") or "").strip(),
        source="shared_credentials_file",
    )


def _expand_windows_vars(command: str) -> str:
    """``~/.aws/config`` written by a Windows helper uses ``%USERPROFILE%``, which
    ``subprocess`` does not expand. ``os.path.expandvars`` handles the ``%VAR%``
    form on Windows and the ``$VAR`` form elsewhere."""
    return os.path.expandvars(command)


def _credentials_from_process(profile: str) -> tuple[Credentials | None, str]:
    """Run the profile's ``credential_process`` and parse its JSON contract.

    Returns (credentials, reason). ``reason`` is non-empty only on failure and is
    safe to show an operator: it names the mechanism that failed, never the
    helper's output (which contains the secret).
    """
    cached = _process_cache.get(profile)
    if cached is not None and not cached.is_expired():
        return cached, ""

    section = _config_section(profile)
    command = (section.get("credential_process") or "").strip()
    if not command:
        return None, ""

    try:
        completed = subprocess.run(  # noqa: S602 — operator-configured AWS mechanism
            _expand_windows_vars(command),
            shell=True,
            capture_output=True,
            timeout=_CREDENTIAL_PROCESS_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None, f"credential_process for profile '{profile}' timed out"
    except Exception as exc:  # noqa: BLE001
        return None, f"credential_process for profile '{profile}' failed: {type(exc).__name__}"

    if completed.returncode != 0:
        return None, (
            f"credential_process for profile '{profile}' exited "
            f"{completed.returncode}"
        )
    try:
        payload = json.loads(completed.stdout.decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001 — stdout is NOT echoed: it holds the secret
        return None, f"credential_process for profile '{profile}' returned non-JSON output"

    access = str(payload.get("AccessKeyId") or "").strip()
    secret = str(payload.get("SecretAccessKey") or "").strip()
    if not access or not secret:
        return None, f"credential_process for profile '{profile}' returned no key pair"

    expires_at = 0.0
    raw_expiry = str(payload.get("Expiration") or "").strip()
    if raw_expiry:
        try:
            expires_at = _dt.datetime.fromisoformat(
                raw_expiry.replace("Z", "+00:00")
            ).timestamp()
        except Exception:  # noqa: BLE001 — an unparsable expiry means "unknown"
            expires_at = 0.0

    creds = Credentials(
        access_key=access,
        secret_key=secret,
        session_token=str(payload.get("SessionToken") or "").strip(),
        source="credential_process",
        expires_at=expires_at,
    )
    _process_cache[profile] = creds
    return creds, ""


def resolve_credentials(*, force_refresh: bool = False) -> tuple[Credentials | None, str]:
    """Resolve credentials via the supported chain.

    ``force_refresh`` skips the env/file steps and re-runs ``credential_process``.
    That is the recovery path for a cached-but-expired session token in
    ``~/.aws/credentials``: the file looks populated, so steps 1–2 succeed and
    signing then fails with ExpiredToken — the caller retries once with this flag.

    Returns (credentials, reason). Both may be empty/None: no credentials AND no
    reason means "this host is simply not AWS-configured", which is not an error.
    """
    profile = active_profile()

    if force_refresh:
        _process_cache.pop(profile, None)
        return _credentials_from_process(profile)

    env_creds = _credentials_from_env()
    if env_creds is not None:
        return env_creds, ""

    file_creds = _credentials_from_file(profile)
    if file_creds is not None:
        return file_creds, ""

    creds, reason = _credentials_from_process(profile)
    if creds is not None:
        return creds, reason

    section = _config_section(profile)
    if section.get("sso_session") or section.get("sso_start_url"):
        return None, (
            f"profile '{profile}' authenticates via AWS SSO, which this signer does "
            f"not implement — run the AWS CLI's `aws sso login` so a cached "
            f"credential lands in ~/.aws/credentials"
        )
    if section.get("role_arn"):
        return None, (
            f"profile '{profile}' assumes a role (role_arn), which this signer does "
            f"not implement"
        )
    return None, reason


def has_credential_process(profile: str | None = None) -> bool:
    return bool(_config_section(profile or active_profile()).get("credential_process"))


# ---------------------------------------------------------------------------
# Signing
# ---------------------------------------------------------------------------


def _sign(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


def _signing_key(secret_key: str, date_stamp: str, region: str, service: str) -> bytes:
    key = _sign(f"AWS4{secret_key}".encode("utf-8"), date_stamp)
    key = _sign(key, region)
    key = _sign(key, service)
    return _sign(key, "aws4_request")


def canonical_query(params: dict[str, str]) -> str:
    """SigV4 requires the query string sorted by key with RFC-3986 encoding, and
    a mismatch here fails as an opaque signature error rather than a bad-request."""
    return "&".join(
        f"{quote(k, safe='-_.~')}={quote(str(v), safe='-_.~')}"
        for k, v in sorted(params.items())
    )


def signed_get_headers(
    *,
    credentials: Credentials,
    host: str,
    region: str,
    service: str,
    canonical_uri: str,
    query: dict[str, str] | None = None,
) -> dict[str, str]:
    """Return the headers that authorise ``GET https://{host}{canonical_uri}?{query}``.

    ``canonical_uri`` must already be the encoded path (e.g. ``/foundation-models``).
    """
    now = _dt.datetime.now(_dt.timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(b"").hexdigest()

    headers: dict[str, str] = {
        "host": host,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
    }
    if credentials.session_token:
        headers["x-amz-security-token"] = credentials.session_token

    signed_headers = ";".join(sorted(headers))
    canonical_headers = "".join(f"{k}:{headers[k]}\n" for k in sorted(headers))
    canonical_request = "\n".join([
        "GET",
        canonical_uri,
        canonical_query(query or {}),
        canonical_headers,
        signed_headers,
        payload_hash,
    ])

    credential_scope = f"{date_stamp}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join([
        _ALGORITHM,
        amz_date,
        credential_scope,
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
    ])
    signature = hmac.new(
        _signing_key(credentials.secret_key, date_stamp, region, service),
        string_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    headers["Authorization"] = (
        f"{_ALGORITHM} Credential={credentials.access_key}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    headers["Accept"] = "application/json"
    return headers
