"""Repo-wide pytest tripwire: tests must never destroy live operator state.

This is the third incarnation of the "test contaminates real operator state"
class (bridge-suite settings.json contamination, console sys.modules/env
pollution, and the 2026-07-08 uninstall-test wipe of the running bridge's
in-repo .corvin — session state, budgets, and the hash-chained audit log were
deleted by a green `pytest tests/test_uninstall_windows_autostart.py` run).

The guard is detection-only: it takes a cheap snapshot of the protected live
roots before every test and fails the test loudly if any of them disappeared.
It never redirects or mutates anything itself, so it cannot break legitimate
tests — a test only fails here if it (or code it invoked) deleted real state,
which is always a bug in the test's isolation.
"""
from __future__ import annotations

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent

# The protected paths, resolved ONCE at import time — before any test can patch
# `HOME` or `Path.home`.
#
# Resolving them per snapshot was a false-positive generator, and a guard that
# cries wolf about the exact incident it exists to catch is worse than no guard.
# Two tests in `tests/` monkeypatch the home directory (one via
# `monkeypatch.setenv("HOME", tmp_path)`, one via `monkeypatch.setattr(Path,
# "home", …)` — which patches the shared `pathlib.Path` class this file uses
# too). Fixture teardown for those patches runs AFTER this tripwire's teardown,
# so the "after" snapshot resolved `~` to an empty tmp dir, found no
# `corvin-voice` and no `corvin-*` units there, and reported:
#
#   LIVE OPERATOR STATE DESTROYED by this test
#     ~/.config/corvin-voice: was DELETED
#     ~/.config/systemd/user corvin-* units: directory itself was DELETED
#
# while both were fully intact on disk. Verified 2026-07-28: 30 corvin-* units
# present and every service running, immediately after the failure.
#
# Freezing the paths also makes the guard STRICTER, which is the point: a test
# that patches `HOME` and then deletes the REAL `~/.config/corvin-voice` through
# some other route is now still caught, whereas before the patched lookup could
# have hidden it.
def _frozen_paths() -> "dict[str, Path] | None":
    try:
        home = Path.home()
    except (OSError, RuntimeError):
        return None
    return {
        "repo_corvin": _REPO_ROOT / ".corvin",
        "voice_config": home / ".config" / "corvin-voice",
        "systemd_user": home / ".config" / "systemd" / "user",
        "plugin_cache": home / ".claude" / "plugins" / "cache" / "corvin-voice-local",
    }


_PROTECTED_PATHS = _frozen_paths()


def _protected_state(
    paths: "dict[str, Path] | None" = None,
) -> "dict[str, object] | None":
    """Snapshot the live roots a mis-isolated test has historically deleted.

    Returns None when the snapshot itself cannot be taken (missing HOME on
    a bare Windows CI, permission error, or a concurrent writer racing the
    iterdir — a live bridge legitimately runs next to pytest in this repo).
    The tripwire then skips its comparison for that test instead of
    erroring an innocent test with a raw traceback.

    Reads only :data:`_PROTECTED_PATHS`, never ``Path.home()`` — see the note
    above that constant.

    ``paths`` overrides the roots for ONE call, so
    ``tests/test_live_state_tripwire.py`` can exercise the comparison against a
    sandbox tree. It is a parameter rather than a monkeypatch of the module
    global for the same fixture-ordering reason this whole guard was mis-firing:
    a patched global is still in place when the tripwire's own teardown runs, so
    the guard would read the sandbox and report every real entry as deleted.
    """
    p = paths if paths is not None else _PROTECTED_PATHS
    if p is None:
        return None
    try:
        return {
            "repo .corvin top-level entries": (
                frozenset(q.name for q in p["repo_corvin"].iterdir())
                if p["repo_corvin"].is_dir() else None
            ),
            "~/.config/corvin-voice": p["voice_config"].is_dir(),
            "~/.config/systemd/user corvin-* units": (
                frozenset(q.name for q in p["systemd_user"].glob("corvin-*"))
                if p["systemd_user"].is_dir() else None
            ),
            "~/.claude/plugins/cache/corvin-voice-local": p["plugin_cache"].is_dir(),
        }
    except Exception:  # noqa: BLE001
        # Deliberately broad, matching this function's stated contract: "returns
        # None when the snapshot itself cannot be taken … the tripwire then skips
        # its comparison for that test instead of erroring an innocent test with a
        # raw traceback." (OSError, RuntimeError) was too narrow for the commonest
        # real cause — a test that monkeypatches `Path.stat`/`is_dir` GLOBALLY for
        # its own subject (e.g. test_voice_session_summary stubs
        # `Path.stat -> Mock(st_size=100)` to fake an audio file). The teardown
        # snapshot then hit `S_ISDIR(Mock)` → TypeError, which escaped and turned
        # two PASSING tests into teardown ERRORs. A guard that cannot take its
        # snapshot must stand down, never fail the test it is protecting.
        return None


