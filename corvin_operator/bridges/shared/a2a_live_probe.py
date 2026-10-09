"""Probe a RUNNING A2A host over its real HTTP boundary (stdlib only).

One implementation, two callers: the live-host E2E test (``tests/e2e/a2a/test_live_host_a2a_contract_e2e.py``)
asserts on it, and the continuous watch (``a2a_live_watch``) runs it on a timer. A check that only one of
them exercised would be a dead mechanism for the other.

The point: unit and in-process tests prove the CODE; they cannot prove that the PROCESS an operator runs
has loaded it. A service keeps the code it started with — the console bundle can be new while the A2A
receiver behind it is not.

Contract probed (ADR-2242 §2/§8), all through ``POST /v1/a2a/ping`` and ``GET /v1/console/a2a/feed``:
  * ``ordinary_ping``       an old-style signed ping is answered with a verifiable pong
  * ``pong_capacity``       the pong says ``task_capacity``: ``available`` | ``limit_reached``
  * ``task_status_unknown`` a signed task-status query for an unknown task answers ``{"stage": "unknown"}``
  * ``reaimed_ignored``     a task signature made for another task is ignored (plain pong)
  * ``forged_refused``      a ping signed with the wrong key gets the opaque 403 and no hint
  * ``feed_shape``          the console feed carries ``stages`` and every peer carries ``task_capacity``

Safety: it never touches a real pairing. It drops a THROWAWAY origin (random id and keys, mode 0600) into
the origins directory the host reads and removes it again in a ``finally`` — also when everything else
fails. Keys are never part of a result. Nothing here raises: every failure is a failed :class:`Check`.
"""
from __future__ import annotations

import contextlib
import hashlib
import hmac
import http.cookiejar
import json
import os
import secrets
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

MAX_BODY = 2 * 1024 * 1024          # a host answering with more than this is not a console
DEFAULT_TIMEOUT_S = 15.0
CAPACITY_VALUES = ("available", "limit_reached")


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str = ""
    severity: str = "critical"      # "critical" = the HOST is wrong; "warn" = something to look at
    ms: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "detail": self.detail[:400],
                "severity": self.severity, "ms": self.ms}


# ── transport: never raises ───────────────────────────────────────────────

def _request(method: str, url: str, body: dict | None = None, *, timeout: float,
             opener: urllib.request.OpenerDirector | None = None) -> tuple[int, Any, str]:
    """(status, parsed JSON or None, error). status 0 = no HTTP answer at all."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"} if data else {})
    open_ = opener.open if opener is not None else urllib.request.urlopen
    try:
        with open_(req, timeout=timeout) as r:
            status, raw = r.status, r.read(MAX_BODY + 1)
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read(MAX_BODY + 1)
    except Exception as e:  # noqa: BLE001 — refused / DNS / timeout / TLS / reset
        return 0, None, f"{type(e).__name__}"
    if len(raw) > MAX_BODY:
        return status, None, "response_too_large"
    try:
        return status, json.loads(raw or b"{}"), ""
    except ValueError:
        return status, None, "not_json"


def _canon(d: dict) -> bytes:
    return json.dumps(d, separators=(",", ":"), sort_keys=True).encode()


def _sign(key_hex: str, d: dict) -> str:
    return hmac.new(bytes.fromhex(key_hex), _canon(d), hashlib.sha256).hexdigest()


# ── the throwaway pairing ─────────────────────────────────────────────────

def default_origins_dir() -> Path:
    """The directory a host on this machine reads origins from (same resolver the host uses)."""
    env = os.environ.get("REMOTE_ORIGINS_DIR") or os.environ.get("CORVIN_LIVE_ORIGINS_DIR")
    if env:
        return Path(env)
    from remote_trigger_receiver import OriginRegistry  # noqa: PLC0415 — only needed here
    return Path(OriginRegistry(None)._dir)


@contextlib.contextmanager
def throwaway_origin(origins_dir: Path) -> Iterator[dict]:
    """A paired-looking origin with random id and keys; always removed again."""
    origins_dir.mkdir(parents=True, exist_ok=True)
    oid = f"live-probe-{uuid.uuid4().hex[:12]}"
    cfg = {"origin_id": oid, "hmac_key": secrets.token_hex(32), "recv_key": secrets.token_hex(32),
           "enabled": True, "max_ttl_s": 60, "allowed_personas": ["assistant"], "spawn_worker": False}
    path = origins_dir / f"{oid}.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(cfg, fh)
        yield cfg
    finally:
        with contextlib.suppress(OSError):
            path.unlink()


def _ping_body(o: dict, *, task_id: str | None = None, task_sig_for: str | None = None) -> dict:
    base = {"ping_id": uuid.uuid4().hex, "issued_at": int(time.time()), "origin_id": o["origin_id"]}
    req = dict(base, signature=_sign(o["hmac_key"], base))
    if task_id is not None:
        req["task_id"] = task_id
        req["task_sig"] = _sign(o["hmac_key"], dict(base, task_id=task_sig_for or task_id))
    return req


def _verified_pong(o: dict, status: int, resp: Any, err: str) -> tuple[dict | None, str]:
    """(pong, "") when the answer is a 200 whose signature verifies with the pairing's recv key."""
    if status == 0:
        return None, f"no answer from the host ({err})"
    if status != 200:
        return None, f"HTTP {status}" + (f" ({err})" if err else "")
    if not isinstance(resp, dict):
        return None, f"answer is not a JSON object ({err or 'shape'})"
    body = dict(resp)
    sig = body.pop("signature", "")
    try:
        if not isinstance(sig, str) or not hmac.compare_digest(sig, _sign(o["recv_key"], body)):
            return None, "pong signature does not verify"
    except (ValueError, TypeError):
        return None, "pong signature is malformed"
    return body, ""


