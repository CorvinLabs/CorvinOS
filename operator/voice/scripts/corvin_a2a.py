#!/usr/bin/env python3
"""Implementation behind the ``corvin-a2a`` CLI wrapper.

L38 management surface — pair instances, send envelopes, inspect configs.
Mirrors the existing ``corvin-erasure`` / ``voice-audit`` CLI patterns.

Exit codes:
  0 — success
  1 — runtime error (transport, signature, mode)
  2 — bad arguments
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from pathlib import Path

# Locate the shared module path when invoked from the repo.
_HERE = Path(__file__).resolve()
_REPO_SHARED = _HERE.parents[2] / "bridges" / "shared"
if _REPO_SHARED.is_dir() and str(_REPO_SHARED) not in sys.path:
    sys.path.insert(0, str(_REPO_SHARED))

import instance_identity  # type: ignore[import-not-found]
import remote_trigger_sender as rts  # type: ignore[import-not-found]
from a2a_attachments import Attachment  # type: ignore[import-not-found]
import a2a_invite as _inv  # type: ignore[import-not-found]
import a2a_invite_registry as _reg  # type: ignore[import-not-found]


# ── Path resolution ───────────────────────────────────────────────────────

_OPERATOR_COWORK = _HERE.parents[2] / "cowork"
_ORIGINS_DIR = _OPERATOR_COWORK / "remote_origins"
_ENDPOINTS_DIR = _OPERATOR_COWORK / "remote_endpoints"


def _origins_dir() -> Path:
    env = os.environ.get("REMOTE_ORIGINS_DIR")
    return Path(env) if env else _ORIGINS_DIR


def _endpoints_dir() -> Path:
    env = os.environ.get("REMOTE_ENDPOINTS_DIR")
    return Path(env) if env else _ENDPOINTS_DIR


def _generate_key() -> str:
    return secrets.token_hex(32)


def _redact(cfg: dict, keys: tuple[str, ...] = ("hmac_key", "recv_key")) -> dict:
    out = dict(cfg)
    for k in keys:
        if k in out and isinstance(out[k], str):
            out[k] = f"<redacted:{len(out[k])}>"
    return out


def _read_endpoint_cfg(path: Path) -> dict:
    """Read an endpoint config file, returning empty dict on any error."""
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception:
        return {}


def _resolve_endpoint_id(name: str, endpoints_dir: Path) -> str:
    """Resolve a user-supplied name (endpoint_id OR label) to an actual endpoint_id.

    Resolution order:
    1. Exact endpoint_id match (filename stem).
    2. Case-insensitive label match.
    3. Case-insensitive endpoint_id prefix match (unique prefix only).

    Falls back to the original name so the registry reports 'not found' with
    full context rather than a cryptic alias-lookup error.
    """
    if not endpoints_dir.exists():
        return name

    # Exact match
    if (endpoints_dir / f"{name}.json").exists():
        return name

    name_lower = name.lower()

    # Label match (sorted for determinism on ties)
    for entry in sorted(endpoints_dir.iterdir()):
        if not entry.is_file() or entry.suffix != ".json":
            continue
        cfg = _read_endpoint_cfg(entry)
        label = cfg.get("label", "")
        if label and label.lower() == name_lower:
            return entry.stem

    # Prefix match on endpoint_id (unique only)
    matches = [
        entry.stem
        for entry in sorted(endpoints_dir.iterdir())
        if entry.is_file()
        and entry.suffix == ".json"
        and entry.stem.lower().startswith(name_lower)
    ]
    if len(matches) == 1:
        return matches[0]

    return name  # fall through — let registry report the error


def _atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, sort_keys=True, indent=2)
        fh.write("\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


# ── A2A peer quota helper ─────────────────────────────────────────────────

def _check_local_a2a_peer_quota() -> str | None:
    """Return an error message if the local a2a_peers_max limit is exceeded.

    Counts existing origin JSON files in _origins_dir().  Tries to read the
    limit from the active license; falls back to the free-tier default (0
    peers allowed) when the validator cannot be imported.  Returns None when
    adding one more peer is within the limit.
    """
    try:
        _lic_dir = _HERE.parents[2] / "license"
        if str(_lic_dir) not in sys.path:
            sys.path.insert(0, str(_lic_dir))
        import validator as _v  # type: ignore[import-not-found]
        if not _v.is_loaded():
            _v.load_license_from_env()
        max_peers = _v.get_limit("a2a_peers_max")
    except Exception:
        # Validator unavailable — fail-open (server enforces authoritatively).
        return None

    if max_peers is None:
        return None  # no constraint

    # ADR-0144: type-guard the limit before comparison. A string-typed limit
    # (e.g. "unlimited") causes `current_count >= max_peers` to raise TypeError;
    # the outer `except Exception: return None` would then silently fail-open.
    # A negative limit would never trigger the guard (0 >= -1 is True) but would
    # always block even the first peer.  Both are handled fail-closed here so that
    # the authoritative server gate (remote_trigger_receiver → assert_limit) is the
    # tiebreaker for malformed tokens, not silent pass-through.
    try:
        max_peers_int = int(max_peers)
        if max_peers_int < 0:
            return (
                f"a2a_peers_max: negative limit {max_peers_int!r} in license token "
                "— blocked (fail-closed, ADR-0144)"
            )
    except (TypeError, ValueError):
        return (
            f"a2a_peers_max: malformed limit {max_peers!r} in license token "
            "— blocked (fail-closed, ADR-0144)"
        )

    origins = _origins_dir()
    if origins.is_dir():
        current_count = sum(
            1 for f in origins.iterdir() if f.is_file() and f.suffix == ".json"
        )
    else:
        current_count = 0

    if current_count >= max_peers_int:
        return (
            f"a2a_peers_max limit reached: {current_count}/{max_peers_int} peers "
            f"registered (tier={_v.active_tier()!r}). "
            "Upgrade your licence or revoke an existing peer first."
        )
    return None


# ── pair ──────────────────────────────────────────────────────────────────

def _cmd_pair(args: argparse.Namespace) -> int:
    """Generate a paired key set for a peer relationship.

    Writes locally:  operator/cowork/remote_origins/<peer>.json
                    (the peer signs *outbound* envelopes to us with hmac_key;
                     we sign *responses* back with recv_key — they verify
                     responses with the same recv_key.)

    Prints to stdout: the corresponding remote_endpoint file the peer
    must install on their side. Operator transports it through a secure
    out-of-band channel (signal/age-encrypted email/etc).
    """
    peer = args.peer_name
    url = args.peer_url
    # The peer endpoint URL is POSTed to as-is by RemoteTriggerSender, which
    # targets the receiver's POST /v1/a2a/receive route. Operators naturally
    # pass the base origin (http://host:port); normalize so a paired endpoint
    # sends out of the box instead of failing with http_404. Idempotent.
    if url and not url.rstrip("/").endswith("/v1/a2a/receive"):
        url = url.rstrip("/") + "/v1/a2a/receive"

    if "/" in peer or "\\" in peer or ":" in peer or peer.startswith("."):
        print(f"error: invalid peer name {peer!r}", file=sys.stderr)
        return 2

    # Generate keys (symmetric setup: each side has one origin file +
    # one endpoint file. The hmac_key authenticates THEIR outbound
    # envelopes to us; the recv_key authenticates OUR responses back.)
    inbound_hmac = _generate_key()
    inbound_recv = _generate_key()

    local_iid = instance_identity.get_instance_id()

    # ── Local-side origin file (what we install locally) ──────────────
    origin_cfg = {
        "origin_id": peer,
        "hmac_key": inbound_hmac,
        "recv_key": inbound_recv,
        "enabled": True,
        "max_ttl_s": args.max_ttl_s,
        "allowed_personas": ["assistant"],
        "spawn_worker": args.spawn_worker,
    }
    origin_path = _origins_dir() / f"{peer}.json"
    if origin_path.exists() and not args.force:
        print(
            f"error: {origin_path} exists; pass --force to overwrite",
            file=sys.stderr,
        )
        return 1

    # Fix ADR-0095-R3: enforce a2a_peers_max locally before writing the origin
    # file.  The server enforces the same limit authoritatively; this is a
    # fast-fail guard for the common offline case.
    if not origin_path.exists():
        err = _check_local_a2a_peer_quota()
        if err:
            print(f"error: {err}", file=sys.stderr)
            return 1

    # ADR-0103 M1: network membership pairing gate.
    # Call corvin-features-production.up.railway.app/v1/pair/authorize to obtain a
    # PairingCertificate. Without a valid certificate, the pairing is
    # rejected (fail-closed). Use --offline-pair to bypass for isolated /
    # air-gapped deployments not participating in the Corvin Labs A2A network.
    pairing_id, pairing_cert = _authorize_pairing_m1(
        local_iid,
        url,
        offline=getattr(args, "offline_pair", False),
    )
    if pairing_id is None and not getattr(args, "offline_pair", False):
        # Gate rejected the pairing — error message already printed.
        return 1
    if pairing_id is None:
        # Offline mode: use a local UUID so the field is present.
        import uuid as _uuid
        pairing_id = str(_uuid.uuid4())

    # Embed pairing_id (and cert when available) in the origin config.
    origin_cfg["pairing_id"] = pairing_id
    if pairing_cert:
        origin_cfg["pairing_cert"] = pairing_cert

    # ADR-0103 M4: set require_network_attestation based on pairing mode.
    # Server-verified pairs (M1 gate passed) mandate attestation by default —
    # only licensed senders with a valid SesT can connect. Offline-paired
    # origins explicitly opt-out; the HMAC is still the primary gate but
    # forged-SesT protection is absent. See ADR-0140 for threat model.
    if getattr(args, "offline_pair", False):
        origin_cfg["require_network_attestation"] = False
        print(
            "WARNING (ADR-0103): --offline-pair disables network membership "
            "attestation for this origin. Only use for air-gapped networks that "
            "do not participate in the Corvin Labs A2A network. Receivers "
            "cannot distinguish a legitimate sender from a fork without "
            "network_attestation.",
            file=sys.stderr,
        )
    else:
        origin_cfg["require_network_attestation"] = True

    _atomic_write(origin_path, origin_cfg)

    # ── Peer-side endpoint file (what the peer must install) ─────────
    # The peer's RemoteTriggerSender will sign envelopes with inbound_hmac
    # (their HMAC key), POST to our URL, and verify our response with
    # inbound_recv. We tell them to pin our instance_id, so a swapped
    # receiver behind the same URL gets caught.
    peer_endpoint = {
        "endpoint_id": args.local_endpoint_id or "corvin-local",
        "url": url,
        "hmac_key": inbound_hmac,
        "recv_key": inbound_recv,
        "instance_id": local_iid,
        "enabled": True,
        "default_ttl_s": args.max_ttl_s,
        "our_origin_id": peer,
        # ADR-0103 M2: peer must include this pairing_id in network_attestation
        # so the receiver can verify the M1-gated pairing was mutually authorised.
        "pairing_id": pairing_id,
    }

    # ADR-0095 M2: optional server-side peer registration.
    # Adds a server-signed PeerPermit to the origin file for enforced
    # a2a_peers_max quota. Silently skipped if not activated or unreachable.
    _register_peer_server_side(peer, url, origin_path, origin_cfg)

    print(f"# OK: wrote local origin file: {origin_path}")
    print()
    print("# Peer-side endpoint file — copy to the peer instance at:")
    print(f"#   operator/cowork/remote_endpoints/{peer_endpoint['endpoint_id']}.json")
    print("#")
    print("# Use a secure channel (signed email, age-encrypted file, etc.).")
    print("# Set mode 0600 after writing.")
    print()
    print(json.dumps(peer_endpoint, sort_keys=True, indent=2))
    return 0


_A2A_FEATURES_SERVER_PROD = "https://corvin-features-production.up.railway.app"


def _a2a_features_url() -> str:
    """Return Corvin-Features URL for A2A peer registration (ADR-0098)."""
    if os.environ.get("CORVIN_TEST_MODE") == "1":
        override = os.environ.get("CORVIN_FEATURES_URL", "").rstrip("/")
        return override or _A2A_FEATURES_SERVER_PROD
    return _A2A_FEATURES_SERVER_PROD


# ── ADR-0103 M1 — Network membership pairing gate ────────────────────────

def _compute_sest_fp() -> str | None:
    """Compute the SesT fingerprint SHA-256(header + '.' + payload).

    Returns None when no SesT is available (free/unlicensed instance).
    """
    import hashlib as _hl

    token = os.environ.get("CORVIN_LICENSE_KEY", "").strip()
    if not token:
        # Session key written by the refresh daemon (mirrors validator._find_token order)
        try:
            # VOICE-F8: honor XDG_CONFIG_HOME — the writer (session_refresh.py)
            # resolves this dir via XDG, so a hardcoded ~/.config here is a
            # reader≠writer split whenever XDG_CONFIG_HOME is set.
            _xdg = os.environ.get("XDG_CONFIG_HOME") or "~/.config"
            session_key = Path(os.path.expanduser(_xdg)) / "corvin-voice" / "session.key"
            if session_key.exists():
                t = session_key.read_text("utf-8").strip()
                if t:
                    token = t
        except Exception:
            pass
    if not token:
        try:
            corvin_home = Path(
                os.environ.get("CORVIN_HOME", "") or (Path.home() / ".corvin")
            )
            key_file = corvin_home / "global" / "license.key"
            if key_file.exists():
                token = key_file.read_text("utf-8").strip()
        except Exception:
            pass
    if not token:
        return None

    parts = token.split(".")
    if len(parts) != 3:
        return None

    header_payload = parts[0] + "." + parts[1]
    return _hl.sha256(header_payload.encode("ascii")).hexdigest()


def _authorize_pairing_m1(
    local_iid: str,
    peer_url: str,
    *,
    offline: bool = False,
) -> tuple[str | None, str | None]:
    """ADR-0103 M1: call the pairing authorization gate.

    Returns ``(pairing_id, pairing_cert_jwt)`` on success.
    Returns ``(None, None)`` when ``offline=True`` (caller uses a local UUID).
    Exits the current process with an error message when the gate rejects.

    The gate call sends:
      - local instance_id
      - local SesT fingerprint (proves this is a licensed instance)
      - peer_url (so the server can log the topology)
    to ``corvin-features-production.up.railway.app/v1/pair/authorize``.
    """
    import hashlib as _hl
    import json as _j
    import time as _t
    import urllib.error
    import urllib.request

    if offline:
        print(
            "# WARNING: --offline-pair used. Pairing certificate NOT verified "
            "by Corvin Labs. This instance will not be accepted by M2 receivers "
            "after the attestation grace period expires.",
            file=sys.stderr,
        )
        return None, None

    sest_fp = _compute_sest_fp()
    if not sest_fp:
        print(
            "error: no Session Token (SesT / license key) found. "
            "A valid Corvin Labs license is required to pair with the A2A network. "
            "Use --offline-pair for isolated networks not participating in the "
            "Corvin Labs A2A network.",
            file=sys.stderr,
        )
        return None, None

    features_url = _a2a_features_url()
    body = _j.dumps({
        "instance_id": local_iid,
        "sest_fp": sest_fp,
        "peer_url": peer_url,
    }).encode()

    req = urllib.request.Request(
        f"{features_url}/v1/pair/authorize",
        data=body,
        method="POST",
    )
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "corvin-a2a/1.0")
    req.add_header("X-Corvin-Instance-Id", local_iid)

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = _j.loads(resp.read().decode("utf-8"))
            pairing_id = str(result.get("pairing_id", ""))
            pairing_cert = str(result.get("pairing_cert", ""))
            if not pairing_id:
                print(
                    "error: pairing gate returned no pairing_id — "
                    "server response malformed.",
                    file=sys.stderr,
                )
                return None, None
            print(
                f"# A2A network pairing authorized "
                f"(pairing_id={pairing_id[:8]}…)",
                file=sys.stderr,
            )
            return pairing_id, pairing_cert or None
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace")[:300]
        print(
            f"error: pairing gate rejected (HTTP {exc.code}): {body_text}\n"
            "Use --offline-pair for isolated / air-gapped deployments.",
            file=sys.stderr,
        )
        return None, None
    except urllib.error.URLError as exc:
        print(
            f"error: pairing gate unreachable ({exc.reason}). "
            "Check your network connection or use --offline-pair for "
            "isolated networks.",
            file=sys.stderr,
        )
        return None, None
    except Exception as exc:
        print(
            f"error: pairing gate call failed ({type(exc).__name__}: {exc}). "
            "Use --offline-pair to bypass.",
            file=sys.stderr,
        )
        return None, None


def _a2a_device_fp() -> str:
    """Compute ADR-0098 device fingerprint from hardware (not from disk)."""
    import hashlib as _hl
    import socket as _s
    import uuid as _u
    try:
        _core_lic = Path(__file__).resolve().parents[3] / "core" / "license"
        import sys as _sys
        if str(_core_lic) not in _sys.path:
            _sys.path.insert(0, str(_core_lic))
        from corvin_license.trial import machine_fingerprint as _mfp  # type: ignore
        machine_fp = _mfp()
    except Exception:
        hostname = "unknown"
        try:
            hostname = _s.gethostname()
        except Exception:
            pass
        machine_fp = _hl.sha256(f"{hostname}:{format(_u.getnode(), '012x')}".encode()).hexdigest()[:32]
    return _hl.sha256(machine_fp.encode()).hexdigest()[:32]


def _register_peer_server_side(
    peer_id: str,
    peer_url: str,
    origin_path: "Path",
    origin_cfg: dict,
) -> None:
    """Register peer with Corvin-Features server and embed the PeerPermit.

    Best-effort: any error is logged and suppressed so pair always succeeds.
    ADR-0098: URL hardcoded in production; X-Corvin-Device-Fp sent on every request.
    """
    import hashlib as _hl
    import hmac as _hm
    import json as _j
    import time as _t
    import urllib.error
    import urllib.request

    features_url = _a2a_features_url()

    try:
        xdg = os.environ.get("XDG_CONFIG_HOME") or "~/.config"
        feat_path = Path(os.path.expanduser(xdg)) / "corvin-voice" / "features.json"
        if not feat_path.exists():
            return
        feat = _j.loads(feat_path.read_text(encoding="utf-8"))
        api_key = feat.get("api_key", "")
        license_token = os.environ.get("CORVIN_LICENSE_KEY", "").strip()
        if not license_token:
            corvin_home = Path(
                os.environ.get("CORVIN_HOME", "") or (Path.home() / ".corvin")
            )
            key_file = corvin_home / "global" / "license.key"
            if key_file.exists():
                license_token = key_file.read_text().strip()
        if not api_key or not license_token:
            return

        body = _j.dumps({
            "peer_id": peer_id,
            "peer_url": peer_url,
        }).encode()
        ts = str(int(_t.time()))
        sig = _hm.new(api_key.encode(), body + b"." + ts.encode(), _hl.sha256).hexdigest()

        req = urllib.request.Request(
            f"{features_url}/v1/a2a/peers",
            data=body,
            method="POST",
        )
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", f"Bearer {license_token}")
        req.add_header("X-Corvin-Ts", ts)
        req.add_header("X-Corvin-Sig", sig)
        req.add_header("X-Corvin-Device-Fp", _a2a_device_fp())

        with urllib.request.urlopen(req, timeout=8) as resp:
            result = _j.loads(resp.read().decode())
            permit = result.get("permit", "")
            if permit:
                updated = dict(origin_cfg, server_permit=permit)
                _atomic_write(origin_path, updated)
                print(f"# server-permit embedded (exp={result.get('exp')})", file=sys.stderr)
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode()[:200]
        print(f"# warning: server peer registration failed HTTP {exc.code}: {body_text}", file=sys.stderr)
    except Exception as exc:
        print(f"# warning: server peer registration skipped: {exc}", file=sys.stderr)


# ── send ──────────────────────────────────────────────────────────────────

def _cmd_send(args: argparse.Namespace) -> int:
    instruction = args.instruction
    if instruction == "-":
        instruction = sys.stdin.read()

    result_schema: dict = {}
    if args.schema:
        schema_path = Path(args.schema)
        try:
            result_schema = json.loads(schema_path.read_text("utf-8"))
        except Exception as exc:
            print(f"error: schema load failed: {exc}", file=sys.stderr)
            return 2

    # ── Build outbound attachments from --attach FILE flags ──────────
    attachments: list[Attachment] = []
    for raw in (args.attach or []):
        path = Path(raw)
        if not path.is_file():
            print(f"error: attachment not found: {path}", file=sys.stderr)
            return 2
        try:
            attachments.append(Attachment.from_file(path))
        except Exception as exc:
            print(f"error: failed to read {path}: {exc}", file=sys.stderr)
            return 2

    endpoint_id = _resolve_endpoint_id(args.endpoint_id, _endpoints_dir())
    sender = rts.RemoteTriggerSender(endpoints_dir=_endpoints_dir())
    res = sender.send(
        endpoint_id,
        instruction=instruction,
        result_schema=result_schema or None,
        ttl_s=args.ttl,
        timeout_s=args.timeout,
        attachments=attachments or None,
    )

    # ── Persist returned attachments if --attach-out-dir given ──────
    written_attachments: list[str] = []
    if args.attach_out_dir and res.attachments:
        out_dir = Path(args.attach_out_dir).expanduser()
        out_dir.mkdir(parents=True, exist_ok=True)
        for att_dict in res.attachments:
            att = Attachment.from_dict(att_dict)
            try:
                raw_bytes = att.decode()
            except Exception as exc:
                print(f"warn: skipping bad attachment {att.name}: {exc}",
                      file=sys.stderr)
                continue
            dest = out_dir / att.name
            dest.write_bytes(raw_bytes)
            written_attachments.append(str(dest))

    output = {
        "ok": res.ok,
        "status": res.status,
        "task_id": res.task_id,
        "instance_id": res.instance_id,
        "instance_id_match": res.instance_id_match,
        "duration_ms": res.duration_ms,
        "data": res.data,
        "attachments": [
            {"name": a["name"], "mime": a["mime"],
             "sha256": a["sha256"],
             "bytes": (len(a.get("content_b64", "")) * 3) // 4}
            for a in res.attachments
        ],
    }
    if written_attachments:
        output["attachments_written"] = written_attachments
    print(json.dumps(output, sort_keys=True, indent=2))
    return 0 if res.ok else 1


# ── list / show ───────────────────────────────────────────────────────────

def _list_dir(d: Path) -> list[str]:
    if not d.exists():
        return []
    out = []
    for entry in sorted(d.iterdir()):
        if entry.is_file() and entry.suffix == ".json":
            out.append(entry.stem)
    return out


def _cmd_list_origins(_: argparse.Namespace) -> int:
    ids = _list_dir(_origins_dir())
    for i in ids:
        print(i)
    return 0


def _cmd_list_endpoints(_: argparse.Namespace) -> int:
    ids = _list_dir(_endpoints_dir())
    for i in ids:
        print(i)
    return 0


def _cmd_show_origin(args: argparse.Namespace) -> int:
    path = _origins_dir() / f"{args.id}.json"
    if not path.exists():
        print(f"error: not found: {path}", file=sys.stderr)
        return 1
    cfg = json.loads(path.read_text("utf-8"))
    print(json.dumps(_redact(cfg), sort_keys=True, indent=2))
    return 0


def _cmd_show_endpoint(args: argparse.Namespace) -> int:
    path = _endpoints_dir() / f"{args.id}.json"
    if not path.exists():
        print(f"error: not found: {path}", file=sys.stderr)
        return 1
    cfg = json.loads(path.read_text("utf-8"))
    print(json.dumps(_redact(cfg), sort_keys=True, indent=2))
    return 0


# ── migrate-attestation ──────────────────────────────────────────────────


def _cmd_migrate_attestation(args: argparse.Namespace) -> int:
    """P1-C (security review 2026-06-18): set require_network_attestation on
    existing origin files that pre-date ADR-0103 M4 enforcement (commit ff4a469).

    Origins created by ``corvin-a2a pair`` before that commit do not have the
    field. Without it, the receiver falls back to the pre-M4 grace period —
    a fork without a SesT can still send if the pairing HMAC is valid.

    This command adds ``require_network_attestation: true`` to every origin file
    that is missing the field (server-verified pairs). Origins with
    ``--offline-pair`` (field already ``false``) are left unchanged.
    """
    origins_dir = _origins_dir()
    if not origins_dir.is_dir():
        print("no origins directory found — nothing to migrate", file=sys.stderr)
        return 0

    dry_run = getattr(args, "dry_run", False)
    updated, skipped, already_set = [], [], []

    for path in sorted(origins_dir.glob("*.json")):
        try:
            cfg = json.loads(path.read_text("utf-8"))
        except Exception as exc:  # noqa: BLE001
            print(f"  skip {path.stem}: unreadable ({exc})", file=sys.stderr)
            skipped.append(path.stem)
            continue

        if "require_network_attestation" in cfg:
            already_set.append((path.stem, cfg["require_network_attestation"]))
            continue

        # Field absent → this is a pre-M4 origin; add enforcement.
        cfg["require_network_attestation"] = True
        if not dry_run:
            _atomic_write(path, cfg)
        updated.append(path.stem)

    print(f"migration report (dry_run={dry_run}):")
    for oid in updated:
        tag = "[DRY]" if dry_run else "[SET]"
        print(f"  {tag} {oid}: require_network_attestation = true")
    for oid, val in already_set:
        print(f"  [OK]  {oid}: require_network_attestation already = {val!r}")
    for oid in skipped:
        print(f"  [ERR] {oid}: could not process")

    if not dry_run and updated:
        print(f"\n{len(updated)} origin(s) updated. Restart the adapter to pick up changes.")
    elif dry_run and updated:
        print(f"\n{len(updated)} origin(s) would be updated. Run without --dry-run to apply.")
    else:
        print("\nnothing to update.")
    return 0


# ── label-endpoint ────────────────────────────────────────────────────────

def _cmd_label_endpoint(args: argparse.Namespace) -> int:
    """Set a human-readable label on an existing endpoint file."""
    ep_id = _resolve_endpoint_id(args.endpoint_id, _endpoints_dir())
    path = _endpoints_dir() / f"{ep_id}.json"
    if not path.exists():
        print(f"error: endpoint not found: {args.endpoint_id!r}", file=sys.stderr)
        return 1
    label = args.label
    if len(label) > 64:
        print("error: label too long (max 64 chars)", file=sys.stderr)
        return 2
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in label):
        print("error: label contains control characters", file=sys.stderr)
        return 2
    cfg = json.loads(path.read_text("utf-8"))
    cfg["label"] = label
    _atomic_write(path, cfg)
    print(json.dumps(_redact(cfg), sort_keys=True, indent=2))
    return 0


# ── agents ────────────────────────────────────────────────────────────────

def _cmd_agents(args: argparse.Namespace) -> int:
    """List all known agents: local instance identity + all remote endpoints."""
    result: dict = {}

    # Local agent
    try:
        meta = instance_identity.instance_id_metadata()
        result["_local"] = {
            "type": "local",
            "instance_id": meta.get("instance_id", ""),
            "label": meta.get("label", ""),
            "created_at": meta.get("created_at", ""),
        }
    except Exception as exc:  # noqa: BLE001
        result["_local"] = {"type": "local", "error": str(exc)}

    # Remote endpoints
    ep_dir = _endpoints_dir()
    if ep_dir.exists():
        for entry in sorted(ep_dir.iterdir()):
            if not entry.is_file() or entry.suffix != ".json":
                continue
            cfg = _read_endpoint_cfg(entry)
            result[entry.stem] = {
                "type": "remote",
                "endpoint_id": cfg.get("endpoint_id", entry.stem),
                "label": cfg.get("label", ""),
                "url": cfg.get("url", ""),
                "enabled": cfg.get("enabled", True),
                "instance_id": cfg.get("instance_id", ""),
            }

    if getattr(args, "json", False):
        print(json.dumps(result, sort_keys=True, indent=2))
        return 0

    # Human-readable output
    local = result.pop("_local", {})
    lbl = local.get("label") or "(no label set)"
    iid = local.get("instance_id", "?")
    print(f"Local:   label={lbl!r}  id={iid[:8]}…")
    print()
    if not result:
        print("Remote agents: (none — use `corvin-a2a pair` to add one)")
    else:
        print(f"Remote agents ({len(result)}):")
        for eid, info in result.items():
            lbl = info.get("label") or ""
            url = info.get("url", "")
            disabled = "  [disabled]" if not info.get("enabled", True) else ""
            lbl_str = f"  label={lbl!r}" if lbl else ""
            print(f"  {eid}{lbl_str}  {url}{disabled}")
    return 0


# ── invite ────────────────────────────────────────────────────────────────

def _cmd_invite(args: argparse.Namespace) -> int:
    """Generate a self-contained A2A invite token (ADR-0063)."""
    iid = instance_identity.get_instance_id()
    scope = [p.strip() for p in args.scope.split(",") if p.strip()]

    ttl: float | None = None
    if args.ttl:
        raw = args.ttl.strip().lower()
        if raw.endswith("h"):
            ttl = float(raw[:-1]) * 3600
        elif raw.endswith("d"):
            ttl = float(raw[:-1]) * 86400
        elif raw.endswith("m"):
            ttl = float(raw[:-1]) * 60
        else:
            ttl = float(raw)

    url = args.url or ""
    if not url:
        print("error: --url is required (e.g. https://host:8000)", file=sys.stderr)
        return 2

    try:
        token, token_str = _inv.generate_invite(
            iid=iid,
            origin_id=args.origin_id or iid[:16].replace("-", ""),
            url=url,
            receive_path=args.receive_path,
            allowed_personas=scope,
            max_ttl_s=args.max_call_ttl,
            ttl_seconds=ttl,
            single_use=args.single_use,
            label=args.label or None,
            spawn_worker=args.spawn_worker,
        )
    except _inv.InviteError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    # Save to registry
    registry = _reg.InviteRegistry()
    import a2a_invite_registry as _reg2  # noqa: F811
    entry = _reg2.InviteEntry(
        ikey=token.ikey,
        oid=token.oid,
        lbl=token.lbl or "",
        iat=token.iat,
        exp=token.exp,
        su=token.su,
    )
    registry.create(entry)

    if getattr(args, "json", False):
        import datetime
        print(json.dumps({
            "token": token_str,
            "ikey": token.ikey,
            "oid": token.oid,
            "exp": token.exp,
            "su": token.su,
        }, indent=2))
        return 0

    exp_str = ""
    if token.exp:
        import datetime
        exp_str = f" · gültig bis {datetime.datetime.fromtimestamp(token.exp).strftime('%Y-%m-%d %H:%M')}"

    print(f"# Invite-Token ({('single-use, ' if token.su else '')}{'kein Ablauf' if not token.exp else f'läuft ab{exp_str}'}):")
    print()
    print(token_str)
    print()
    print("# Gegenstelle führt aus:")
    print(f"#   corvin-a2a accept <token>")

    if args.qr:
        try:
            import qrcode  # type: ignore[import-not-found]
            import io
            qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M)
            qr.add_data(token_str)
            qr.make(fit=True)
            f = io.StringIO()
            qr.print_ascii(out=f)
            print(f.getvalue())
            # Save PNG
            import os as _os
            out_path = _os.path.join("outputs", "corvin-invite-qr.png")
            _os.makedirs("outputs", exist_ok=True)
            img = qr.make_image()
            img.save(out_path)
            print(f"# QR-Code gespeichert: {out_path}")
        except ImportError:
            print("# (qrcode-Paket nicht installiert — ASCII-QR nicht verfügbar)", file=sys.stderr)

    return 0


# ── accept ────────────────────────────────────────────────────────────────

def _cmd_accept(args: argparse.Namespace) -> int:
    """Accept an A2A invite token and install origin + endpoint files."""
    token_str = args.token.strip()
    try:
        result = _inv.parse_invite(token_str)
    except _inv.InviteError as exc:
        print(f"error: ungültiger Token: {exc}", file=sys.stderr)
        return 1

    # parse_invite returns a 3-tuple
    token, payload_bytes, sig_bytes = result  # type: ignore[misc]

    # Validate expiry (+ registry if we are the issuer)
    local_iid = instance_identity.get_instance_id()
    registry: _reg.InviteRegistry | None = None
    if token.iid == local_iid:
        registry = _reg.InviteRegistry()
        if not _inv.verify_invite_sig(payload_bytes, sig_bytes):
            print("error: Token-Signatur ungültig (HMAC-Mismatch)", file=sys.stderr)
            return 1

    validation = _inv.validate_invite(token, registry=registry)
    if not validation.ok:
        print(f"error: Token abgelehnt: {validation.reason}", file=sys.stderr)
        return 1

    # Conflict check
    origin_path = _origins_dir() / f"{token.oid}.json"
    endpoint_path = _endpoints_dir() / f"{token.oid}.json"
    if (origin_path.exists() or endpoint_path.exists()) and not args.overwrite:
        print(
            f"warn: {token.oid} existiert bereits. Nutze --overwrite zum Überschreiben.",
            file=sys.stderr,
        )
        if not args.overwrite:
            return 1

    if args.dry_run:
        print(f"[dry-run] Würde origin schreiben:   {origin_path}")
        print(f"[dry-run] Würde endpoint schreiben: {endpoint_path}")
        print(f"[dry-run] oid={token.oid}  url={token.url}  personas={token.pa}")
        return 0

    # Write files
    origin_cfg = _inv.invite_to_origin_dict(token)
    endpoint_cfg = _inv.invite_to_endpoint_dict(token, local_instance_id=local_iid)
    _atomic_write(origin_path, origin_cfg)
    _atomic_write(endpoint_path, endpoint_cfg)

    # Mark accepted in registry (if we are the issuer)
    if registry is not None:
        registry.mark_accepted(token.ikey)

    print(f"[OK] Verbindung zu {token.oid!r} hergestellt.")
    print(f"     URL:              {token.url}")
    print(f"     Personas:         {', '.join(token.pa)}")
    print(f"     spawn_worker:     {token.spawn_worker}")
    if token.exp:
        import datetime
        print(f"     Läuft ab:         {datetime.datetime.fromtimestamp(token.exp).strftime('%Y-%m-%d %H:%M')}")
    print(f"     Origin-Datei:     {origin_path}")
    print(f"     Endpoint-Datei:   {endpoint_path}")
    print()
    print(f"     Test: corvin-a2a send {token.oid} \"ping\"")

    if args.respond:
        # Generate a return invite so the issuer can accept our side.
        our_url = args.respond_url or ""
        if not our_url:
            print("\nwarn: --respond-url fehlt — kein Rück-Token generiert.", file=sys.stderr)
            return 0
        try:
            _ret_token, ret_str = _inv.generate_invite(
                iid=local_iid,
                origin_id=args.respond_origin_id or local_iid[:16].replace("-", ""),
                url=our_url,
                ttl_seconds=args.respond_ttl,
                allowed_personas=token.pa,
                single_use=True,
                label=f"Rück-Token für {token.oid}",
            )
        except _inv.InviteError as exc:
            print(f"warn: Rück-Token-Generierung fehlgeschlagen: {exc}", file=sys.stderr)
            return 0
        _reg.InviteRegistry().create(_reg.InviteEntry(
            ikey=_ret_token.ikey,
            oid=_ret_token.oid,
            lbl=_ret_token.lbl or "",
            iat=_ret_token.iat,
            exp=_ret_token.exp,
            su=_ret_token.su,
        ))
        print()
        print("# Rück-Token für Gegenstelle (einmal einlösen):")
        print()
        print(ret_str)

    return 0


# ── list-invites ──────────────────────────────────────────────────────────

def _cmd_list_invites(args: argparse.Namespace) -> int:
    """List all issued invite tokens and their status."""
    registry = _reg.InviteRegistry()
    if args.clean:
        removed = registry.cleanup()
        if removed:
            print(f"Bereinigt: {removed} abgelaufene Einträge entfernt.")
    entries = registry.list_all()
    if not entries:
        print("(keine Invites)")
        return 0
    if getattr(args, "json", False):
        print(json.dumps([e.to_dict() for e in entries], indent=2))
        return 0
    import datetime
    for e in entries:
        exp_str = datetime.datetime.fromtimestamp(e.exp).strftime("%Y-%m-%d %H:%M") if e.exp else "kein Ablauf"
        su_str = " [single-use]" if e.su else ""
        lbl_str = f" [{e.lbl}]" if e.lbl else ""
        print(f"{e.ikey}  {e.oid:<30s}  {e.status:<10s}  {exp_str}{su_str}{lbl_str}")
    return 0


# ── revoke-invite ─────────────────────────────────────────────────────────

def _cmd_revoke_invite(args: argparse.Namespace) -> int:
    """Revoke an unaccepted invite by ikey or label."""
    registry = _reg.InviteRegistry()
    ikey = args.ikey_or_label
    # Try as label first if it doesn't look like a hex prefix
    if len(ikey) != 16 or not all(c in "0123456789abcdef" for c in ikey.lower()):
        entry = registry.find_by_label(ikey)
        if entry is None:
            print(f"error: kein Invite mit Label {ikey!r} gefunden", file=sys.stderr)
            return 1
        ikey = entry.ikey
    ok = registry.revoke(ikey)
    if not ok:
        print(f"error: Invite {ikey!r} nicht gefunden", file=sys.stderr)
        return 1
    print(f"[OK] Invite {ikey} widerrufen.")
    return 0


# ── ADR-0070: Friendship Token commands ──────────────────────────────────

import a2a_friendship as _friendship  # type: ignore[import-not-found]


def _cmd_create_token(args: argparse.Namespace) -> int:
    """Generate a friendship token.  Writes nothing to disk."""
    url = args.url.strip().rstrip("/") if args.url else None
    label = args.label.strip() if args.label else None
    ttl: float | None = None
    if args.ttl:
        raw = args.ttl.strip().lower()
        if raw.endswith("d"):
            ttl = float(raw[:-1]) * 86400
        elif raw.endswith("h"):
            ttl = float(raw[:-1]) * 3600
        elif raw == "never" or raw == "0":
            ttl = None
        else:
            try:
                ttl = float(raw)
            except ValueError:
                print(f"error: ungültiges TTL-Format: {args.ttl!r} (z.B. 7d, 24h, never)", file=sys.stderr)
                return 2

    personas: list[str] | None = None
    if args.scope:
        personas = [p.strip() for p in args.scope.split(",") if p.strip()]

    max_ttl: int | None = args.max_call_ttl if args.max_call_ttl > 0 else None

    try:
        token, token_str = _friendship.create_friendship_token(
            url=url,
            label=label,
            ttl_seconds=ttl,
            personas=personas,
            max_ttl_s=max_ttl,
        )
    except _friendship.FriendshipError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.remember_url and url:
        _friendship.set_my_url(url)
        print(f"[info] Eigene URL gespeichert: {url}", file=sys.stderr)

    if getattr(args, "json", False):
        print(json.dumps({
            "token": token_str,
            "kid": token.kid,
            "expires": token.expires,
            "url": token.url,
        }, indent=2))
        return 0

    print()
    print("WICHTIG: Dieser Token enthält ein kryptografisches Geheimnis.")
    print("         Teile ihn wie ein Passwort — nur über verschlüsselte Kanäle!")
    print()
    print(token_str)
    print()
    print(f"# kid: {token.kid}")
    if token.url:
        print(f"# URL: {token.url}")
    else:
        print("# URL: (nicht gesetzt — wird nach dem Import mit 'set-url' ergänzt)")
    if token.expires:
        import datetime
        print(f"# Gültig bis: {datetime.datetime.fromtimestamp(token.expires).strftime('%Y-%m-%d %H:%M')}")
    print()
    print("# Gegenstelle führt aus:")
    print("#   corvin-a2a import-token <token>")
    if not token.url:
        print("#   corvin-a2a set-url <kid> <url-der-gegenstelle>")

    if args.qr:
        try:
            import qrcode as _qr  # type: ignore[import-not-found]
            import io
            qr = _qr.QRCode(error_correction=_qr.constants.ERROR_CORRECT_M)
            qr.add_data(token_str)
            qr.make(fit=True)
            f = io.StringIO()
            qr.print_ascii(out=f)
            print(f.getvalue())
            import os as _os
            out_path = _os.path.join("outputs", "friendship-qr.png")
            _os.makedirs("outputs", exist_ok=True)
            qr.make_image().save(out_path)
            print(f"# QR-Code gespeichert: {out_path}")
        except ImportError:
            print("# (qrcode-Paket nicht installiert — QR nicht verfügbar)", file=sys.stderr)

    return 0


def _cmd_import_token(args: argparse.Namespace) -> int:
    """Import a friendship token: write origin + endpoint files."""
    token_str = args.token.strip()
    try:
        token = _friendship.parse_and_verify(token_str)
    except _friendship.FriendshipError as exc:
        print(f"error: ungültiger Token: {exc}", file=sys.stderr)
        return 1

    # Override URL from --url flag
    peer_url = args.url.strip().rstrip("/") if args.url else None
    if peer_url:
        from dataclasses import replace
        token = replace(token, url=peer_url)

    origin_path = _origins_dir() / f"{token.kid}.json"
    endpoint_path = _endpoints_dir() / f"{token.kid}.json"
    if (origin_path.exists() or endpoint_path.exists()) and not args.overwrite:
        print(
            f"warn: Verbindung {token.kid!r} existiert bereits. --overwrite verwenden.",
            file=sys.stderr,
        )
        return 1

    if args.dry_run:
        state = "ACTIVE" if token.url else "PENDING"
        print(f"[dry-run] kid={token.kid}  state={state}  url={token.url or '(leer)'}  label={token.label or '—'}")
        print(f"[dry-run] Würde schreiben: {origin_path}")
        print(f"[dry-run] Würde schreiben: {endpoint_path}")
        return 0

    _atomic_write(origin_path, _friendship.to_origin_dict(token))
    _atomic_write(endpoint_path, _friendship.to_endpoint_dict(token))

    state = "ACTIVE" if token.url else "PENDING"
    print(f"[OK] Verbindung importiert (kid={token.kid}, state={state})")
    if token.label:
        print(f"     Label:   {token.label}")
    if token.url:
        print(f"     URL:     {token.url}")
    else:
        print(f"     URL:     (noch nicht gesetzt)")
        print(f"     → URL ergänzen: corvin-a2a set-url {token.kid} <peer-url>")
    print(f"     Origin:  {origin_path}")
    print(f"     Endpoint:{endpoint_path}")
    return 0


def _cmd_set_url(args: argparse.Namespace) -> int:
    """Upgrade a PENDING connection to ACTIVE by providing the peer URL."""
    kid = args.kid
    peer_url = args.peer_url.strip().rstrip("/")
    try:
        _friendship.activate_connection(
            kid, peer_url,
            origins_dir=_origins_dir(),
            endpoints_dir=_endpoints_dir(),
        )
    except _friendship.FriendshipError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"[OK] Verbindung {kid!r} aktiviert (ACTIVE).")
    print(f"     URL: {peer_url}")
    print(f"     Test: corvin-a2a send {kid} \"ping\"")
    return 0


def _cmd_my_url(args: argparse.Namespace) -> int:
    """Show or set this instance's own A2A base URL."""
    if args.url:
        new_url = args.url.strip().rstrip("/")
        _friendship.set_my_url(new_url)
        print(f"[OK] Eigene URL gesetzt: {new_url}")
        return 0
    current = _friendship.get_my_url()
    if current:
        print(current)
    else:
        print("(keine eigene URL konfiguriert)")
        print("Setzen: corvin-a2a my-url <url>")
        print("   oder CORVIN_A2A_URL=<url> setzen")
    return 0