@pytest.fixture(autouse=True)
def _isolated_bridge_outbox(monkeypatch, tmp_path):
    """Never let a test queue a real message for a real messenger.

    Fourth incarnation of the class this file guards: the workflow
    `deliver`/`ask_human`/`answer` node types write their envelope straight
    into `corvin_operator/bridges/shared/outbox/`, which the LIVE Discord/WhatsApp
    daemons poll. Every test run of those nodes therefore handed the running
    bridge a real send job — 724 of them, addressed to the test placeholder
    chat_id "owner-chat", were sitting in the Discord dead-letter dir on
    2026-07-26, each one costing a REST round-trip against Discord's
    invalid-request budget.

    Redirect the outbox to tmp for every test. A test that needs a specific
    path still wins: its own monkeypatch.setenv runs after this fixture.
    """
    monkeypatch.setenv("ADAPTER_OUTBOX", str(tmp_path / "outbox"))


@pytest.fixture(autouse=True)
def _live_state_tripwire(request: pytest.FixtureRequest):
    before = _protected_state()
    yield
    if before is None:
        return
    after = _protected_state()
    if after is None:
        return
    violations: list[str] = []
    for label, prev in before.items():
        cur = after[label]
        if isinstance(prev, frozenset):
            gone = prev - (cur if isinstance(cur, frozenset) else frozenset())
            if cur is None and prev:
                violations.append(f"{label}: directory itself was DELETED")
            elif gone:
                violations.append(f"{label}: deleted {sorted(gone)}")
        elif prev is True and cur is not True:
            violations.append(f"{label}: was DELETED")
    if violations:
        pytest.fail(
            "LIVE OPERATOR STATE DESTROYED by this test (isolation bug — "
            "inject sandbox roots instead of touching real paths):\n  "
            + "\n  ".join(violations),
            pytrace=False,
        )


# ── Gateway suite: loopback peer for TestClient (adversarial review E-09) ───────
# ``corvin_gateway.app._jwt_guard`` requires a Bearer JWT from every NON-loopback
# peer; Starlette's TestClient reports ("testclient", 50000), which the guard
# treats fail-closed as remote. The gateway suite models the local deployment,
# so every TestClient created from a test under core/gateway/tests/ defaults to
# ("127.0.0.1", 50000); an explicit ``client=`` always wins. Lives here (scoped
# by path) because a conftest.py inside core/gateway/tests/ collides with
# tests/conftest.py — both packages are named ``tests`` on sys.path.
import pytest as _pytest  # noqa: E402

_GATEWAY_TESTS = Path(__file__).resolve().parent / "core" / "gateway" / "tests"
_CONSOLE_TESTS = Path(__file__).resolve().parent / "core" / "console" / "tests"
_LOOPBACK_PEER = ("127.0.0.1", 50000)


@_pytest.fixture(autouse=True)
def _gateway_loopback_test_client(request, monkeypatch):
    try:
        _p = Path(str(request.node.fspath)).resolve()
        # The console suite drives the gateway app too (local-deployment model).
        under_gateway = _p.is_relative_to(_GATEWAY_TESTS) or _p.is_relative_to(_CONSOLE_TESTS)
    except Exception:  # noqa: BLE001
        under_gateway = False
    if not under_gateway:
        yield
        return
    from starlette.testclient import TestClient

    original_init = TestClient.__init__

    def _init(self, *args, **kwargs):
        if "client" not in kwargs:
            kwargs["client"] = _LOOPBACK_PEER
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(TestClient, "__init__", _init)
    yield


