#!/usr/bin/env python3
"""Every Python reader of a bridge's settings reads the file its DAEMON reads.

ADR-0008 §8.3 moved bridge settings to ``<corvin_home>/bridges/<channel>/``;
the daemons followed, four Python readers did not. Measured 2026-09-27 on the
maintainer install: the canonical Discord file held the whitelist and the
chat profiles, the legacy in-repo file only ``operator_name``. Consequences:

  * adapter: the read-time whitelist re-check saw no whitelist → fail-open,
    "no whitelist configured" logged on every message; chat_profiles ignored;
  * roles.is_intrinsic_owner: empty whitelist = DEV-mode → EVERY sender owner;
  * disclosure / phase3_cli: same file, same blindness.

This test reproduces that layout — canonical file with a whitelist, legacy
file without — WITHOUT the ADAPTER_BRIDGES_DIR override, and requires every
reader to see the canonical file. A static sweep then forbids composing a
bridge settings path by hand anywhere else in bridges/shared (positive
control first: the sweep must reach the resolver itself).

Run: python3 test_bridge_settings_ssot.py
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

OWNER, STRANGER = "owner-uid-1", "stranger-uid-2"
CHANNEL = "ssotprobe"   # a channel no real install has, so the legacy file is ours to create


def _fresh(mod: str):
    sys.modules.pop(mod, None)
    return __import__(mod)


def test_readers_follow_the_daemon_file() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="bridge-settings-ssot-"))
    legacy = ROOT.parent / CHANNEL / "settings.json"
    saved = {k: os.environ.get(k) for k in ("CORVIN_HOME", "ADAPTER_BRIDGES_DIR")}
    try:
        os.environ["CORVIN_HOME"] = str(tmp / "home")
        os.environ.pop("ADAPTER_BRIDGES_DIR", None)
        canonical = tmp / "home" / "bridges" / CHANNEL / "settings.json"
        canonical.parent.mkdir(parents=True)
        canonical.write_text(json.dumps({"whitelist": [OWNER],
                                         "chat_profiles": {"c1": {"persona": "coder"}}}))
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text(json.dumps({"operator_name": "legacy only"}))

        paths = _fresh("paths")
        assert paths.resolve_bridge_settings_file(CHANNEL) == canonical
        assert paths.resolve_bridge_settings_file("../etc") is None

        roles = _fresh("roles")
        assert roles.is_intrinsic_owner(CHANNEL, OWNER) is True
        assert roles.is_intrinsic_owner(CHANNEL, STRANGER) is False, "stranger classified owner"

        disclosure = _fresh("disclosure")
        assert disclosure._channel_settings_path(CHANNEL) == canonical

        # the adapter's read-time whitelist re-check and chat profile
        import test_adapter_stream_idle as h  # noqa: PLC0415 — the shared adapter loader
        adapter = h._fresh_adapter()
        assert adapter._load_channel_settings(CHANNEL).get("whitelist") == [OWNER]
        ok, why = adapter._inbox_sender_authorized(CHANNEL, STRANGER, "c1")
        assert not ok and why != "no-whitelist", (ok, why)
        assert adapter._inbox_sender_authorized(CHANNEL, OWNER, "c1")[0] is True
        assert adapter._sender_is_operator(CHANNEL, OWNER) is True

        # without a canonical file the legacy one is still honoured (daemon parity)
        canonical.unlink()
        assert paths.resolve_bridge_settings_file(CHANNEL) == legacy
        print("PASS: adapter, roles and disclosure read the daemon's settings file")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(legacy.parent, ignore_errors=True)
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# A hand-composed bridge settings path: `<x>.parent / channel / "settings.json"`
# and its spellings. The resolver in paths.py is the one allowed composition.
_HAND_BUILT = re.compile(r'(?:parent|HERE|ROOT|here|base)[\w.]*\s*/\s*(?:channel|ch|_ch)\s*/\s*["\']settings\.json["\']')


def test_no_reader_composes_the_path_by_hand() -> None:
    files = [p for p in ROOT.glob("*.py") if not p.name.startswith("test_")]
    # positive control: the sweep reaches real modules, the resolver included
    assert len(files) > 30, f"sweep reached only {len(files)} files"
    assert "def resolve_bridge_settings_file" in (ROOT / "paths.py").read_text()
    offenders = []
    for p in files:
        for n, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if _HAND_BUILT.search(line) and not line.lstrip().startswith("#"):
                offenders.append(f"{p.name}:{n}: {line.strip()}")
    assert not offenders, "compose via paths.resolve_bridge_settings_file:\n" + "\n".join(offenders)
    print(f"PASS: no hand-built bridge settings path in {len(files)} modules")


_MIRRORS = [ROOT / "paths.py", ROOT.parents[1] / "forge" / "forge" / "paths.py",
            ROOT.parents[1] / "cowork" / "lib" / "paths.py"]


def _resolver_source(p: Path) -> str:
    src = p.read_text(encoding="utf-8")
    start = src.index("def resolve_bridge_settings_file(")
    body = src[start:src.index("\ndef ", start + 1)]
    return re.sub(r"Mirror of bridges/shared/paths.py — guard:", "Guard:", body)


def test_every_paths_mirror_carries_the_same_resolver() -> None:
    """`import paths` means a different file per process: bridges/shared in the
    adapter, forge/forge in the console (measured 2026-09-27 — the console's
    chat-settings route answered 500 on AttributeError until the forge mirror
    had the resolver). All mirrors must define it, identically."""
    sources = {p: _resolver_source(p) for p in _MIRRORS}
    first = sources[_MIRRORS[0]]
    for p, s in sources.items():
        assert s == first, f"{p} resolver drifted from bridges/shared/paths.py"
    print(f"PASS: resolver identical in {len(sources)} paths mirrors")


if __name__ == "__main__":
    test_readers_follow_the_daemon_file()
    test_no_reader_composes_the_path_by_hand()
    test_every_paths_mirror_carries_the_same_resolver()
