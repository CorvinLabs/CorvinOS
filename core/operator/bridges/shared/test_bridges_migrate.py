"""ADR-0008 Phase 8.2 per-subtask E2E for bridges_migrate."""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

THIS = Path(__file__).resolve()
sys.path.insert(0, str(THIS.parent))
# Make forge importable for the audit chain check.
_repo = THIS.parents[3]
sys.path.insert(0, str(_repo / "operator" / "forge"))

import bridges_migrate  # noqa: E402


def _clean_env():
    for key in list(os.environ.keys()):
        if key.startswith("CORVIN_BRIDGE") or key in (
            "CORVIN_HOME", "CORVIN_BRIDGES_HOME",
            "CORVIN_BRIDGES_MIGRATE", "CORVIN_HOME",
        ):
            os.environ.pop(key, None)


def _fake_repo(tmp: Path, channels=("discord", "email")) -> Path:
    """Build a fake repo tree with some private bridge state."""
    repo = tmp / "repo"
    bridges = repo / "operator" / "bridges"
    bridges.mkdir(parents=True)
    for ch in channels:
        ch_dir = bridges / ch
        ch_dir.mkdir()
        # Inbox + outbox + processed with realistic content
        (ch_dir / "inbox").mkdir()
        (ch_dir / "inbox" / "msg-1.json").write_text(
            json.dumps({"from": "x", "text": "hello"})
        )
        (ch_dir / "outbox").mkdir()
        (ch_dir / "outbox" / "reply-1.json").write_text(
            json.dumps({"chat_id": 1, "text": "reply"})
        )
        (ch_dir / "processed").mkdir()
        # Attachments
        (ch_dir / "attachments").mkdir()
        (ch_dir / "attachments" / "chat-export.txt").write_text(
            "PRIVATE: should-never-leak"
        )
        # settings.json + voice.log files
        (ch_dir / "settings.json").write_text('{"token": "secret"}')
        (ch_dir / "voice.log").write_text("[boot] daemon up\n")
    return repo


def case_opt_out_env() -> None:
    with tempfile.TemporaryDirectory() as tmp_s:
        tmp = Path(tmp_s)
        repo = _fake_repo(tmp)
        home = tmp / "corvin"
        _clean_env()
        os.environ["CORVIN_BRIDGES_MIGRATE"] = "0"
        result = bridges_migrate.migrate_bridges_state_if_needed(
            repo_root=repo, corvin_home=home,
        )
        assert result["status"] == "skipped-opt-out", result
        # No move happened
        assert (repo / "operator" / "bridges" / "discord" / "inbox").exists()


def case_no_legacy_content() -> None:
    with tempfile.TemporaryDirectory() as tmp_s:
        tmp = Path(tmp_s)
        repo = tmp / "repo"
        (repo / "operator" / "bridges").mkdir(parents=True)
        home = tmp / "corvin"
        _clean_env()
        result = bridges_migrate.migrate_bridges_state_if_needed(
            repo_root=repo, corvin_home=home,
        )
        assert result["status"] == "skipped-nothing", result
        assert result["moves"] == []


def case_dry_run() -> None:
    with tempfile.TemporaryDirectory() as tmp_s:
        tmp = Path(tmp_s)
        repo = _fake_repo(tmp)
        home = tmp / "corvin"
        _clean_env()
        result = bridges_migrate.migrate_bridges_state_if_needed(
            repo_root=repo, corvin_home=home, dry_run=True,
        )
        assert result["status"] == "dry-run", result
        # Sources untouched
        assert (repo / "operator" / "bridges" / "discord" / "inbox" / "msg-1.json").exists()
        # Target not created
        assert not (home / "bridges").exists()
        # Plan covers every present source
        kinds_per_channel = {(m["channel"], m["kind"]) for m in result["moves"]}
        for ch in ("discord", "email"):
            for kind in ("inbox", "outbox", "processed", "attachments"):
                assert (ch, kind) in kinds_per_channel, (ch, kind)
            assert (ch, "root") in kinds_per_channel       # settings.json
            assert (ch, "log") in kinds_per_channel        # voice.log