# ── Live-install isolation for EVERY pytest process (incident 2026-09-27) ──────
# Incident: 2 x 34 ``data_flow.approved`` records (engine_id codex_cli /
# claude_code, persona coder) landed in the LIVE chain
# ``/home/shumway/projects/CorvinOS/.corvin/tenants/_default/global/forge/audit.jsonl``
# at 2026-09-27 18:44 and 19:43 CEST. Reproduced exactly (same engine/persona
# sequence 10/1/6/10 + 7) by ``core/delegate/tests/test_delegation.py`` followed by
# ``test_output_judge.py`` / ``test_prompt_safety.py`` run WITHOUT ``CORVIN_HOME``:
# ``load_guard_for_tenant()`` fell back to ``forge.paths.corvin_home()``, which
# resolves the repo-marker home of the checkout that FILE lives in. From a second
# worktree that is a split brain: ``instance_identity`` (bridges ``paths``, found
# via the test's own sys.path insert) resolved the worktree's ``.corvin`` while
# ``corvin_operator`` came through the venv's ``.pth`` from the live checkout.
# Every pytest test therefore gets, BEFORE any test module is imported:
#   * ``CORVIN_HOME``              — session sandbox (a narrower per-test one set by
#                                    a deeper conftest/test still wins);
#   * ``XDG_CONFIG_HOME``          — sandbox (voice config dir, vault, keys);
#   * ``CORVIN_AUDIT_ANCHOR_KEY``  — sandbox key (MAC + out-of-tree sidecars);
#   * ``VOICE_CONFIG_DIR``         — removed unless it already points into tmp.
# They are written into ``os.environ`` so every subprocess inherits them. ``HOME``
# is deliberately left alone: live suites (CLAUDE_LIVE_E2E=1) need the real
# ``claude`` OAuth credentials under ``~/.claude``.
#
# Plus a tripwire that cannot be talked out of: the protected homes (this
# checkout's ``.corvin``, ``~/.corvin``, the repo-marker home of every CorvinOS
# checkout on ``sys.path`` — the venv ``.pth`` makes the live checkout one — and
# ``CORVIN_TEST_PROTECTED_HOMES``) are resolved ONCE here, before any env change.
#   * a resolver (forge / core / bridges ``corvin_home()`` or
#     ``tenant_audit_chain()``) pointing into one → ``pytest.exit``;
#   * a test that leaks a non-restored env change back to a protected home →
#     env re-armed at once, the test named in the terminal summary;
#   * each protected audit chain is stat'ed (read-only) around every test; records
#     appended from a FOREIGN instance (or any append to a home without an
#     ``instance_id.json``), or a shrink / inode swap → ``pytest.exit`` naming
#     the test. Appends carrying the home's OWN instance id are indistinguishable
#     from the running live services and are listed in the terminal summary.
import json as _json
import os as _os
import shutil as _shutil
import sys as _sys
import tempfile as _tempfile

_REAL_HOME = Path(_os.path.expanduser("~")).resolve()
_REAL_CONFIG = _REAL_HOME / ".config"
_REAL_VOICE_CONFIG = _REAL_CONFIG / "corvin-voice"
_ISOLATED_KEYS = ("CORVIN_HOME", "XDG_CONFIG_HOME", "CORVIN_AUDIT_ANCHOR_KEY")


def _norm(p: "str | Path") -> Path:
    return Path(_os.path.abspath(_os.path.expanduser(_os.path.expandvars(str(p)))))


def _under(p: Path, root: Path) -> bool:
    try:
        return p == root or p.is_relative_to(root)
    except Exception:  # noqa: BLE001
        return False


def _checkout_root(start: Path) -> "Path | None":
    """The CorvinOS checkout containing *start* (same marker rule as paths.py)."""
    try:
        here = start.resolve()
    except Exception:  # noqa: BLE001
        return None
    for parent in [here, *list(here.parents)[:6]]:
        if (parent / ".corvin_repo").exists():
            return parent
    return None


