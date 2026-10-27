"""Every pairing WRITER of the ``corvin-a2a`` CLI answers the member gate (A2A review, transport package).

Until now only ``create-token`` / ``import-token`` did; ``pair``, ``pair-import``, ``pair-confirm``,
``invite`` and ``accept`` wrote pairing files (and ``a2a.pairing_authorized`` records) on a Free install
whose data path refuses everything. The real CLI runs as a subprocess in a sandboxed ``CORVIN_HOME``
(no licence file there, so the install is Free); nothing may be written. ``set-role`` and
``list-endpoints`` are local configuration and stay ungated.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent
_REPO = _SCRIPTS.parents[2]
_CLI = _SCRIPTS / "corvin_a2a.py"


def _run(tmp: Path, *argv: str, stdin: str = "") -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CORVIN_", "REMOTE_", "FORGE_"))}
    env.update({
        "CORVIN_HOME": str(tmp / "home"), "XDG_CONFIG_HOME": str(tmp / "xdg"),
        "CORVIN_AUDIT_ANCHOR_KEY": str(tmp / "anchor.key"),
        "REMOTE_ORIGINS_DIR": str(tmp / "o"), "REMOTE_ENDPOINTS_DIR": str(tmp / "e"),
        "REMOTE_PENDING_FRIENDSHIPS_DIR": str(tmp / "p"), "REMOTE_INVITES_DIR": str(tmp / "i"),
        "VOICE_AUDIT_PATH": str(tmp / "audit.jsonl"),
        "CORVIN_A2A_URL": "http://10.9.9.9:8775", "CORVIN_A2A_RELAY_URL": "off",
        "PYTHONPATH": os.pathsep.join([str(_REPO), str(_REPO / "corvin_operator" / "bridges" / "shared"),
                                       str(_SCRIPTS)]),
    })
    for d in ("o", "e", "p", "i", "home", "xdg"):
        (tmp / d).mkdir(exist_ok=True)
    (tmp / "anchor.key").write_text("ab" * 32)
    (tmp / "anchor.key").chmod(0o600)
    return subprocess.run([sys.executable, str(_CLI), *argv], env=env, input=stdin, text=True,
                          capture_output=True, timeout=60, cwd=str(tmp))


def _written(tmp: Path) -> list[str]:
    return sorted(str(p.relative_to(tmp)) for d in ("o", "e", "p", "i") for p in (tmp / d).rglob("*") if p.is_file())


WRITERS = {
    "pair": ["pair", "peer1", "http://10.1.2.3:8775/v1/a2a/receive"],
    "pair-offline": ["pair", "peer1", "http://10.1.2.3:8775/v1/a2a/receive", "--offline-pair"],
    "pair-import": ["pair-import", "-"],
    "pair-confirm": ["pair-confirm", "peer1"],
    "invite": ["invite", "--url", "http://10.1.2.3:8775", "--origin-id", "me"],
    "accept": ["accept", "corvin-a2a:inv1:xx.yy"],
}


@pytest.mark.parametrize("name", sorted(WRITERS))
def test_free_install_every_cli_pairing_writer_is_refused(tmp_path, name):
    r = _run(tmp_path, *WRITERS[name], stdin="{}")
    assert r.returncode == 1, (name, r.returncode, r.stdout, r.stderr)
    assert "member seat" in r.stderr and "nothing was written" in r.stderr, r.stderr
    assert _written(tmp_path) == []
    audit = tmp_path / "audit.jsonl"
    assert not audit.exists() or "pairing_authorized" not in audit.read_text()


def test_local_configuration_commands_stay_ungated(tmp_path):
    r = _run(tmp_path, "list-endpoints")
    assert "member seat" not in r.stderr, r.stderr
    assert r.returncode == 0, (r.stdout, r.stderr)