def case_full_migration_same_fs() -> None:
    with tempfile.TemporaryDirectory() as tmp_s:
        tmp = Path(tmp_s)
        repo = _fake_repo(tmp)
        home = tmp / "corvin"
        _clean_env()
        result = bridges_migrate.migrate_bridges_state_if_needed(
            repo_root=repo, corvin_home=home,
        )
        assert result["status"] == "migrated", result
        assert result["failed"] == 0
        # Targets exist + sources gone (rename method)
        d_inbox = home / "bridges" / "discord" / "inbox" / "msg-1.json"
        assert d_inbox.exists()
        assert json.loads(d_inbox.read_text())["text"] == "hello"
        e_attach = home / "bridges" / "email" / "attachments" / "chat-export.txt"
        assert e_attach.exists()
        assert "PRIVATE" in e_attach.read_text()
        # settings.json now lives at the channel root
        assert (home / "bridges" / "discord" / "settings.json").exists()
        # voice.log under log/
        assert (home / "bridges" / "discord" / "log" / "voice.log").exists()
        # Source dirs gone (rename)
        assert not (repo / "operator" / "bridges" / "discord" / "inbox").exists()
        assert not (repo / "operator" / "bridges" / "email" / "settings.json").exists()
        # Marker present
        assert (home / "bridges" / ".bridges-migrated").exists()


def case_idempotent_second_run() -> None:
    with tempfile.TemporaryDirectory() as tmp_s:
        tmp = Path(tmp_s)
        repo = _fake_repo(tmp)
        home = tmp / "corvin"
        _clean_env()
        first = bridges_migrate.migrate_bridges_state_if_needed(
            repo_root=repo, corvin_home=home,
        )
        assert first["status"] == "migrated"
        # Add fresh "leftover" content to source — second run must NOT
        # touch it, because the marker file short-circuits.
        ch_inbox = repo / "operator" / "bridges" / "discord" / "inbox"
        ch_inbox.mkdir(parents=True, exist_ok=True)
        (ch_inbox / "msg-2.json").write_text(json.dumps({"text": "leftover"}))
        second = bridges_migrate.migrate_bridges_state_if_needed(
            repo_root=repo, corvin_home=home,
        )
        assert second["status"] == "skipped-marker", second
        # Leftover NOT moved (marker honoured)
        assert (ch_inbox / "msg-2.json").exists()
        assert not (home / "bridges" / "discord" / "inbox" / "msg-2.json").exists()


def case_force_runs_again() -> None:
    with tempfile.TemporaryDirectory() as tmp_s:
        tmp = Path(tmp_s)
        repo = _fake_repo(tmp)
        home = tmp / "corvin"
        _clean_env()
        bridges_migrate.migrate_bridges_state_if_needed(
            repo_root=repo, corvin_home=home,
        )
        # Set up additional source
        ch_inbox = repo / "operator" / "bridges" / "discord" / "inbox"
        ch_inbox.mkdir(parents=True, exist_ok=True)
        (ch_inbox / "msg-2.json").write_text(json.dumps({"text": "second"}))
        forced = bridges_migrate.migrate_bridges_state_if_needed(
            repo_root=repo, corvin_home=home, force=True,
        )
        assert forced["status"] == "migrated", forced
        assert (home / "bridges" / "discord" / "inbox" / "msg-2.json").exists()