def _discover_protected_homes() -> "tuple[Path, ...]":
    homes = {_REPO_ROOT / ".corvin", _REAL_HOME / ".corvin"}
    try:  # the account's home even when a runner already swapped $HOME
        import pwd as _pwd  # noqa: PLC0415
        homes.add(Path(_pwd.getpwuid(_os.getuid()).pw_dir) / ".corvin")
    except Exception:  # noqa: BLE001 — no pwd on Windows
        pass
    for entry in list(_sys.path):
        root = _checkout_root(Path(entry or "."))
        if root is not None:
            homes.add(root / ".corvin")
    start_home = _os.environ.get("CORVIN_HOME", "").strip()
    if start_home and not _is_tmp_path(_norm(start_home)):
        homes.add(_norm(start_home))  # the operator's shell pointed at a real install
    for extra in _os.environ.get("CORVIN_TEST_PROTECTED_HOMES", "").split(_os.pathsep):
        if extra.strip():
            homes.add(_norm(extra.strip()))
    return tuple(sorted({_norm(h) for h in homes} | {h.resolve() for h in homes}))


def _is_tmp_path(p: Path) -> bool:
    roots = {_norm(_tempfile.gettempdir()), Path("/tmp"), Path("/var/tmp")}
    return any(_under(p, r) for r in roots)


_PROTECTED_HOMES = _discover_protected_homes()


def _is_protected(p: "str | Path") -> bool:
    n = _norm(p)
    cands = {n}
    try:
        cands.add(n.resolve())
    except Exception:  # noqa: BLE001
        pass
    return any(_under(c, h) for c in cands for h in _PROTECTED_HOMES)


def _unsafe(key: str, value: "str | None") -> bool:
    """True when *value* for *key* would let a test touch real operator state."""
    if value is None or not value.strip():
        return True
    if key == "CORVIN_AUDIT_ANCHOR_KEY" and not _os.path.isabs(
            _os.path.expanduser(_os.path.expandvars(value.strip()))):
        return True  # relative: the key would be created in the CWD (repo root)
    p = _norm(value.strip())
    if _is_protected(p):
        return True
    if key == "XDG_CONFIG_HOME":
        return p == _REAL_CONFIG or _under(_REAL_CONFIG, p)
    if key == "CORVIN_AUDIT_ANCHOR_KEY":
        return _under(p, _REAL_VOICE_CONFIG)
    return False


def _install_session_sandbox() -> "tuple[Path, bool, dict[str, str]]":
    existing = _os.environ.get("CORVIN_PYTEST_SESSION_SANDBOX", "")
    if existing and Path(existing).is_dir():
        sandbox, created = Path(existing), False  # nested pytest: reuse, never delete
    else:
        sandbox, created = Path(_tempfile.mkdtemp(prefix="corvin-pytest-session-")), True
        _os.environ["CORVIN_PYTEST_SESSION_SANDBOX"] = str(sandbox)
    defaults = {
        "CORVIN_HOME": str(sandbox / "corvin_home"),
        "XDG_CONFIG_HOME": str(sandbox / "xdg_config"),
        "CORVIN_AUDIT_ANCHOR_KEY": str(sandbox / "anchor" / "audit_anchor.key"),
    }
    for key, default in defaults.items():
        if _unsafe(key, _os.environ.get(key)):
            _os.environ[key] = default
        # else: the invoking shell already gave a non-live location — respect it.
    for key in _ISOLATED_KEYS:
        target = Path(_os.environ[key])
        (target.parent if key == "CORVIN_AUDIT_ANCHOR_KEY" else target).mkdir(
            parents=True, exist_ok=True, mode=0o700)
    vcd = _os.environ.get("VOICE_CONFIG_DIR", "")
    if vcd and not _is_tmp_path(_norm(vcd)):
        _os.environ.pop("VOICE_CONFIG_DIR", None)
    return sandbox, created, {k: _os.environ[k] for k in _ISOLATED_KEYS}


_SESSION_SANDBOX, _SANDBOX_CREATED, _SESSION_ENV = _install_session_sandbox()


