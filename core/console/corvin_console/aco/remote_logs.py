"""ACO remote-log ingestion (ADR-0178 follow-up).

Pulls the logs/healing artifacts of OTHER CorvinOS instances (e.g. the Hetzner
server) into the local home so the nightly analysis + support bundle cover them
too. Read-only mirror under ``<corvin_home>/aco/remote/<name>/`` — the live
instance is never modified.

SECRET-SAFE: the rsync filter pulls ONLY log/healing files and EXCLUDES secrets
(*.key/*.pem/*.env/secret/vault/id_rsa) before any include, so a key on the remote
can never be mirrored locally (and thus never end up in a support bundle).

Remotes are configured in ``<corvin_home>/aco/remotes.json``:
  [{"name": "hetzner", "ssh": "root@178.105.220.226",
    "remote_home": "/opt/corvin/.corvin"}]
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Callable

# rsync filter — order matters (first match wins). Secrets excluded FIRST.
_FILTER = [
    "--exclude=*.key", "--exclude=*.pem", "--exclude=*.env", "--exclude=*secret*",
    "--exclude=*vault*", "--exclude=id_rsa*", "--exclude=*.tmp", "--exclude=*token*",
    "--exclude=identity_registry.json",
    "--include=*/",                       # descend into every dir
    "--include=**/corvin.log*",
    "--include=**/audit.jsonl",
    "--include=**/chat_debug.jsonl*",
    "--include=**/aco_repair.jsonl",
    "--include=aco/diagnoses/**",
    "--exclude=*",                        # nothing else
]
_SSH = "ssh -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new"


def remotes_config_path(corvin_home: str | Path) -> Path:
    return Path(corvin_home) / "aco" / "remotes.json"


def load_remotes(corvin_home: str | Path) -> list[dict]:
    p = remotes_config_path(corvin_home)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def save_remotes(corvin_home: str | Path, remotes: list[dict]) -> Path:
    p = remotes_config_path(corvin_home)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(remotes, indent=2, ensure_ascii=False), encoding="utf-8")
    return p


def mirror_dir(corvin_home: str | Path, name: str) -> Path:
    return Path(corvin_home) / "aco" / "remote" / name


def pull_remote(corvin_home: str | Path, remote: dict, *,
                runner: Callable[[list[str]], tuple[int, str]] | None = None) -> dict[str, Any]:
    """rsync one remote's logs into the local mirror. Best-effort; returns a summary
    dict (never raises). ``runner`` is injectable for tests."""
    name = str(remote.get("name") or "remote")
    ssh = remote.get("ssh")
    rhome = str(remote.get("remote_home") or ".corvin")
    if not ssh:
        return {"name": name, "ok": False, "error": "no ssh target"}
    dst = mirror_dir(corvin_home, name)
    dst.mkdir(parents=True, exist_ok=True)
    src = f"{ssh}:{rhome.rstrip('/')}/"
    cmd = ["rsync", "-az", "--prune-empty-dirs", "-e", _SSH, *_FILTER, src, str(dst) + "/"]

    def _run(c: list[str]) -> tuple[int, str]:
        try:
            p = subprocess.run(c, capture_output=True, text=True, timeout=300)
            return p.returncode, (p.stdout + p.stderr)[-500:]
        except Exception as exc:  # noqa: BLE001
            return 1, str(exc)[:300]

    rc, out = (runner or _run)(cmd)
    return {"name": name, "ok": rc == 0, "rc": rc, "dst": str(dst), "detail": out[-200:]}


def pull_all(corvin_home: str | Path, *,
             runner: Callable[[list[str]], tuple[int, str]] | None = None) -> list[dict]:
    """Pull every configured remote. Returns one summary per remote."""
    return [pull_remote(corvin_home, r, runner=runner) for r in load_remotes(corvin_home)]