def case_audit_event_emitted() -> None:
    with tempfile.TemporaryDirectory() as tmp_s:
        tmp = Path(tmp_s)
        repo = _fake_repo(tmp)
        home = tmp / "corvin"
        audit = home / "global" / "forge" / "audit.jsonl"
        audit.parent.mkdir(parents=True)
        _clean_env()
        bridges_migrate.migrate_bridges_state_if_needed(
            repo_root=repo, corvin_home=home, audit_path=audit,
        )
        assert audit.exists(), "audit file must be created"
        lines = audit.read_text().strip().splitlines()
        assert len(lines) >= 2, lines  # intent + complete
        events = [json.loads(L) for L in lines]
        for ev in events:
            assert ev["event_type"] == "bridges.path_migrated", ev
        # The intent event lands BEFORE the complete event
        stages = [ev["details"].get("stage") for ev in events]
        assert stages[0] == "intent", stages
        assert stages[-1] == "complete", stages


def case_audit_chain_integrity() -> None:
    with tempfile.TemporaryDirectory() as tmp_s:
        tmp = Path(tmp_s)
        repo = _fake_repo(tmp)
        home = tmp / "corvin"
        audit = home / "global" / "forge" / "audit.jsonl"
        audit.parent.mkdir(parents=True)
        _clean_env()
        bridges_migrate.migrate_bridges_state_if_needed(
            repo_root=repo, corvin_home=home, audit_path=audit,
        )
        # Chain must verify cleanly across the migration events.
        from forge.security_events import verify_chain
        ok, problems = verify_chain(audit)
        assert ok, problems


def case_legacy_shared_queues() -> None:
    """Older deployments wrote into bridges/shared/inbox/ etc. The
    helper migrates those into bridges/shared/<kind>/."""
    with tempfile.TemporaryDirectory() as tmp_s:
        tmp = Path(tmp_s)
        repo = tmp / "repo"
        bridges = repo / "operator" / "bridges"
        shared_legacy = bridges / "shared"
        for kind in ("inbox", "outbox", "processed"):
            (shared_legacy / kind).mkdir(parents=True)
            (shared_legacy / kind / "msg.json").write_text(
                json.dumps({"kind": kind})
            )
        home = tmp / "corvin"
        _clean_env()
        result = bridges_migrate.migrate_bridges_state_if_needed(
            repo_root=repo, corvin_home=home,
        )
        assert result["status"] == "migrated", result
        for kind in ("inbox", "outbox", "processed"):
            moved = home / "bridges" / "shared" / kind / "msg.json"
            assert moved.exists(), moved
            assert json.loads(moved.read_text())["kind"] == kind


def case_partial_migration_some_channels_absent() -> None:
    """Build a repo where only one of the five channels has state.
    The plan must skip the absent channels silently."""
    with tempfile.TemporaryDirectory() as tmp_s:
        tmp = Path(tmp_s)
        repo = _fake_repo(tmp, channels=("discord",))
        home = tmp / "corvin"
        _clean_env()
        result = bridges_migrate.migrate_bridges_state_if_needed(
            repo_root=repo, corvin_home=home,
        )
        assert result["status"] == "migrated"
        assert result["failed"] == 0
        # All planned moves target discord only
        channels = {m["channel"] for m in result["moves"]}
        assert channels == {"discord"}, channels
        # Other channels' targets never created
        for ch in ("telegram", "slack", "whatsapp", "email"):
            assert not (home / "bridges" / ch).exists()


CASES = [
    case_opt_out_env,
    case_no_legacy_content,
    case_dry_run,
    case_full_migration_same_fs,
    case_idempotent_second_run,
    case_force_runs_again,
    case_audit_event_emitted,
    case_audit_chain_integrity,
    case_legacy_shared_queues,
    case_partial_migration_some_channels_absent,
]


def main() -> int:
    failed = []
    for case in CASES:
        try:
            case()
            print(f"PASS {case.__name__}")
        except Exception as e:
            print(f"FAIL {case.__name__}: {e!r}")
            failed.append(case.__name__)
    if failed:
        print(f"\n{len(failed)} failure(s):", failed)
        return 1
    print(f"\nAll {len(CASES)} cases passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