def _resolver_targets() -> "list[tuple[str, Path]]":
    """(label, path) for every corvin_home()/tenant_audit_chain() mirror."""
    out: "list[tuple[str, Path]]" = []
    mods = []
    try:
        from corvin_operator.forge.forge import paths as _fp  # noqa: PLC0415
        mods.append(("forge.paths", _fp))
    except Exception:  # noqa: BLE001
        pass
    try:
        from core.paths import tenant as _cp  # noqa: PLC0415
        mods.append(("core.paths.tenant", _cp))
    except Exception:  # noqa: BLE001
        pass
    _bp = _sys.modules.get("_corvin_isolation_bridges_paths")
    if _bp is None:
        try:
            import importlib.util as _ilu  # noqa: PLC0415
            _spec = _ilu.spec_from_file_location(
                "_corvin_isolation_bridges_paths",
                _REPO_ROOT / "corvin_operator" / "bridges" / "shared" / "paths.py")
            _bp = _ilu.module_from_spec(_spec)
            _spec.loader.exec_module(_bp)
            _sys.modules["_corvin_isolation_bridges_paths"] = _bp
        except Exception:  # noqa: BLE001
            _bp = None
    if _bp is not None:
        mods.append(("bridges.paths", _bp))
    for label, mod in mods:
        for fn, args in (("corvin_home", ()), ("tenant_audit_chain", ("_default",))):
            try:
                out.append((f"{label}.{fn}", Path(getattr(mod, fn)(*args))))
            except Exception:  # noqa: BLE001
                continue
    return out


def _resolution_violations() -> "list[str]":
    return [f"{label}() -> {p}" for label, p in _resolver_targets() if _is_protected(p)]


# ── read-only stat snapshot of every protected audit chain ──
def _protected_chains() -> "list[Path]":
    found: "list[Path]" = []
    for home in _PROTECTED_HOMES:
        cands = [home / "forge" / "audit.jsonl", home / "global" / "forge" / "audit.jsonl"]
        tenants = home / "tenants"
        try:
            if tenants.is_dir():
                cands += [t / "global" / "forge" / "audit.jsonl" for t in tenants.iterdir()]
        except OSError:
            pass
        found += [c for c in cands if c.is_file()]
    return found


def _chain_snapshot() -> "dict[Path, tuple[int, int, int]]":
    snap = {}
    for c in _protected_chains():
        try:
            st = c.stat()
            snap[c] = (st.st_ino, st.st_size, st.st_mtime_ns)
        except OSError:
            continue
    return snap


def _own_instance_id(chain: Path) -> "str | None":
    for home in _PROTECTED_HOMES:
        if _under(chain, home):
            try:
                return _json.loads((home / "global" / "instance_id.json").read_text())["instance_id"]
            except Exception:  # noqa: BLE001
                return None
    return None


def _appended_records(chain: Path, start: int, end: int) -> "list[dict]":
    recs = []
    try:
        with open(chain, "rb") as fh:  # read-only
            fh.seek(start)
            blob = fh.read(min(end - start, 8 * 1024 * 1024))
    except OSError:
        return recs
    for line in blob.splitlines():
        try:
            recs.append(_json.loads(line))
        except Exception:  # noqa: BLE001
            recs.append({"event_type": "<unparseable>"})
    return recs


_UNATTRIBUTED_GROWTH: "list[str]" = []
_ENV_LEAKS: "list[str]" = []


def _chain_violations(before: dict, after: dict) -> "list[str]":
    out = []
    for chain, (ino, size, _mt) in before.items():
        cur = after.get(chain)
        if cur is None:
            out.append(f"{chain}: DELETED")
            continue
        c_ino, c_size, _ = cur
        if c_ino != ino or c_size < size:
            out.append(f"{chain}: REWRITTEN (inode {ino}->{c_ino}, size {size}->{c_size})")
            continue
        if c_size == size:
            continue
        own = _own_instance_id(chain)
        recs = _appended_records(chain, size, c_size)
        foreign = [r for r in recs if own is None or r.get("instance_id") not in (own, None)]
        if foreign:
            kinds = sorted({str(r.get("event_type")) for r in foreign})
            iids = sorted({str(r.get("instance_id", "?"))[:8] for r in foreign})
            out.append(f"{chain}: {len(foreign)} foreign record(s) appended "
                       f"(instance {iids}, events {kinds[:6]})")
        elif recs:
            _UNATTRIBUTED_GROWTH.append(
                f"{chain}: +{len(recs)} record(s) with the install's own instance id "
                f"({sorted({str(r.get('event_type')) for r in recs})[:4]})")
    for chain in after.keys() - before.keys():
        own = _own_instance_id(chain)
        if own is None:
            out.append(f"{chain}: CREATED during the test")
    return out


