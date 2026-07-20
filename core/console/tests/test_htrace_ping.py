"""Tests for the anonymous instance-count ping (ADR-0180 §3) —
adversarial review findings: TOCTOU race in ping_if_due, and the
one-shot-instead-of-recurring ping at corvin-serve startup.

Verifies:
  - ping_if_due() locks the check-then-send-then-stamp sequence, so a
    concurrent caller (lock already held) never sends a duplicate ping.
  - ping_loop() re-invokes ping_if_due() repeatedly (not just once).
  - start_ping_thread() is idempotent — only ever starts one thread per
    process, even if called multiple times.
"""
from __future__ import annotations

import json
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from corvin_console.aco import htrace_uploader as hu


@pytest.fixture(autouse=True)
def _reset_ping_thread_state():
    """start_ping_thread's idempotency guard is module-global — reset it
    around every test so tests don't leak state into each other."""
    orig = hu._ping_thread_started
    hu._ping_thread_started = False
    yield
    hu._ping_thread_started = orig


def _make_home(tmp_path: Path) -> Path:
    home = tmp_path / ".corvin"
    (home / "aco" / "telemetry").mkdir(parents=True, exist_ok=True)
    return home


def test_ping_if_due_skips_network_call_when_lock_already_held(tmp_path):
    """Simulates the exact race: another process (or thread) already holds
    the ping lock when this call arrives — it must return True (not an
    error) and must NOT send a second ping for the same instance-day."""
    if not hu._HAS_FLOCK:
        pytest.skip("flock not available on this platform")
    import fcntl as _fcntl

    home = _make_home(tmp_path)
    with (
        patch.object(hu, "ping_enabled", return_value=True),
        patch.object(hu, "ensure_ping_tokens", return_value=True),
    ):
        lock_path = hu.htrace_dir(home) / hu._PING_LOCK_FILENAME
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        holder = lock_path.open("w")
        _fcntl.flock(holder, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
        try:
            with patch("urllib.request.urlopen") as mock_urlopen:
                result = hu.ping_if_due(home)
        finally:
            _fcntl.flock(holder, _fcntl.LOCK_UN)
            holder.close()

    assert result is True
    mock_urlopen.assert_not_called()


def test_ping_if_due_sends_when_lock_is_free(tmp_path):
    """Sanity counterpart: with no contention, a due ping actually sends."""
    home = _make_home(tmp_path)
    mock_resp = type("R", (), {"getcode": lambda self: 200})()
    mock_ctx = type("Ctx", (), {
        "__enter__": lambda self: mock_resp,
        "__exit__": lambda self, *a: False,
    })()

    with (
        patch.object(hu, "ping_enabled", return_value=True),
        patch.object(hu, "ensure_ping_tokens", return_value=True),
        patch.object(hu, "_last_ping_path", return_value=tmp_path / "last_ping"),
        patch.object(hu, "_load_telemetry_token", return_value="tok"),
        patch.object(hu, "_load_instance_token", return_value="itok"),
        patch.object(hu, "load_or_create_instance_id", return_value="iid"),
        patch.object(hu, "_detect_active_engine", return_value="claude_code"),
        # The ping send routes through the hardened no-redirect/https-only opener
        # (F8), not a bare urlopen — patch that so the test exercises the real
        # transport path.
        patch.object(hu, "_open_no_redirect", return_value=mock_ctx) as mock_urlopen,
    ):
        result = hu.ping_if_due(home)

    assert result is True
    mock_urlopen.assert_called_once()


def test_ping_body_carries_only_allowlisted_enum_fields(tmp_path):
    """CLAUDE.md invariant (since 2026-07-10): the anonymous instance-count ping
    carries "uuid4 + version + coarse allowlisted environment enums". The uuid4
    (instance_id) and HMAC (instance_token) travel in HEADERS; the JSON body
    carries exactly corvin_version + platform + python_minor + active_engine,
    every value from a closed enum / pattern — never free-form strings."""
    home = _make_home(tmp_path)
    captured: dict = {}

    mock_resp = type("R", (), {"getcode": lambda self: 200})()
    mock_ctx = type("Ctx", (), {
        "__enter__": lambda self: mock_resp,
        "__exit__": lambda self, *a: False,
    })()

    def _capture(req, *a, **k):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        captured["url"] = req.full_url
        return mock_ctx

    with (
        patch.object(hu, "ping_enabled", return_value=True),
        patch.object(hu, "ensure_ping_tokens", return_value=True),
        patch.object(hu, "_last_ping_path", return_value=tmp_path / "last_ping"),
        patch.object(hu, "_load_telemetry_token", return_value="tok"),
        patch.object(hu, "_load_instance_token", return_value="itok-hmac"),
        patch.object(hu, "load_or_create_instance_id", return_value="uuid4-iid"),
        patch.object(hu, "_detect_active_engine", return_value="claude_code"),
        patch.object(hu, "_open_no_redirect", side_effect=_capture) as mock_urlopen,
    ):
        result = hu.ping_if_due(home)

    assert result is True
    mock_urlopen.assert_called_once()
    # The actual REQUEST targets the Cloudflare-fronted ping URL (test-audit F3:
    # the sibling test pins the _PING_URL_DEFAULT constant, but nothing asserted
    # the POST used it — a mutation sending to the Railway origin passed).
    assert captured["url"] == hu._PING_URL_DEFAULT
    # Body: exactly the four allowlisted keys, values from closed enums.
    assert set(captured["body"].keys()) == {
        "corvin_version", "platform", "python_minor", "active_engine",
    }
    assert captured["body"]["platform"] in hu._PING_ALLOWED_PLATFORMS
    assert hu._RE_PING_PY_MINOR.match(captured["body"]["python_minor"])
    assert captured["body"]["active_engine"] == "claude_code"
    # uuid4 + HMAC are in headers, never the body.
    assert "uuid4-iid" not in json.dumps(captured["body"])
    assert "itok-hmac" not in json.dumps(captured["body"])
    assert captured["headers"].get("x-htrace-instance-id") == "uuid4-iid"
    assert captured["headers"].get("x-httrace-instance-token") == "itok-hmac"


def test_assert_ping_safe_is_fail_closed():
    """_assert_ping_safe accepts the allowlisted enum body and rejects any
    extra key AND any value outside its closed enum/pattern (fail-closed
    backstop mirroring telemetry._assert_safe)."""
    hu._assert_ping_safe({"corvin_version": "0.10.17"})  # must not raise
    hu._assert_ping_safe({
        "corvin_version": "0.10.17",
        "platform": "linux",
        "python_minor": "3.12",
        "active_engine": "claude_code",
    })  # full allowlisted body must not raise
    # Unknown keys stay rejected.
    for extra in ({"hostname": "x"}, {"user": "y"}, {"ip": "1.2.3.4"}):
        body = {"corvin_version": "0.10.17", **extra}
        with pytest.raises(ValueError, match="non-allowlisted"):
            hu._assert_ping_safe(body)
    # Allowlisted keys with out-of-enum / free-form values stay rejected.
    for bad in (
        {"platform": "amiga"},
        {"platform": "linux; rm -rf"},
        {"python_minor": "3.12.4"},
        {"active_engine": "custom-engine"},
        {"corvin_version": "x" * 33},
        {"active_engine": 7},
    ):
        body = {"corvin_version": "0.10.17", **bad}
        with pytest.raises(ValueError, match="non-allowlisted"):
            hu._assert_ping_safe(body)


def test_unquoted_yaml_zero_opts_out(tmp_path):
    """Adversarial-review finding: an operator hand-editing the tenant config to
    ``ping_enabled: 0`` (unquoted → parsed as int 0, not the string "0") must
    opt out, as the docstring promises. Previously only ``false``/``no``/``off``/
    ``'0'`` (string) disabled it, silently ignoring the int form."""
    from corvin_console.aco import htrace_consent as hc

    cfg = tmp_path / "tenant.corvin.yaml"
    cfg.write_text("spec:\n  telemetry:\n    ping_enabled: 0\n", encoding="utf-8")
    assert hc._read_telemetry_flag(cfg, "ping_enabled") is False
    cfg.write_text("spec:\n  telemetry:\n    healing_traces: 0\n", encoding="utf-8")
    assert hc._read_telemetry_flag(cfg, "healing_traces") is False
    assert hc._healing_flag_on({"telemetry": {"healing_traces": 0}}) is False
    # A truthy int (e.g. 1) still counts as opted-in.
    cfg.write_text("spec:\n  telemetry:\n    ping_enabled: 1\n", encoding="utf-8")
    assert hc._read_telemetry_flag(cfg, "ping_enabled") is True


def test_ping_send_uses_no_redirect_opener(tmp_path):
    """The ping POST carries a Bearer + instance token in headers; it must go
    through the hardened no-redirect/https-only opener (F8), never a bare
    urlopen that would forward those credentials across a 302 or over http://."""
    home = _make_home(tmp_path)
    mock_resp = type("R", (), {"getcode": lambda self: 200})()
    mock_ctx = type("Ctx", (), {
        "__enter__": lambda self: mock_resp,
        "__exit__": lambda self, *a: False,
    })()
    with (
        patch.object(hu, "ping_enabled", return_value=True),
        patch.object(hu, "ensure_ping_tokens", return_value=True),
        patch.object(hu, "_last_ping_path", return_value=tmp_path / "last_ping"),
        patch.object(hu, "_load_telemetry_token", return_value="tok"),
        patch.object(hu, "_load_instance_token", return_value="itok"),
        patch.object(hu, "load_or_create_instance_id", return_value="iid"),
        patch.object(hu, "_detect_active_engine", return_value="claude_code"),
        patch.object(hu, "_open_no_redirect", return_value=mock_ctx) as mock_open,
        patch("urllib.request.urlopen") as mock_urlopen,
    ):
        assert hu.ping_if_due(home) is True
    mock_open.assert_called_once()
    mock_urlopen.assert_not_called()


def test_ping_loop_reinvokes_ping_if_due_repeatedly():
    """The recurring loop must call ping_if_due() more than once — locks in
    the fix for the "sent once at boot, never again" undercounting bug."""
    calls = []

    def _fake_ping_if_due(home):
        calls.append(home)
        if len(calls) >= 3:
            raise SystemExit  # break out of the infinite loop for the test
        return True

    with (
        patch.object(hu, "ping_if_due", _fake_ping_if_due),
        patch.object(hu.time, "sleep", lambda _s: None),  # no real waiting
    ):
        with pytest.raises(SystemExit):
            hu.ping_loop(Path("/fake/home"))

    assert len(calls) == 3


def test_ping_if_due_still_sends_after_a_backward_clock_jump(tmp_path):
    """A negative `age` (backward clock jump — NTP correction, VM/container
    clock skew on boot) must NOT be treated as "already sent today". Before
    the fix, `age < _PING_INTERVAL_S` was true for ANY negative age, so a
    clock that jumps backward on every boot suppressed the ping forever."""
    home = _make_home(tmp_path)
    stamp = tmp_path / "last_ping"
    stamp.write_text("x", encoding="utf-8")
    # Backdate the stamp's mtime into the FUTURE relative to "now" so
    # time.time() - mtime is negative, simulating a backward clock jump.
    future = hu.time.time() + 10_000
    import os as _os
    _os.utime(stamp, (future, future))

    mock_resp = type("R", (), {"getcode": lambda self: 200})()
    mock_ctx = type("Ctx", (), {
        "__enter__": lambda self: mock_resp,
        "__exit__": lambda self, *a: False,
    })()

    with (
        patch.object(hu, "ping_enabled", return_value=True),
        patch.object(hu, "ensure_ping_tokens", return_value=True),
        patch.object(hu, "_last_ping_path", return_value=stamp),
        patch.object(hu, "_load_telemetry_token", return_value="tok"),
        patch.object(hu, "_load_instance_token", return_value="itok"),
        patch.object(hu, "load_or_create_instance_id", return_value="iid"),
        patch.object(hu, "_detect_active_engine", return_value="claude_code"),
        patch.object(hu, "_open_no_redirect", return_value=mock_ctx) as mock_urlopen,
    ):
        result = hu.ping_if_due(home)

    assert result is True
    mock_urlopen.assert_called_once(), (
        "a backward clock jump must not suppress the ping indefinitely"
    )


def test_ping_if_due_provisions_tokens_inside_the_lock_not_before(tmp_path):
    """ensure_ping_tokens() must run AFTER the flock is acquired — calling it
    before the lock (the pre-fix ordering) let two racing processes both
    provision tokens concurrently, risking a mismatched instance/telemetry
    token pair. Verified by asserting ensure_ping_tokens is only invoked
    while the lock file is actually held."""
    if not hu._HAS_FLOCK:
        pytest.skip("flock not available on this platform")
    import fcntl as _fcntl

    home = _make_home(tmp_path)
    observed_locked_during_provision = []

    def _fake_ensure_tokens(_home):
        lock_path = hu.htrace_dir(home) / hu._PING_LOCK_FILENAME
        probe = lock_path.open("w")
        try:
            _fcntl.flock(probe, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
            observed_locked_during_provision.append(False)  # we got it — not locked
            _fcntl.flock(probe, _fcntl.LOCK_UN)
        except (OSError, BlockingIOError):
            observed_locked_during_provision.append(True)  # someone else holds it
        finally:
            probe.close()
        return False  # stop here — no need to actually send a ping

    with (
        patch.object(hu, "ping_enabled", return_value=True),
        patch.object(hu, "ensure_ping_tokens", side_effect=_fake_ensure_tokens),
    ):
        hu.ping_if_due(home)

    assert observed_locked_during_provision == [True], (
        "ensure_ping_tokens() must run while ping_if_due's own lock is held"
    )


class TestPingTargetsCloudflareProxy:
    """ADR-0204: the ping POST must route through the Cloudflare-fronted
    corvin-labs.com Pages Function, not hit the Railway origin directly —
    Railway has no Cloudflare zone in front of it, so a direct hit never
    carries CF-IPCountry and the backend's country capture never fires."""

    def test_ping_default_url_is_cloudflare_fronted(self):
        assert hu._PING_URL_DEFAULT == "https://corvin-labs.com/api/telemetry/ping"

    def test_healing_trace_upload_url_is_unaffected(self):
        """Only the ping moved behind Cloudflare — the healing-trace bundle
        upload still targets the Railway origin directly (unchanged)."""
        assert hu._UPLOAD_URL_DEFAULT == (
            "https://corvin-features-production.up.railway.app/v1/telemetry/healing-traces"
        )

    def test_ping_base_url_overridable_via_env(self, monkeypatch):
        """CORVIN_TELEMETRY_PING_BASE_URL lets local dev/CI point the ping
        straight at Railway (or any other host) without touching the code."""
        import importlib

        monkeypatch.setenv(
            "CORVIN_TELEMETRY_PING_BASE_URL", "https://example-dev.test/"
        )
        try:
            reloaded = importlib.reload(hu)
            assert reloaded._PING_URL_DEFAULT == "https://example-dev.test/api/telemetry/ping"
        finally:
            monkeypatch.delenv("CORVIN_TELEMETRY_PING_BASE_URL", raising=False)
            importlib.reload(hu)


def test_start_ping_thread_is_idempotent():
    """Calling start_ping_thread() twice must only ever start ONE thread —
    matches the pattern already used by start_heartbeat_thread()."""
    started_threads = []
    orig_thread = threading.Thread

    def _tracking_thread(*a, **k):
        t = orig_thread(*a, **k)
        started_threads.append(t)
        return t

    with (
        patch.object(hu, "ping_loop", lambda home: None),
        patch("threading.Thread", side_effect=_tracking_thread),
    ):
        hu.start_ping_thread(Path("/fake/home"))
        hu.start_ping_thread(Path("/fake/home"))

    assert len(started_threads) == 1


class TestTelemetryUserAgent:
    """Cloudflare's bot protection blocks generic Python UAs (Python-urllib/3.x
    → 403 at the edge, before the Pages Function runs). Every telemetry request
    that routes through corvin-labs.com MUST therefore carry the explicit
    CorvinOS-* User-Agent — a missing UA is a silently-dead channel, not a
    cosmetic issue (adversarial review 2026-07-20: the heartbeat shipped
    without one and 100% of presence beats died with 403)."""

    @staticmethod
    def _capture_request(captured):
        mock_resp = type("R", (), {"getcode": lambda self: 200})()

        def _open(req, timeout):
            captured.append(req)
            return type("Ctx", (), {
                "__enter__": lambda self: mock_resp,
                "__exit__": lambda self, *a: False,
            })()

        return _open

    def test_heartbeat_request_carries_corvinos_user_agent(self, tmp_path):
        from corvin_console.aco import heartbeat as hb

        captured: list = []
        with (
            patch.object(hb, "_load_telemetry_token", return_value="tok"),
            patch.object(hb, "_load_instance_token", return_value="itok"),
            patch.object(hb, "load_or_create_instance_id", return_value="iid"),
            patch.object(hb, "_open_no_redirect", side_effect=self._capture_request(captured)),
        ):
            assert hb.send_heartbeat(_make_home(tmp_path)) is True

        assert len(captured) == 1
        ua = captured[0].get_header("User-agent")
        assert ua is not None and ua.startswith("CorvinOS-"), (
            f"heartbeat User-Agent is {ua!r} — a generic/absent UA gets 403'd "
            "by Cloudflare bot protection and kills the presence channel"
        )

    def test_ping_request_carries_corvinos_user_agent(self, tmp_path):
        home = _make_home(tmp_path)
        captured: list = []
        with (
            patch.object(hu, "ping_enabled", return_value=True),
            patch.object(hu, "ensure_ping_tokens", return_value=True),
            patch.object(hu, "_last_ping_path", return_value=tmp_path / "last_ping"),
            patch.object(hu, "_load_telemetry_token", return_value="tok"),
            patch.object(hu, "_load_instance_token", return_value="itok"),
            patch.object(hu, "load_or_create_instance_id", return_value="iid"),
            patch.object(hu, "_detect_active_engine", return_value="claude_code"),
            patch.object(hu, "_open_no_redirect", side_effect=self._capture_request(captured)),
        ):
            assert hu.ping_if_due(home) is True

        assert len(captured) == 1
        ua = captured[0].get_header("User-agent")
        assert ua is not None and ua.startswith("CorvinOS-")

    def test_heartbeat_url_is_cloudflare_fronted(self):
        """ADR-0204: like the ping, the heartbeat must route through the
        corvin-labs.com Pages Function (CF-IPCountry for online geo), and it
        must follow _PING_BASE so the env override redirects both channels."""
        from corvin_console.aco import heartbeat as hb

        assert hb._HEARTBEAT_URL == "https://corvin-labs.com/api/telemetry/heartbeat"
        assert hb._HEARTBEAT_URL.startswith(hu._PING_BASE)


class TestPing401Reprovision:
    """T6 (review finding, pre-existing) + T6-RESIDUAL (2026-07-20 refutation):
    locally persisted tokens can become permanently invalid when the server
    rotates/deletes them — ensure_ping_tokens only provisions when the FILES are
    missing, so ping + heartbeat returned 401 forever. Recovery drops the token
    pair, re-provisions, and retries EXACTLY once.

    The RESIDUAL hardens the *trigger*: the destructive delete must NOT fire on a
    single transient 401 (WAF blip, auth-backend hiccup, a rate-limiter answering
    401 instead of 429) — a fleet-wide simultaneous wipe would drive a
    self-reinforcing hourly reprovision storm. Only TWO consecutive 401s (the
    ``.consec_401`` counter) count as persistent token invalidation, and the
    delete is ADDITIONALLY capped to once per day (``.reprovision_401``). A
    successful (or otherwise non-401) ping resets the streak counter."""

    @staticmethod
    def _raise_401(req, timeout):
        import urllib.error
        raise urllib.error.HTTPError(
            str(req.full_url), 401, "Unauthorized", None, None
        )

    @staticmethod
    def _ok_ctx():
        mock_resp = type("R", (), {"getcode": lambda self: 200})()
        return type("Ctx", (), {
            "__enter__": lambda self: mock_resp,
            "__exit__": lambda self, *a: False,
        })()

    @staticmethod
    def _write_token_pair(home: Path) -> Path:
        d = home / "aco" / "telemetry"
        d.mkdir(parents=True, exist_ok=True)
        (d / "htrace-token.txt").write_text("stale-instance-token", encoding="utf-8")
        (d / ".telemetry_token").write_text("stale-telemetry-token", encoding="utf-8")
        return d

    def test_single_transient_401_does_not_delete_tokens(self, tmp_path):
        """RESIDUAL core: ONE 401 must never destroy the token pair — no
        re-provision, no retry, tokens stay on disk. The consecutive-401 counter
        is bumped to 1 and awaits a second confirmation."""
        home = _make_home(tmp_path)
        tel_dir = self._write_token_pair(home)

        with (
            patch.object(hu, "ping_enabled", return_value=True),
            patch.object(hu, "ensure_ping_tokens", return_value=True) as mock_ensure,
            patch.object(hu, "load_or_create_instance_id", return_value="iid"),
            patch.object(hu, "_detect_active_engine", return_value="claude_code"),
            patch.object(hu, "_open_no_redirect", side_effect=self._raise_401) as mock_open,
        ):
            result = hu.ping_if_due(home)

        assert result is False
        assert mock_open.call_count == 1, "a single 401 must NOT be retried"
        assert mock_ensure.call_count == 1, (
            "only the normal entry check may run — a single 401 must NOT "
            "re-provision"
        )
        assert (tel_dir / "htrace-token.txt").exists(), (
            "a single transient 401 must not delete the instance token"
        )
        assert (tel_dir / ".telemetry_token").exists(), (
            "a single transient 401 must not delete the telemetry token"
        )
        assert not (tel_dir / ".reprovision_401").exists(), (
            "no destructive re-provision may have been attempted"
        )
        assert (tel_dir / ".consec_401").read_text().strip() == "1", (
            "the consecutive-401 counter must be at 1 after one 401"
        )

    def test_second_consecutive_401_deletes_reprovisions_and_retries_once(self, tmp_path):
        home = _make_home(tmp_path)
        tel_dir = self._write_token_pair(home)

        with (
            patch.object(hu, "ping_enabled", return_value=True),
            patch.object(hu, "ensure_ping_tokens", return_value=True) as mock_ensure,
            patch.object(hu, "load_or_create_instance_id", return_value="iid"),
            patch.object(hu, "_detect_active_engine", return_value="claude_code"),
            patch.object(hu, "_open_no_redirect", side_effect=self._raise_401) as mock_open,
        ):
            hu.ping_if_due(home)  # first 401 — counter → 1, no delete
            self._write_token_pair(home)  # tokens still present
            mock_open.reset_mock()
            mock_ensure.reset_mock()
            result = hu.ping_if_due(home)  # second consecutive 401 → recovery

        assert result is False, "a still-failing ping must stay a failure (fail-soft)"
        assert mock_open.call_count == 2, (
            "the second consecutive 401 triggers EXACTLY one retry "
            "(send + one retry, no retry loop)"
        )
        assert mock_ensure.call_count == 2, (
            "recovery must re-run ensure_ping_tokens after dropping the pair"
        )
        assert not (tel_dir / "htrace-token.txt").exists(), (
            "the persistent-401 recovery must delete the stale instance token"
        )
        assert not (tel_dir / ".telemetry_token").exists(), (
            "the persistent-401 recovery must delete the stale telemetry token"
        )
        assert (tel_dir / ".reprovision_401").exists(), (
            "the recovery must persist a daily backoff stamp"
        )

    def test_successful_ping_resets_the_consecutive_401_counter(self, tmp_path):
        """A non-401 result breaks the streak: after a 401 (counter → 1) a
        successful ping must clear ``.consec_401``, so a LATER isolated 401
        again needs a fresh second confirmation before any delete."""
        home = _make_home(tmp_path)
        tel_dir = self._write_token_pair(home)
        stamp = tmp_path / "last_ping"

        with (
            patch.object(hu, "ping_enabled", return_value=True),
            patch.object(hu, "ensure_ping_tokens", return_value=True),
            patch.object(hu, "_last_ping_path", return_value=stamp),
            patch.object(hu, "_load_telemetry_token", return_value="tok"),
            patch.object(hu, "_load_instance_token", return_value="itok"),
            patch.object(hu, "load_or_create_instance_id", return_value="iid"),
            patch.object(hu, "_detect_active_engine", return_value="claude_code"),
        ):
            with patch.object(hu, "_open_no_redirect", side_effect=self._raise_401):
                hu.ping_if_due(home)  # 401 → counter = 1
            assert (tel_dir / ".consec_401").read_text().strip() == "1"
            # A success in between (delete the rate-limit stamp so it sends).
            stamp.unlink(missing_ok=True)
            with patch.object(hu, "_open_no_redirect", return_value=self._ok_ctx()):
                assert hu.ping_if_due(home) is True

        assert not (tel_dir / ".consec_401").exists(), (
            "a successful ping must reset the consecutive-401 streak counter"
        )

    def test_second_reprovision_same_day_is_blocked(self, tmp_path):
        """After the persistent-401 recovery fires once, further 401s the same
        day must NOT delete the (re-provisioned) tokens again — the daily
        ``.reprovision_401`` cap gates a second destructive attempt even though
        the consecutive counter keeps climbing."""
        home = _make_home(tmp_path)
        self._write_token_pair(home)

        with (
            patch.object(hu, "ping_enabled", return_value=True),
            patch.object(hu, "ensure_ping_tokens", return_value=True),
            patch.object(hu, "load_or_create_instance_id", return_value="iid"),
            patch.object(hu, "_detect_active_engine", return_value="claude_code"),
            patch.object(hu, "_open_no_redirect", side_effect=self._raise_401) as mock_open,
        ):
            hu.ping_if_due(home)              # 401 → counter = 1
            hu.ping_if_due(home)              # 2nd 401 → recovery deletes + stamps
            tel_dir = self._write_token_pair(home)  # tokens re-appear on disk
            mock_open.reset_mock()
            result = hu.ping_if_due(home)     # 3rd 401 same day

        assert result is False
        assert mock_open.call_count == 1, (
            "a further 401 on the same day must NOT retry — the daily "
            ".reprovision_401 stamp gates the destructive recovery"
        )
        assert (tel_dir / "htrace-token.txt").exists(), (
            "the daily cap must keep the re-provisioned token pair untouched"
        )


class TestUploadBackgroundThread:
    """T2 (bridge review): run_upload_cycle() used to be invoked synchronously
    from the bridge's main inbox loop — up to ~60s of network timeouts stalled
    message handling and the SIGTERM drain. The uploader now offers a daemon
    background thread mirroring start_ping_thread(); run_upload_cycle's own
    flock + daily stamp keep the hourly cadence double-upload-free."""

    def _reset_guard(self):
        orig = hu._upload_thread_started
        hu._upload_thread_started = False
        return orig

    def test_start_upload_thread_does_not_block_caller_on_a_slow_upload(self, tmp_path):
        import time as _time

        release = threading.Event()
        called = threading.Event()

        def _blocking_cycle(_home):
            called.set()
            release.wait(timeout=10)
            # Plain return: after the patch is restored the daemon loop calls
            # the real run_upload_cycle on tmp_path once (local no_bundle
            # no-op) and parks in its hourly sleep until process exit.

        orig = self._reset_guard()
        try:
            with patch.object(hu, "run_upload_cycle", side_effect=_blocking_cycle):
                t0 = _time.monotonic()
                hu.start_upload_thread(tmp_path)
                elapsed = _time.monotonic() - t0
                assert elapsed < 1.0, (
                    f"start_upload_thread blocked the caller for {elapsed:.1f}s — "
                    "the upload cycle must run in a background daemon thread"
                )
                assert called.wait(timeout=5), (
                    "the background thread must actually run the upload cycle"
                )
        finally:
            release.set()
            hu._upload_thread_started = orig

    def test_start_upload_thread_is_idempotent_and_daemon(self):
        started_threads = []
        orig_thread = threading.Thread

        def _tracking_thread(*a, **k):
            t = orig_thread(*a, **k)
            started_threads.append(t)
            return t

        orig = self._reset_guard()
        try:
            with (
                patch.object(hu, "upload_loop", lambda home: None),
                patch("threading.Thread", side_effect=_tracking_thread),
            ):
                hu.start_upload_thread(Path("/fake/home"))
                hu.start_upload_thread(Path("/fake/home"))
        finally:
            hu._upload_thread_started = orig

        assert len(started_threads) == 1
        assert started_threads[0].daemon is True, (
            "the upload thread must be a daemon so it never blocks shutdown"
        )

    def test_upload_loop_reinvokes_run_upload_cycle_repeatedly(self):
        calls = []

        def _fake_cycle(home):
            calls.append(home)
            if len(calls) >= 3:
                raise SystemExit  # break out of the infinite loop for the test
            return ("no_bundle", 0)

        with (
            patch.object(hu, "run_upload_cycle", _fake_cycle),
            patch.object(hu.time, "sleep", lambda _s: None),  # no real waiting
        ):
            with pytest.raises(SystemExit):
                hu.upload_loop(Path("/fake/home"))

        assert len(calls) == 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
