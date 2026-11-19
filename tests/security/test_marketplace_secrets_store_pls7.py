"""Review 2026-11-19, PLS-7: the marketplace secrets store is locked, atomic and private.

Until this fix ``SecretsStore`` rewrote ``secrets.yaml`` with ``open(path, "w")`` at the
process umask and without any lock: the encrypted tokens were world-readable (0o644), a crash
mid-write left a truncated file, and two writers each loaded the file, added their own token
and wrote back - the later write silently dropped the earlier token.

The lost-update test uses REAL processes (the lock is a file lock; threads in one process
would only prove the in-process half).
"""
from __future__ import annotations

import base64
import os
import stat
import subprocess
import sys
import threading
from pathlib import Path

import pytest
import yaml

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from core.marketplace.auth import secrets_store as ss  # noqa: E402

KEY = base64.b64encode(bytes(range(32))).decode()


def _token(n: int) -> str:
    return "ghp_" + f"{n:036d}"


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("CORVIN_GITHUB_TOKEN_KEY", KEY)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


posix_only = pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")


@posix_only
def test_a_new_store_is_created_private_whatever_the_umask(tmp_path):
    old = os.umask(0o022)
    try:
        path = tmp_path / "sub" / "secrets.yaml"
        store = ss.SecretsStore(path)
        assert _mode(path) == 0o600
        store.store_token("https://github.com/a/b", _token(1), "t1")
        assert _mode(path) == 0o600, "os.replace carried the temp file's mode - it must be 0o600"
        store.delete_token("https://github.com/a/b", "t1")
        assert _mode(path) == 0o600
    finally:
        os.umask(old)


@posix_only
def test_an_existing_world_readable_store_is_tightened_on_open(tmp_path):
    path = tmp_path / "secrets.yaml"
    path.write_text(yaml.dump({"repositories": {}}), encoding="utf-8")
    path.chmod(0o644)
    ss.SecretsStore(path)
    assert _mode(path) == 0o600


def test_round_trip_and_tenant_isolation_still_hold(tmp_path):
    store = ss.SecretsStore(tmp_path / "secrets.yaml")
    store.store_token("https://github.com/a/b", _token(7), "t1")
    assert store.retrieve_token("https://github.com/a/b", "t1") == _token(7)
    with pytest.raises(ss.TokenNotFoundError):
        store.retrieve_token("https://github.com/a/b", "t2")
    assert store.list_tokens_for_tenant("t1") == ["https://github.com/a/b"]
    assert store.list_tokens_for_tenant("t2") == []
    # the token itself is never on disk
    assert _token(7) not in (tmp_path / "secrets.yaml").read_text(encoding="utf-8")


def test_a_failed_write_leaves_the_previous_file_and_no_temp(tmp_path, monkeypatch):
    path = tmp_path / "secrets.yaml"
    store = ss.SecretsStore(path)
    store.store_token("https://github.com/a/b", _token(1), "t1")
    before = path.read_bytes()

    def _boom(src, dst):
        raise OSError("disk full")

    with monkeypatch.context() as m:
        m.setattr(ss.os, "replace", _boom)
        with pytest.raises(ss.SecretsStoreError):
            store.store_token("https://github.com/c/d", _token(2), "t1")

    assert path.read_bytes() == before, "the previous file is intact, never truncated"
    assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []
    assert ss.SecretsStore(path).retrieve_token("https://github.com/a/b", "t1") == _token(1)


def test_the_write_goes_through_a_fresh_temp_never_the_target(tmp_path, monkeypatch):
    """A crash between truncate and write was the old failure: the target must never be
    opened for writing. The replace source is a sibling temp in the same directory."""
    path = tmp_path / "secrets.yaml"
    store = ss.SecretsStore(path)
    seen: list[tuple[str, str]] = []
    real = os.replace

    def _spy(src, dst):
        seen.append((str(src), str(dst)))
        assert Path(src).parent == path.parent and Path(src) != path
        if os.name == "posix":
            assert _mode(Path(src)) == 0o600, "chmod BEFORE replace"
        return real(src, dst)

    monkeypatch.setattr(ss.os, "replace", _spy)
    store.store_token("https://github.com/a/b", _token(1), "t1")
    assert len(seen) == 1 and seen[0][1] == str(path)


@posix_only
def test_a_planted_symlink_at_a_predictable_temp_name_is_not_followed(tmp_path):
    """The earlier fixed-name pattern (``<name>.tmp``) would write the ciphertexts through a
    planted link. mkstemp never opens an existing name."""
    path = tmp_path / "secrets.yaml"
    victim = tmp_path / "victim.txt"
    victim.write_text("untouched", encoding="utf-8")
    for name in ("secrets.yaml.tmp", "secrets.tmp"):
        (tmp_path / name).symlink_to(victim)
    store = ss.SecretsStore(path)
    store.store_token("https://github.com/a/b", _token(1), "t1")
    assert victim.read_text(encoding="utf-8") == "untouched"
    assert store.retrieve_token("https://github.com/a/b", "t1") == _token(1)