def pytest_report_header(config):
    return [
        f"corvin isolation: sandbox={_SESSION_SANDBOX}",
        "corvin isolation: protected homes=" + ", ".join(str(h) for h in _PROTECTED_HOMES),
    ]


def pytest_sessionstart(session):
    bad = _resolution_violations()
    if bad:
        pytest.exit("corvin isolation: a path resolver points at a PROTECTED live "
                    "install before any test ran — refusing to start:\n  "
                    + "\n  ".join(bad), returncode=3)


def pytest_unconfigure(config):
    if _SANDBOX_CREATED:
        _shutil.rmtree(_SESSION_SANDBOX, ignore_errors=True)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_setup(item):
    item._corvin_chain_before = _chain_snapshot()
    return (yield)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_teardown(item, nextitem):
    try:
        return (yield)
    finally:
        # Every fixture (incl. monkeypatch) is finalised now. Anything still
        # pointing at a protected home is a leak the next test would inherit.
        leaked = [k for k in _ISOLATED_KEYS if _unsafe(k, _os.environ.get(k))]
        for k in leaked:
            _os.environ[k] = _SESSION_ENV[k]
        before = getattr(item, "_corvin_chain_before", None)
        if before is not None:
            bad = _chain_violations(before, _chain_snapshot())
            if bad:
                pytest.exit(
                    f"LIVE AUDIT CHAIN TOUCHED during {item.nodeid} (or a background "
                    "writer it started) — stopping the session before more damage. "
                    "Isolate it: inherit the pytest env in subprocesses, never "
                    "scrub CORVIN_HOME.\n  " + "\n  ".join(bad), returncode=3)
        if leaked:
            # Re-armed above, before anything else runs — so this is reported, not
            # failed: dozens of unittest-style tearDowns ``os.environ.pop()`` the
            # key instead of restoring it, and they are harmless once re-armed.
            _ENV_LEAKS.append(f"{item.nodeid}: left {leaked} unset/live (re-armed)")


@pytest.fixture(autouse=True)
def _corvin_live_isolation_env(monkeypatch):
    """Per-test re-arm of the session sandbox (a narrower test value still wins)."""
    for key in _ISOLATED_KEYS:
        if _unsafe(key, _os.environ.get(key)):
            monkeypatch.setenv(key, _SESSION_ENV[key])
    vcd = _os.environ.get("VOICE_CONFIG_DIR", "")
    if vcd and not _is_tmp_path(_norm(vcd)):
        monkeypatch.delenv("VOICE_CONFIG_DIR")
    bad = _resolution_violations()
    if bad:
        pytest.exit("corvin isolation: path resolver points at a PROTECTED live "
                    "install:\n  " + "\n  ".join(bad), returncode=3)
    yield


def pytest_terminal_summary(terminalreporter):
    if _ENV_LEAKS:
        terminalreporter.write_sep(
            "-", f"corvin isolation: {len(_ENV_LEAKS)} test(s) left CORVIN_HOME/XDG/anchor "
                 "pointing at the live install — re-armed; fix with monkeypatch")
        for line in _ENV_LEAKS[:20]:
            terminalreporter.write_line(line)
    if _UNATTRIBUTED_GROWTH:
        terminalreporter.write_sep("-", "corvin isolation: protected chain grew (own instance id)")
        for line in _UNATTRIBUTED_GROWTH[:20]:
            terminalreporter.write_line(line)
        terminalreporter.write_line(
            "These carry the live install's own instance id, so they are most likely "
            "the running services — or a subprocess that scrubbed CORVIN_HOME.")