def _cmd_revoke_token(args: argparse.Namespace) -> int:
    """Delete a friendship connection (origin + endpoint files)."""
    kid = args.kid
    if "/" in kid or "\\" in kid or kid.startswith("."):
        print(f"error: ungültige kid: {kid!r}", file=sys.stderr)
        return 2
    origin_path = _origins_dir() / f"{kid}.json"
    endpoint_path = _endpoints_dir() / f"{kid}.json"

    found = False
    for p in (origin_path, endpoint_path):
        if p.exists():
            try:
                cfg = json.loads(p.read_text("utf-8"))
                if cfg.get("_friendship"):
                    found = True
            except Exception:
                pass

    if not found:
        print(f"error: Friendship-Verbindung {kid!r} nicht gefunden", file=sys.stderr)
        return 1

    origin_path.unlink(missing_ok=True)
    endpoint_path.unlink(missing_ok=True)
    print(f"[OK] Verbindung {kid!r} gelöscht.")
    return 0


# ── Argparse ──────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="corvin-a2a",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_pair = sub.add_parser("pair", help="generate paired keys for a peer")
    p_pair.add_argument("peer_name", help="peer identifier (lowercase, no '/' '\\' ':' '.')")
    p_pair.add_argument("peer_url", help="peer URL, e.g. http://host:8000/v1/a2a/receive")
    p_pair.add_argument("--max-ttl-s", type=int, default=300)
    p_pair.add_argument("--spawn-worker", action="store_true",
                        help="opt this peer into M2 worker spawn")
    p_pair.add_argument("--local-endpoint-id", default="",
                        help="endpoint_id the peer should use for us")
    p_pair.add_argument("--force", action="store_true",
                        help="overwrite existing origin file")
    p_pair.add_argument("--offline-pair", action="store_true", dest="offline_pair",
                        help="bypass the Corvin Labs pairing gate "
                             "(for isolated / air-gapped networks only; "
                             "issued pairs will not be accepted by M2 receivers "
                             "after the attestation grace period)")

    p_send = sub.add_parser("send", help="send a TaskEnvelope to an endpoint")
    p_send.add_argument("endpoint_id")
    p_send.add_argument("instruction", help="instruction text, or '-' for stdin")
    p_send.add_argument("--ttl", type=int, default=60)
    p_send.add_argument("--timeout", type=int, default=30)
    p_send.add_argument("--schema", default=None,
                        help="path to a result_schema JSON file")
    p_send.add_argument("--attach", action="append", default=[],
                        metavar="FILE",
                        help="attach a file (repeatable; ≤16 files, ≤1 MiB total)")
    p_send.add_argument("--attach-out-dir", default=None,
                        metavar="DIR",
                        help="write returned attachments into DIR")

    sub.add_parser("list-origins", help="list inbound origins")
    sub.add_parser("list-endpoints", help="list outbound endpoints")

    p_so = sub.add_parser("show-origin", help="show one origin config (keys redacted)")
    p_so.add_argument("id")

    p_se = sub.add_parser("show-endpoint", help="show one endpoint config (keys redacted)")
    p_se.add_argument("id")

    p_lep = sub.add_parser(
        "label-endpoint",
        help="set a human-readable label on an endpoint (use in /a2a and --json output)",
    )
    p_lep.add_argument("endpoint_id", help="endpoint_id (filename stem) or existing label")
    p_lep.add_argument("label", help="friendly name (≤ 64 chars, no control chars)")

    p_ag = sub.add_parser("agents", help="list all known agents (local + remote)")
    p_ag.add_argument("--json", action="store_true", dest="json",
                      help="output as machine-readable JSON")

    # ── ADR-0063: invite / accept / list-invites / revoke-invite ──────
    p_inv = sub.add_parser("invite", help="generate a self-contained A2A invite token (ADR-0063)")
    p_inv.add_argument("--url", required=True,
                       help="this instance's base URL (e.g. https://host:8000)")
    p_inv.add_argument("--origin-id", default="",
                       help="origin_id the peer should register (default: iid prefix)")
    p_inv.add_argument("--scope", default="assistant",
                       help="comma-separated allowed personas (default: assistant)")
    p_inv.add_argument("--ttl", default="7d",
                       help="token validity: e.g. 1h, 7d, 30d (default: 7d)")
    p_inv.add_argument("--single-use", action="store_true",
                       help="token may be accepted exactly once")
    p_inv.add_argument("--spawn-worker", action="store_true",
                       help="allow the peer to spawn worker tasks")
    p_inv.add_argument("--label", default="",
                       help="human-readable label stored in registry")
    p_inv.add_argument("--max-call-ttl", type=int, default=300,
                       help="max TaskEnvelope TTL in seconds (default: 300)")
    p_inv.add_argument("--receive-path", default="/v1/a2a/receive",
                       help="A2A receive path (default: /v1/a2a/receive)")
    p_inv.add_argument("--qr", action="store_true",
                       help="render QR code in terminal + save PNG to ./outputs/")
    p_inv.add_argument("--json", action="store_true", dest="json",
                       help="machine-readable JSON output")

    p_acc = sub.add_parser("accept", help="accept an A2A invite token")
    p_acc.add_argument("token", help="invite token string")
    p_acc.add_argument("--overwrite", action="store_true",
                       help="overwrite existing origin/endpoint files")
    p_acc.add_argument("--dry-run", action="store_true",
                       help="validate and print what would be written; do not write files")
    p_acc.add_argument("--respond", action="store_true",
                       help="generate and print a return invite for the issuer")
    p_acc.add_argument("--respond-url", default="",
                       help="our A2A URL for the return invite (required with --respond)")
    p_acc.add_argument("--respond-origin-id", default="",
                       help="origin_id for the return invite")
    p_acc.add_argument("--respond-ttl", type=float, default=7 * 86400,
                       help="TTL for the return invite in seconds (default: 7d)")

    p_li = sub.add_parser("list-invites", help="list issued invite tokens and their status")
    p_li.add_argument("--clean", action="store_true",
                      help="prune entries expired more than 1 day ago")
    p_li.add_argument("--json", action="store_true", dest="json",
                      help="machine-readable JSON output")

    p_ri = sub.add_parser("revoke-invite", help="revoke an unaccepted invite by ikey or label")
    p_ri.add_argument("ikey_or_label",
                      help="16-hex-char ikey prefix or human label")

    # ── ADR-0070: Friendship Token ─────────────────────────────────────
    p_ct = sub.add_parser("create-token",
                          help="generate a friendship token (ADR-0070) — all args optional")
    p_ct.add_argument("--url", default="",
                      help="own A2A base URL (e.g. https://host:8000) — optional")
    p_ct.add_argument("--label", default="",
                      help="human-readable label (optional)")
    p_ct.add_argument("--ttl", default="30d",
                      help="token validity: 7d, 24h, never (default: 30d)")
    p_ct.add_argument("--scope", default="",
                      help="comma-separated allowed personas (empty = assistant)")
    p_ct.add_argument("--max-call-ttl", type=int, default=0, dest="max_call_ttl",
                      help="max TaskEnvelope TTL in seconds (0 = no cap)")
    p_ct.add_argument("--remember-url", action="store_true", dest="remember_url",
                      help="also save --url as this instance's my-url")
    p_ct.add_argument("--qr", action="store_true",
                      help="render QR code in terminal")
    p_ct.add_argument("--json", action="store_true", dest="json",
                      help="machine-readable JSON output")

    p_it = sub.add_parser("import-token",
                          help="import a friendship token from a peer (ADR-0070)")
    p_it.add_argument("token", help="friendship token string (corvin-a2a:ft1:…)")
    p_it.add_argument("--url", default="",
                      help="peer's A2A base URL (overrides URL in token)")
    p_it.add_argument("--overwrite", action="store_true",
                      help="overwrite existing connection files")
    p_it.add_argument("--dry-run", action="store_true",
                      help="validate and show what would be written; do not write files")

    p_su = sub.add_parser("set-url",
                          help="upgrade a PENDING friendship connection to ACTIVE")
    p_su.add_argument("kid", help="connection kid (UUID)")
    p_su.add_argument("peer_url", help="peer's A2A base URL")

    p_mu = sub.add_parser("my-url",
                          help="show or set this instance's own A2A base URL")
    p_mu.add_argument("url", nargs="?", default="",
                      help="new URL to store (omit to display current)")

    p_rvt = sub.add_parser("revoke-token",
                           help="delete a friendship connection (ADR-0070)")
    p_rvt.add_argument("kid", help="connection kid to delete")

    p_ma = sub.add_parser(
        "migrate-attestation",
        help="add require_network_attestation to pre-M4 origin files (P1-C fix)",
    )
    p_ma.add_argument(
        "--dry-run", action="store_true", dest="dry_run",
        help="show what would change without writing files",
    )

    args = parser.parse_args(argv)

    handlers = {
        "pair": _cmd_pair,
        "send": _cmd_send,
        "list-origins": _cmd_list_origins,
        "list-endpoints": _cmd_list_endpoints,
        "show-origin": _cmd_show_origin,
        "show-endpoint": _cmd_show_endpoint,
        "label-endpoint": _cmd_label_endpoint,
        "agents": _cmd_agents,
        "invite": _cmd_invite,
        "accept": _cmd_accept,
        "list-invites": _cmd_list_invites,
        "revoke-invite": _cmd_revoke_invite,
        # ADR-0070
        "create-token": _cmd_create_token,
        "import-token": _cmd_import_token,
        "set-url": _cmd_set_url,
        "my-url": _cmd_my_url,
        "revoke-token": _cmd_revoke_token,
        # P1-C security-review fix
        "migrate-attestation": _cmd_migrate_attestation,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