_WRITER = """
import sys
sys.path.insert(0, {repo!r})
from core.marketplace.auth.secrets_store import SecretsStore
store = SecretsStore({path!r})
w = int(sys.argv[1])
for i in range({per}):
    store.store_token(f"https://github.com/w{{w}}/r{{i}}", "ghp_" + f"{{w * 1000 + i:036d}}", "t1")
"""


def test_concurrent_processes_lose_no_token(tmp_path):
    path = tmp_path / "secrets.yaml"
    ss.SecretsStore(path)
    workers, per = 6, 8
    code = _WRITER.format(repo=str(_REPO), path=str(path), per=per)
    env = {**os.environ, "CORVIN_GITHUB_TOKEN_KEY": KEY}
    procs = [subprocess.Popen([sys.executable, "-c", code, str(w)], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
             for w in range(workers)]
    for p in procs:
        out, err = p.communicate(timeout=120)
        assert p.returncode == 0, err[-2000:]

    store = ss.SecretsStore(path)
    urls = set(store.list_tokens_for_tenant("t1"))
    expected = {f"https://github.com/w{w}/r{i}" for w in range(workers) for i in range(per)}
    assert urls == expected, f"lost {len(expected - urls)} of {len(expected)} tokens"
    assert store.retrieve_token("https://github.com/w3/r5", "t1") == "ghp_" + f"{3005:036d}"
    if os.name == "posix":
        assert _mode(path) == 0o600
    assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []


def test_concurrent_threads_lose_no_token(tmp_path):
    store = ss.SecretsStore(tmp_path / "secrets.yaml")
    errors: list[BaseException] = []

    def _work(w: int) -> None:
        try:
            for i in range(10):
                store.store_token(f"https://github.com/w{w}/r{i}", _token(w * 100 + i), "t1")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=_work, args=(w,)) for w in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == []
    assert len(store.list_tokens_for_tenant("t1")) == 80


def test_the_lock_is_not_taken_on_the_data_file(tmp_path):
    """The data file is replaced on every save: a lock on its old inode excludes nobody."""
    path = tmp_path / "secrets.yaml"
    store = ss.SecretsStore(path)
    inode_before = path.stat().st_ino
    store.store_token("https://github.com/a/b", _token(1), "t1")
    assert (tmp_path / "secrets.yaml.lock").exists()
    if os.name == "posix":
        assert path.stat().st_ino != inode_before, "the save replaced the file"
        assert _mode(tmp_path / "secrets.yaml.lock") == 0o600


def test_the_process_lock_is_not_re_entrant_and_no_operation_nests_it(tmp_path):
    """R2-BR-11: an RLock let a nested _locked() through the in-process half and then blocked
    it forever on its own outer flock (a second descriptor on the lock file). The lock is a
    plain Lock, and every public operation enters _locked() at most once at a time."""
    assert type(ss.SecretsStore._process_lock) is type(threading.Lock())

    store = ss.SecretsStore(tmp_path / "secrets.yaml")
    depth = {"now": 0, "max": 0}
    real = ss.SecretsStore._locked

    import contextlib

    @contextlib.contextmanager
    def _counting(self):
        depth["now"] += 1
        depth["max"] = max(depth["max"], depth["now"])
        try:
            if depth["now"] > 1:
                raise AssertionError("nested _locked()")
            with real(self):
                yield
        finally:
            depth["now"] -= 1

    ss.SecretsStore._locked = _counting
    try:
        store2 = ss.SecretsStore(tmp_path / "secrets.yaml")
        store2.store_token("https://github.com/a/b", _token(1), "t1")
        assert store2.token_exists("https://github.com/a/b", "t1")
        assert store2.retrieve_token("https://github.com/a/b", "t1") == _token(1)
        assert store2.list_tokens_for_tenant("t1") == ["https://github.com/a/b"]
        store2.delete_token("https://github.com/a/b", "t1")
    finally:
        ss.SecretsStore._locked = real
    assert depth["max"] == 1


def test_a_second_thread_waits_for_the_lock_instead_of_passing_it(tmp_path):
    store = ss.SecretsStore(tmp_path / "secrets.yaml")
    entered = threading.Event()
    order: list[str] = []

    def _other():
        entered.set()
        with store._locked():
            order.append("other")

    with store._locked():
        t = threading.Thread(target=_other)
        t.start()
        entered.wait(5)
        t.join(0.3)
        assert t.is_alive(), "the second thread got the lock while it was held"
        order.append("first")
    t.join(5)
    assert order == ["first", "other"]
