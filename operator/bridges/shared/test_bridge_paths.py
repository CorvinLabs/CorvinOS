"""ADR-0008 Phase 8.1 per-subtask E2E for bridge_runtime_dir()."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths  # noqa: E402


def _clean_env():
    for key in list(os.environ.keys()):
        if key.startswith("CORVIN_BRIDGE") or key in (
            "CORVIN_HOME", "CORVIN_BRIDGES_HOME", "CORVIN_HOME",
        ):
            os.environ.pop(key, None)


def case_corvin_home_root() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _clean_env()
        os.environ["CORVIN_HOME"] = tmp
        assert paths.bridges_home() == Path(tmp) / "bridges"
        assert paths.bridge_channel_dir("discord") == Path(tmp) / "bridges" / "discord"


def case_kind_resolution() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _clean_env()
        os.environ["CORVIN_HOME"] = tmp
        for kind in ("inbox", "outbox", "processed", "attachments", "auth", "log"):
            assert paths.bridge_runtime_dir("telegram", kind) == \
                Path(tmp) / "bridges" / "telegram" / kind, kind
        # settings + root both return the channel dir
        for alias in ("settings", "root"):
            assert paths.bridge_runtime_dir("telegram", alias) == \
                Path(tmp) / "bridges" / "telegram", alias


def case_convenience_paths() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _clean_env()
        os.environ["CORVIN_HOME"] = tmp
        assert paths.bridge_settings_path("discord") == \
            Path(tmp) / "bridges" / "discord" / "settings.json"
        assert paths.bridge_log_path("email") == \
            Path(tmp) / "bridges" / "email" / "log" / "voice.log"


def case_root_env_override() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _clean_env()
        os.environ["CORVIN_HOME"] = "/should/not/be/used"
        os.environ["CORVIN_BRIDGES_HOME"] = tmp
        assert paths.bridge_runtime_dir("slack", "inbox") == \
            Path(tmp) / "slack" / "inbox"


def case_per_leaf_env_override() -> None:
    _clean_env()
    os.environ["CORVIN_HOME"] = "/should/not/be/used"
    os.environ["CORVIN_BRIDGE_DISCORD_INBOX"] = "/sandbox/inbox-override"
    assert paths.bridge_runtime_dir("discord", "inbox") == \
        Path("/sandbox/inbox-override")
    # other channel/kind not affected by this override
    assert paths.bridge_runtime_dir("discord", "outbox") != \
        Path("/sandbox/inbox-override")


def case_invalid_channel_rejected() -> None:
    _clean_env()
    for bad in ("Discord", "bad/channel", "", "a" * 33, "9digits", "with space"):
        try:
            paths.bridge_runtime_dir(bad, "inbox")
        except ValueError:
            continue
        raise AssertionError(f"should have rejected channel {bad!r}")


def case_invalid_kind_rejected() -> None:
    _clean_env()
    for bad in ("Bogus", "INBOX", "messages", "", "settings.json"):
        try:
            paths.bridge_runtime_dir("discord", bad)
        except ValueError:
            continue
        raise AssertionError(f"should have rejected kind {bad!r}")


def case_identity_only_no_fs_side_effects() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _clean_env()
        os.environ["CORVIN_HOME"] = tmp
        # Walk every channel × every queue kind. None of these should
        # have created directories — the migration helper is the sole
        # mkdir owner.
        for channel in ("telegram", "discord", "slack", "whatsapp", "email"):
            for kind in ("inbox", "outbox", "processed", "attachments", "auth", "log"):
                p = paths.bridge_runtime_dir(channel, kind)
                assert not p.exists(), f"resolver MUST NOT create {p}"
        assert not (Path(tmp) / "bridges").exists(), \
            "bridges_home MUST NOT be created"


def case_legacy_path_points_at_repo() -> None:
    _clean_env()
    legacy = paths.legacy_bridge_runtime_dir("email", "attachments")
    assert legacy is not None
    assert legacy.is_absolute()
    # the legacy path is the in-repo path
    assert "operator/bridges/email/attachments" in str(legacy)


def case_known_channels_all_accepted() -> None:
    _clean_env()
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["CORVIN_HOME"] = tmp
        for ch in ("telegram", "discord", "slack", "whatsapp", "email", "shared"):
            assert paths.bridge_runtime_dir(ch, "inbox").parent.name == ch


CASES = [
    case_corvin_home_root,
    case_kind_resolution,
    case_convenience_paths,
    case_root_env_override,
    case_per_leaf_env_override,
    case_invalid_channel_rejected,
    case_invalid_kind_rejected,
    case_identity_only_no_fs_side_effects,
    case_legacy_path_points_at_repo,
    case_known_channels_all_accepted,
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