# ── the checks ────────────────────────────────────────────────────────────

def _timed(name: str, fn: Callable[[], tuple[bool, str]], severity: str = "critical") -> Check:
    t = time.time()
    try:
        ok, detail = fn()
    except Exception as e:  # noqa: BLE001 — a probe must never take the watcher down
        ok, detail = False, f"probe error: {type(e).__name__}"
    return Check(name, bool(ok), detail, severity, int((time.time() - t) * 1000))


def probe_host(base_url: str, *, origins_dir: Path | None = None,
               timeout_s: float = DEFAULT_TIMEOUT_S) -> tuple[list[Check], dict | None]:
    """Run every check against ``base_url``. Returns (checks, feed) — the feed (or None) is handed
    back so a caller can analyse peers without a second request. Never raises."""
    base = base_url.rstrip("/")
    checks: list[Check] = []
    feed: dict | None = None
    try:
        odir = origins_dir or default_origins_dir()
        origin_cm = throwaway_origin(odir)
        o = origin_cm.__enter__()
    except Exception as e:  # noqa: BLE001 — unwritable dir, missing registry, ...
        checks.append(Check("probe_origin", False,
                            f"cannot create the throwaway origin ({type(e).__name__}) — the ping contract was NOT probed"))
        o = None
        origin_cm = None
    try:
        if o is not None:
            def ping(**kw):
                return _request("POST", f"{base}/v1/a2a/ping", _ping_body(o, **kw), timeout=timeout_s)

            def c_ordinary():
                pong, why = _verified_pong(o, *ping())
                if pong is None:
                    return False, why
                return bool(pong.get("ok")) and bool(pong.get("instance_id")), "signed pong verified"

            def c_capacity():
                pong, why = _verified_pong(o, *ping())
                if pong is None:
                    return False, why
                cap = pong.get("task_capacity")
                if cap in CAPACITY_VALUES:
                    return True, f"task_capacity={cap}"
                return False, ("the pong carries no task_capacity — the host is running code from before "
                               "the quota fast-fail / capacity change (restart it)")

            def c_unknown():
                pong, why = _verified_pong(o, *ping(task_id=f"no-such-task-{uuid.uuid4().hex[:8]}"))
                if pong is None:
                    return False, why
                ok = pong.get("task_stage") == {"stage": "unknown"}
                return ok, "unknown task answered unknown" if ok else f"task_stage={str(pong.get('task_stage'))[:80]}"

            def c_reaimed():
                pong, why = _verified_pong(o, *ping(task_id="task-a", task_sig_for="task-b"))
                if pong is None:
                    return False, why
                ok = "task_stage" not in pong
                return ok, "re-aimed signature ignored" if ok else "a re-aimed task signature was honoured"

            def c_forged():
                bad = dict(o, hmac_key=secrets.token_hex(32))
                status, resp, err = _request("POST", f"{base}/v1/a2a/ping", _ping_body(bad), timeout=timeout_s)
                if status == 0:
                    return False, f"no answer from the host ({err})"
                ok = status == 403 and resp == {"reason": "ping_rejected"}
                return ok, "forged ping refused without a hint" if ok else f"forged ping got HTTP {status}"

            for name, fn in (("ordinary_ping", c_ordinary), ("pong_capacity", c_capacity),
                             ("task_status_unknown", c_unknown), ("reaimed_ignored", c_reaimed),
                             ("forged_refused", c_forged)):
                checks.append(_timed(name, fn))
    finally:
        if origin_cm is not None:
            with contextlib.suppress(Exception):
                origin_cm.__exit__(None, None, None)

    def c_feed():
        nonlocal feed
        jar = http.cookiejar.CookieJar()
        op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        st, _r, err = _request("GET", f"{base}/v1/console/auth/local-login", timeout=timeout_s, opener=op)
        if st == 0 or st >= 500:
            return False, f"console login failed ({'HTTP %d' % st if st else err})"
        st, data, err = _request("GET", f"{base}/v1/console/a2a/feed?limit=1000&include_former=true",
                                 timeout=timeout_s, opener=op)
        if st != 200 or not isinstance(data, dict):
            return False, f"feed unavailable (HTTP {st}{', ' + err if err else ''})"
        feed = data
        if not isinstance(data.get("stages"), dict):
            return False, "the feed has no `stages` map — the console host predates the message-lifecycle change"
        peers = data.get("peers")
        if not isinstance(peers, list):
            return False, "the feed has no peers list"
        for p in peers:
            if not isinstance(p, dict) or "task_capacity" not in p:
                return False, f"peer {str((p or {}).get('peer_id'))[:12]} has no task_capacity key"
            if p["task_capacity"] not in (None, *CAPACITY_VALUES):
                return False, f"peer {str(p.get('peer_id'))[:12]} reports an unknown capacity"
        return True, f"{len(peers)} peer(s), stages ok"

    checks.append(_timed("feed_shape", c_feed))
    return checks, feed
