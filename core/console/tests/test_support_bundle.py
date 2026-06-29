"""ACO support bundle — one zippable folder of logs/healing, SECRET-SAFE."""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
for _p in (_REPO / "core" / "console", _REPO / "operator" / "forge"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from corvin_console.aco import support_bundle as SB  # type: ignore


def _fake_home(tmp_path: Path) -> Path:
    home = tmp_path / "corvin"
    (home / "logs").mkdir(parents=True)
    (home / "logs" / "corvin.log").write_text("INFO boot\nERROR oops\n", encoding="utf-8")
    (home / "aco_repair.jsonl").write_text('{"event":"repair.applied"}\n', encoding="utf-8")
    (home / "aco" / "diagnoses").mkdir(parents=True)
    (home / "aco" / "diagnoses" / "nightly.log").write_text('{"status":"done"}\n', encoding="utf-8")
    sd = home / "tenants" / "_default" / "sessions" / "web_abc"
    sd.mkdir(parents=True)
    (sd / "chat_debug.jsonl").write_text('{"event":"turn.start"}\n', encoding="utf-8")
    (home / "tenants" / "_default" / "global").mkdir(parents=True)
    (home / "tenants" / "_default" / "global" / "audit.jsonl").write_text(
        '{"event":"os_turn.started"}\n', encoding="utf-8")
    # SECRETS that must NEVER end up in the bundle:
    (home / "global" / "license").mkdir(parents=True)
    (home / "global" / "license" / "license.key").write_text("SECRET-LICENSE", encoding="utf-8")
    (home / "service.env").write_text("OPENAI_API_KEY=sk-SECRET", encoding="utf-8")
    (home / "maintainer.env").write_text("CORVIN_MAINTAINER_CAP=SECRET-TOKEN", encoding="utf-8")
    return home


def test_bundle_collects_logs_and_excludes_secrets(tmp_path):
    home = _fake_home(tmp_path)
    zip_path = SB.create_bundle(home, run_nerve_scan=False, stamp="test")
    assert zip_path.is_file() and zip_path.suffix == ".zip"
    names = zipfile.ZipFile(zip_path).namelist()
    blob = "\n".join(names)
    # logs + metadata ARE included
    assert any("corvin.log" in n for n in names)
    assert any("aco_repair.jsonl" in n for n in names)
    assert any("chat_debug.jsonl" in n for n in names)
    assert any("audit" in n and "audit.jsonl" in n for n in names)
    assert any("system_info.json" in n for n in names)
    assert any("manifest.json" in n for n in names)
    # SECRETS are NOT included — neither by name nor content
    assert "license.key" not in blob
    assert "service.env" not in blob
    assert "maintainer.env" not in blob
    # double-check no secret VALUE leaked into any collected file
    for n in names:
        data = zipfile.ZipFile(zip_path).read(n).decode("utf-8", "replace")
        assert "sk-SECRET" not in data and "SECRET-TOKEN" not in data \
            and "SECRET-LICENSE" not in data


def test_is_secret_guard():
    for bad in ("x.key", "id_rsa", "service.env", "maintainer.key", "vault.json", "x.pem"):
        assert SB._is_secret(bad) is True
    for ok in ("corvin.log", "audit.jsonl", "chat_debug.jsonl", "manifest.json"):
        assert SB._is_secret(ok) is False


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
