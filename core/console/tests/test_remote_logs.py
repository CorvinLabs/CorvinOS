"""Remote-instance log ingestion (Hetzner etc.) — config, secret-safe rsync filter,
and the RemoteLogFiber that analyses the mirrored logs."""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
for _p in (_REPO / "core" / "console", _REPO / "operator" / "forge"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from corvin_console.aco import remote_logs as RL  # type: ignore
from corvin_console.aco import nerve_builtins as NB  # type: ignore


def test_config_roundtrip(tmp_path):
    RL.save_remotes(tmp_path, [{"name": "hetzner", "ssh": "root@1.2.3.4",
                                "remote_home": "/opt/corvin/.corvin"}])
    got = RL.load_remotes(tmp_path)
    assert got and got[0]["name"] == "hetzner"


def test_pull_uses_secret_safe_filter(tmp_path):
    captured = {}
    def stub(cmd):
        captured["cmd"] = cmd
        return (0, "ok")
    RL.save_remotes(tmp_path, [{"name": "h", "ssh": "root@1.2.3.4", "remote_home": "/opt/corvin/.corvin"}])
    res = RL.pull_all(tmp_path, runner=stub)
    assert res[0]["ok"] is True
    cmd = " ".join(captured["cmd"])
    # secrets are excluded BEFORE any include; only logs/healing are pulled
    for excl in ("--exclude=*.key", "--exclude=*.env", "--exclude=*secret*", "--exclude=id_rsa*"):
        assert excl in cmd
    assert "--include=**/corvin.log*" in cmd and "--include=**/audit.jsonl" in cmd
    assert cmd.index("--exclude=*.key") < cmd.index("--include=**/corvin.log*")  # order!
    assert "root@1.2.3.4:/opt/corvin/.corvin/" in cmd


def test_pull_no_ssh_is_safe(tmp_path):
    res = RL.pull_remote(tmp_path, {"name": "x"})
    assert res["ok"] is False and "no ssh" in res["error"]


def test_remote_log_fiber_flags_mirrored_errors(tmp_path, monkeypatch):
    home = tmp_path / "home"
    mirror = home / "aco" / "remote" / "hetzner" / "logs"
    mirror.mkdir(parents=True)
    (mirror / "corvin.log").write_text("\n".join(["ERROR boom"] * 40), encoding="utf-8")
    monkeypatch.setattr(NB, "_home", lambda: home)
    sigs = NB.RemoteLogFiber().scan()
    assert any(s.signal_type == "remote.error_spike" and s.data.get("remote") == "hetzner"
               for s in sigs)


def test_remote_log_fiber_silent_when_clean(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "aco" / "remote" / "h" / "logs").mkdir(parents=True)
    (home / "aco" / "remote" / "h" / "logs" / "corvin.log").write_text("INFO ok\n" * 50)
    monkeypatch.setattr(NB, "_home", lambda: home)
    assert NB.RemoteLogFiber().scan() == []


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
