"""`corvin config set features.<flag_id> <bool>` — the Console-independent off-ramp.

Why this surface exists at all: ``headless_api_mode`` is SELF-LOCKING. When it
is on, ``/console/`` is not mounted, so Settings → Features — the panel CLAUDE.md
points at for every flag — is gone. Without a second write path the flag is a
one-way door and the only recovery is hand-editing ``features.json``.

These tests pin the contract that recovery depends on:

  * the command parses (``config set`` takes a dotted key, not a fixed choice);
  * it writes the SAME per-tenant overlay the Settings route writes, at the same
    highest precedence, so it also beats ``spec.features.*`` in tenant.corvin.yaml;
  * a non-boolean value is refused rather than stored as a truthy string;
  * an unknown flag id is refused (the registry stays the only vocabulary);
  * turning a self-locking flag ON prints the exact command that turns it back off.

CORVIN_HOME is pinned to a tmp dir in every test. Without that this suite would
write into the developer's live install — and the flag under test is the one
that removes their web UI.
"""
from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[3]
_OPERATOR = _REPO / "operator"
_CONSOLE = _REPO / "core" / "console"
_LAUNCHER = _REPO / "ops" / "launcher"

for _p in (str(_LAUNCHER), str(_CONSOLE), str(_OPERATOR),
           str(_OPERATOR / "forge"), str(_OPERATOR / "license")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

pytest.importorskip("corvin_console.feature_flags",
                    reason="console extras not installed")

from corvin import cli  # noqa: E402, I001  — must follow the sys.path + importorskip above


FLAG = "headless_api_mode"


def _reset_console_modules() -> None:
    for key in list(sys.modules):
        head = key.split(".", 1)[0]
        if head in ("corvin_console", "forge"):
            del sys.modules[key]


@pytest.fixture
def corvin_home(tmp_path, monkeypatch):
    """A throwaway CORVIN_HOME with the tenant skeleton the overlay needs."""
    home = tmp_path / "corvin_home"
    (home / "tenants" / "_default" / "global").mkdir(parents=True)
    monkeypatch.setenv("CORVIN_HOME", str(home))
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    _reset_console_modules()
    yield home
    _reset_console_modules()


def _overlay(home: Path) -> dict:
    p = home / "tenants" / "_default" / "global" / "features.json"
    if not p.is_file():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def _run(key: str, value: str) -> tuple[int, str]:
    """Drive the command through the real parser, then the real dispatch."""
    parser = cli._build_parser()
    args = parser.parse_args(["config", "set", key, value])
    assert args.command == "config"
    assert args.config_cmd == "set"
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli.cmd_config_set(args)
    return rc, buf.getvalue()


# ── Parsing ───────────────────────────────────────────────────────────────

def test_parser_accepts_a_dotted_features_key():
    """`config set` must not re-acquire a `choices=` list.

    It had one once; it rejected `telemetry.*` before the handler ever ran and
    broke the documented opt-out. A `features.*` key would break the same way.
    """
    args = cli._build_parser().parse_args(
        ["config", "set", f"features.{FLAG}", "false"])
    assert args.key == f"features.{FLAG}"
    assert args.value == "false"


def test_recovery_command_is_spelled_the_way_the_registry_advertises_it():
    """The string the UI shows and the string the CLI accepts are one string."""
    from corvin_console import feature_flags

    command = feature_flags.recovery_command(FLAG)
    assert command == f"corvin config set features.{FLAG} false"
    # ...and that exact command parses.
    argv = command.split()[1:]          # drop the "corvin" prog name
    args = cli._build_parser().parse_args(argv)
    assert args.key == f"features.{FLAG}"


# ── Writing ───────────────────────────────────────────────────────────────

def test_turning_the_flag_off_writes_the_overlay(corvin_home):
    rc, out = _run(f"features.{FLAG}", "false")
    assert rc == 0, out
    assert _overlay(corvin_home)["flags"][FLAG] is False


def test_off_is_readable_by_the_same_resolver_the_app_uses(corvin_home):
    """A write nobody reads is not a recovery path."""
    from corvin_console import feature_flags

    _run(f"features.{FLAG}", "true")
    assert feature_flags.is_enabled(FLAG, "_default") is True
    _run(f"features.{FLAG}", "false")
    assert feature_flags.is_enabled(FLAG, "_default") is False


def test_off_overrides_a_tenant_yaml_that_says_on(corvin_home):
    """The lock-out case: the operator put it in tenant.corvin.yaml.

    The overlay is the highest-precedence layer, so the CLI can undo a YAML
    setting without the operator editing YAML — which is the whole point.
    """
    from corvin_console import feature_flags

    cfg = corvin_home / "tenants" / "_default" / "global" / "tenant.corvin.yaml"
    cfg.write_text(f"spec:\n  features:\n    {FLAG}: true\n", encoding="utf-8")
    assert feature_flags.is_enabled(FLAG, "_default") is True

    rc, out = _run(f"features.{FLAG}", "false")
    assert rc == 0, out
    assert feature_flags.is_enabled(FLAG, "_default") is False


@pytest.mark.parametrize("word", ["false", "FALSE", "no", "0", "off", "disabled"])
def test_falsey_spellings_all_mean_off(corvin_home, word):
    rc, _ = _run(f"features.{FLAG}", word)
    assert rc == 0
    assert _overlay(corvin_home)["flags"][FLAG] is False


@pytest.mark.parametrize("word", ["true", "TRUE", "yes", "1", "on", "enabled"])
def test_truthy_spellings_all_mean_on(corvin_home, word):
    rc, _ = _run(f"features.{FLAG}", word)
    assert rc == 0
    assert _overlay(corvin_home)["flags"][FLAG] is True


def test_toggling_one_flag_leaves_the_others_alone(corvin_home):
    _run("features.browser_automation", "true")
    _run(f"features.{FLAG}", "false")
    flags = _overlay(corvin_home)["flags"]
    assert flags["browser_automation"] is True
    assert flags[FLAG] is False


# ── Refusals ──────────────────────────────────────────────────────────────

def test_a_typo_value_is_refused_not_stored(corvin_home):
    """`flase` stored as a truthy string would silently leave the flag ON."""
    rc, out = _run(f"features.{FLAG}", "flase")
    assert rc == 1
    assert "invalid value" in out
    assert _overlay(corvin_home) == {}


def test_unknown_flag_is_refused_and_the_registry_is_listed(corvin_home):
    rc, out = _run("features.house_rules_off", "true")
    assert rc == 1
    assert "unknown feature flag" in out
    assert FLAG in out            # the listing is the discovery path
    assert _overlay(corvin_home) == {}


def test_empty_flag_id_is_refused(corvin_home):
    rc, out = _run("features.", "false")
    assert rc == 1
    assert "invalid feature key" in out


def test_no_env_var_kill_flag_is_introduced(corvin_home, monkeypatch):
    """CLAUDE.md forbids an env override for the engine/feature choice.

    Setting a plausible env var must not move the flag; only the file write does.
    """
    from corvin_console import feature_flags

    monkeypatch.setenv("CORVIN_HEADLESS_API_MODE", "1")
    monkeypatch.setenv("CORVIN_FEATURES_HEADLESS_API_MODE", "true")
    assert feature_flags.is_enabled(FLAG, "_default") is False


# ── Operator guidance ─────────────────────────────────────────────────────

def test_turning_a_self_locking_flag_on_prints_the_way_back(corvin_home):
    """The one moment the operator can still be told: before the door shuts."""
    rc, out = _run(f"features.{FLAG}", "true")
    assert rc == 0
    assert f"corvin config set features.{FLAG} false" in out
    assert "Console" in out


def test_a_restart_is_named_because_the_ui_mount_is_decided_at_boot(corvin_home):
    """`mount_static()` runs once at app creation.

    Claiming "no restart needed" here would be a lie: the SPA is not mounted
    into a running headless process by writing a file.
    """
    _, out = _run(f"features.{FLAG}", "false")
    assert "Restart" in out


def test_an_ordinary_flag_gets_no_lock_out_warning(corvin_home):
    _, out = _run("features.browser_automation", "true")
    assert "removes the Console" not in out
    assert "only way back" not in out


def test_dispatch_is_reachable_from_main(corvin_home, monkeypatch):
    """Guards the wiring, not the handler: `main()` must route `config set` here.

    A handler nothing calls is the failure mode this repo has hit repeatedly.
    """
    monkeypatch.setattr(sys, "argv",
                        ["corvin", "config", "set", f"features.{FLAG}", "false"])
    with redirect_stdout(io.StringIO()):
        with pytest.raises(SystemExit) as exc:
            cli.main()
    assert exc.value.code == 0
    assert _overlay(corvin_home)["flags"][FLAG] is False
