"""uninstall.sh, run for real as a subprocess against a sandboxed HOME.

systemctl / ps / uv / claude / launchctl / docker are shimmed on PATH: unit
names and the process table are global to the machine, so an unshimmed run
would stop the developer's live CorvinOS. Everything else (tar, rm, sed, the
script's own control flow) is the real thing.
"""
from __future__ import annotations

import os
import stat
import subprocess
import tarfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "uninstall.sh"

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX uninstaller")


def _shim(bindir: Path, name: str, body: str) -> None:
    p = bindir / name
    p.write_text("#!/bin/sh\n" + body + "\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)


@pytest.fixture()
def sandbox(tmp_path: Path):
    home = tmp_path / "home"
    fake = tmp_path / "fakebin"
    fake.mkdir()
    calls = tmp_path / "calls.log"
    tool_dir = home / ".local/share/uv/tools"
    env_dir = tool_dir / "corvinos"
    dev = home / "projects/CorvinOS"
    managed = home / ".local/share/corvinos/src"

    # A dev checkout the receipt points at, with generated state inside.
    (dev / ".corvin/tenants/_default/global/forge").mkdir(parents=True)
    (dev / "pyproject.toml").write_text("[project]\nname='corvinos'\n")
    (dev / ".corvin_repo").write_text("")
    (dev / ".corvin/tenants/_default/global/forge/audit.jsonl").write_text('{"e":1}\n')
    # Installer-managed tree (marker) — must be deleted.
    managed.mkdir(parents=True)
    (managed / "pyproject.toml").write_text("")
    (managed / ".corvin-managed").write_text("")
    # uv tool env + receipt + shims.
    (env_dir / "bin").mkdir(parents=True)
    (env_dir / "uv-receipt.toml").write_text(
        f'[tool]\nrequirements = [{{ name = "corvinos", editable = "{dev}" }}]\n')
    bindir = home / ".local/bin"
    bindir.mkdir(parents=True)
    (bindir / "corvinos-serve").symlink_to(env_dir / "bin/corvinos-serve")
    (bindir / "corvin-install").write_text(f"#!{env_dir}/bin/python\n# corvinos\n")
    (bindir / "corvin-mytool").write_text("#!/bin/sh\necho mine\n")  # not ours
    # Data + secrets.
    (home / ".corvin/node/bin").mkdir(parents=True)
    (home / ".corvin/node/bin/node").write_text("x" * 1000)
    (home / ".corvin/instance_id").write_text("abc")
    (home / ".config/corvin-voice/piper-models").mkdir(parents=True)
    (home / ".config/corvin-voice/service.env").write_text("OPENAI_API_KEY=sk-test\n")
    units = home / ".config/systemd/user"
    (units / "default.target.wants").mkdir(parents=True)
    for u in ("corvin-webui.service", "corvin-watchdog.service"):
        (units / u).write_text("[Service]\n")
        (units / "default.target.wants" / u).symlink_to(units / u)
    (units / "other.service").write_text("[Service]\n")

    for name in ("systemctl", "launchctl", "docker", "claude"):
        _shim(fake, name, f'echo "{name} $*" >> "{calls}"; exit 0')
    _shim(fake, "ps", "exit 0")  # empty process table
    _shim(fake, "uv", f'echo "uv $*" >> "{calls}"\n'
                      f'[ "$1 $2" = "tool dir" ] && echo "{tool_dir}" && exit 0\n'
                      f'[ "$1 $2" = "tool uninstall" ] && rm -rf "{env_dir}"\nexit 0')
    env = {
        "HOME": str(home), "PATH": f"{fake}:/usr/bin:/bin",
        "CORVIN_CONSOLE_PORT": "59999", "LANG": "C",
    }
    return {"home": home, "env": env, "calls": calls, "dev": dev,
            "managed": managed, "env_dir": env_dir, "bindir": bindir, "units": units}


def _run(sb, *args, stdin=subprocess.DEVNULL):
    return subprocess.run(["bash", str(SCRIPT), *args], env=sb["env"], cwd=sb["home"],
                          capture_output=True, text=True, stdin=stdin, timeout=120)


def test_verify_only_reports_an_installed_system(sandbox):
    r = _run(sandbox, "--verify-only")
    assert r.returncode == 1, r.stdout
    assert "corvin-webui.service" in r.stdout
    assert "uv tool env" in r.stdout


def test_refuses_unattended_without_yes(sandbox):
    r = _run(sandbox)
    assert r.returncode == 2
    assert (sandbox["home"] / ".corvin").exists()


def test_dry_run_changes_nothing(sandbox):
    r = _run(sandbox, "--dry-run")
    assert r.returncode == 0, r.stdout + r.stderr
    assert (sandbox["home"] / ".corvin").exists()
    assert (sandbox["units"] / "corvin-webui.service").exists()
    assert not list(sandbox["home"].glob("corvin-backup-*.tar.gz"))


def test_full_uninstall_backs_up_removes_and_verifies(sandbox):
    home = sandbox["home"]
    r = _run(sandbox, "--yes")
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "removed completely" in out

    # 1. backup exists, is private, holds secrets + the audit chain, not runtimes
    backups = list(home.glob("corvin-backup-*.tar.gz"))
    assert len(backups) == 1
    assert stat.S_IMODE(backups[0].stat().st_mode) == 0o600
    names = tarfile.open(backups[0]).getnames()
    assert any(n.endswith(".config/corvin-voice/service.env") for n in names)
    assert any(n.endswith("forge/audit.jsonl") for n in names)
    assert any(n.endswith("corvin-webui.service") for n in names)
    assert not any("/.corvin/node/" in n for n in names)

    # 2. watchdog stopped BEFORE the console
    calls = sandbox["calls"].read_text()
    assert calls.index("stop corvin-watchdog.service") < \
        calls.index("stop corvin-webui.service")
    assert "plugin uninstall voice@corvin-voice-local" in calls

    # 3. everything of ours is gone, foreign files untouched
    assert not (home / ".corvin").exists()
    assert not (home / ".config/corvin-voice").exists()
    assert not (sandbox["dev"] / ".corvin").exists()
    assert not sandbox["env_dir"].exists()
    assert not (sandbox["units"] / "corvin-webui.service").exists()
    assert not (sandbox["units"] / "default.target.wants/corvin-webui.service").is_symlink()
    assert (sandbox["units"] / "other.service").exists()
    assert not (sandbox["bindir"] / "corvinos-serve").is_symlink()
    assert not (sandbox["bindir"] / "corvin-install").exists()
    assert (sandbox["bindir"] / "corvin-mytool").exists()
    assert not sandbox["managed"].exists()
    # the developer's checkout itself is never deleted
    assert (sandbox["dev"] / "pyproject.toml").exists()

    # 4. a second run is a clean no-op and the scan agrees
    assert _run(sandbox, "--verify-only").returncode == 0
    assert _run(sandbox, "--yes").returncode == 0


def test_never_touches_a_checkout_no_install_points_at(sandbox, tmp_path):
    """Regression (2026-09-24): the script used to add the directory it sits
    in as an install source, so running the repo's copy in a sandbox deleted
    the developer's live <repo>/.corvin. An unrelated checkout — here one
    outside the sandbox HOME, like the real repo — must survive untouched."""
    outside = tmp_path / "elsewhere/CorvinOS"
    (outside / ".corvin").mkdir(parents=True)
    (outside / ".corvin_repo").write_text("")
    (outside / "pyproject.toml").write_text("")
    script = outside / "uninstall.sh"
    script.write_text(SCRIPT.read_text())
    r = subprocess.run(["bash", str(script), "--yes"], env=sandbox["env"],
                       cwd=outside, capture_output=True, text=True, timeout=120,
                       stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (outside / ".corvin").is_dir()
    assert str(outside) not in r.stdout


def test_refuses_data_outside_home_unless_explicit(sandbox, tmp_path):
    ext = tmp_path / "external/.corvin"
    (ext.parent / ".corvin").mkdir(parents=True)
    (ext.parent / "pyproject.toml").write_text("")
    receipt = sandbox["env_dir"] / "uv-receipt.toml"
    receipt.write_text(f'[tool]\nrequirements = [{{ name = "corvinos", editable = "{ext.parent}" }}]\n')
    r = _run(sandbox, "--yes")
    assert r.returncode == 0, r.stdout + r.stderr
    assert ext.is_dir(), "a receipt-named tree outside HOME must not be deleted implicitly"


def test_failed_backup_removes_nothing_and_restarts_services(sandbox, tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")  # a FILE where the backup dir should be
    r = _run(sandbox, "--yes", "--backup-dir", str(blocker / "sub"))
    assert r.returncode == 1
    assert "nothing was removed" in r.stderr
    home = sandbox["home"]
    assert (home / ".corvin/instance_id").exists()
    assert (sandbox["units"] / "corvin-webui.service").exists()
    assert sandbox["env_dir"].exists()
    calls = sandbox["calls"].read_text()
    assert "--user start corvin-webui.service" in calls
    assert "disable" not in calls


def _docker_shim(sandbox, *, cp_ok: bool):
    """A docker CLI with one labelled container and one labelled volume.
    State lives in files so `rm -f` really removes the container."""
    fake = Path(sandbox["env"]["PATH"].split(":")[0])
    state = fake.parent / "docker-state"
    state.mkdir(exist_ok=True)
    (state / "containers").write_text("c0ffee\n")
    calls = sandbox["calls"]
    _shim(fake, "docker", f'''echo "docker $*" >> "{calls}"
S="{state}"
case "$1" in
  info) exit 0 ;;
  ps) cat "$S/containers" 2>/dev/null; exit 0 ;;
  inspect) echo "/corvin-console"; exit 0 ;;
  cp) {"mkdir -p \"$3\"; echo chain > \"$3/audit.jsonl\"; exit 0" if cp_ok else "exit 1"} ;;
  rm) : > "$S/containers"; exit 0 ;;
  volume) [ "$2" = ls ] && echo corvin-data; exit 0 ;;
esac
exit 0''')


def test_docker_data_is_exported_before_volumes_are_destroyed(sandbox):
    _docker_shim(sandbox, cp_ok=True)
    r = _run(sandbox, "--yes")
    assert r.returncode == 0, r.stdout + r.stderr
    calls = sandbox["calls"].read_text()
    assert calls.index("docker cp c0ffee:/root/.corvin") < calls.index("docker rm -f c0ffee")
    assert calls.index("docker rm -f c0ffee") < calls.index("docker volume rm corvin-data")
    exported = list(sandbox["home"].glob("corvin-backup-docker-*/corvin-console/audit.jsonl"))
    assert exported, "container audit chain was not exported"


def test_failed_docker_export_keeps_the_volumes(sandbox):
    _docker_shim(sandbox, cp_ok=False)
    r = _run(sandbox, "--yes")
    assert "Docker volumes KEPT" in r.stdout
    assert "docker volume rm" not in sandbox["calls"].read_text()


def test_keep_data_removes_software_only(sandbox):
    r = _run(sandbox, "--yes", "--keep-data")
    assert r.returncode == 0, r.stdout + r.stderr
    assert (sandbox["home"] / ".config/corvin-voice/service.env").exists()
    assert not sandbox["env_dir"].exists()
    assert not list(sandbox["home"].glob("corvin-backup-*.tar.gz"))
