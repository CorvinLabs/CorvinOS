"""One-shot engine call for capture extraction and decoder stages (PLAN-0973 A9).

The text goes to an engine only after the L34 data-classification gate allows the class
``CONFIDENTIAL`` for that engine (``spawn_gates.check_l34``). A refusal, a gate error or an
unavailable gate means the text is withheld (fail-closed). The call itself runs the Claude CLI
with no tools and one turn, like the L28.2 distiller.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ENGINE_ID = "claude_code"
CLASSIFICATION = "CONFIDENTIAL"
TIMEOUT_S = 90.0


class Withheld(RuntimeError):
    """The L34 gate did not allow sending this text to the engine."""


class EngineUnavailable(RuntimeError):
    pass


def _shared_on_path() -> None:
    shared = Path(__file__).resolve().parents[2] / "corvin_operator" / "bridges" / "shared"
    if str(shared) not in sys.path:
        sys.path.append(str(shared))  # appended: never shadow modules of the host process


def l34_allows(tenant_id: str, text: str, *, engine_id: str = ENGINE_ID) -> bool:
    """True only when the gate positively passes. Any import or gate error is a refusal."""
    try:
        _shared_on_path()
        from spawn_gates import check_l34  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001
        return False
    try:
        return check_l34(engine_id, tenant_id, classification=CLASSIFICATION, prompt=text) is None
    except Exception:  # noqa: BLE001
        return False


def _claude_bin() -> str | None:
    env = os.environ.get("CORVIN_CLAUDE_BIN", "").strip()
    if env and Path(env).exists():
        return env
    try:
        _shared_on_path()
        import helper_model as hm  # type: ignore[import-not-found]
        b = hm.resolve_claude_bin()
        if b:
            return b
    except Exception:  # noqa: BLE001
        pass
    return shutil.which("claude")


def model_for(family: str) -> str | None:
    """The newest model of *family* through the shared resolver (never a hard-coded id); None = CLI default."""
    try:
        _shared_on_path()
        import model_selector  # type: ignore[import-not-found]
        return model_selector.tier_model(family) or None
    except Exception:  # noqa: BLE001
        return None


def _native_env(tenant_id: str, prompt: str) -> dict[str, str]:
    """The environment of the call. A tenant that routes claude_code to another provider (or a Console tier row
    naming one) would send owner text there; the personal feature never does: such a call is withheld."""
    import os  # noqa: PLC0415
    env = dict(os.environ)
    try:
        _shared_on_path()
        from engine_models import apply_claude_code_provider_env  # type: ignore[import-not-found]
        problem = apply_claude_code_provider_env(env, tenant_id, task_input=prompt)
    except ImportError:
        problem = None
    except Exception as exc:  # noqa: BLE001 - an unknown routing state is not a native one
        raise Withheld("the provider routing could not be checked") from exc
    if problem or env.get("ANTHROPIC_BASE_URL") or env.get("CORVIN_CC_PROVIDER"):
        raise Withheld("a provider other than the native endpoint is assigned; personal text is not sent there")
    return env


def one_shot(prompt: str, *, tenant_id: str, timeout_s: float = TIMEOUT_S, model: str | None = None) -> str:
    """Return the engine's raw answer. Raises :class:`Withheld` or :class:`EngineUnavailable`."""
    if not l34_allows(tenant_id, prompt):
        raise Withheld("the data-flow policy does not allow this text for the engine")
    exe = _claude_bin()
    if not exe:
        raise EngineUnavailable("no engine binary")
    env = _native_env(tenant_id, prompt)
    # The one shared neutraliser every claude-CLI payload goes through (ADR-0648): owner text can carry an "@path",
    # and the CLI reads that file into the request. The guard rewrites it; an unavailable guard withholds the call.
    try:
        _shared_on_path()
        from prompt_guard import guard_prompt_head  # type: ignore[import-not-found]
        prompt = guard_prompt_head(prompt)
    except Exception as exc:  # noqa: BLE001 - fail-closed: no unguarded payload reaches the CLI
        raise Withheld("the prompt guard is not available") from exc
    # No session persistence: the prompt carries personal text and must not land in a CLI transcript.
    # An empty temporary cwd: no repository CLAUDE.md or hooks are loaded into this call.
    args = [exe, "-p", "--max-turns", "1", "--tools", "", "--no-session-persistence"]
    if model:
        args += ["--model", model]
    import tempfile  # noqa: PLC0415
    try:
        with tempfile.TemporaryDirectory(prefix="pkb-engine-") as cwd:
            r = subprocess.run(args, input=prompt, capture_output=True, text=True, timeout=timeout_s,
                               encoding="utf-8", errors="replace", check=False, cwd=cwd, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EngineUnavailable(type(exc).__name__) from exc
    if r.returncode != 0 or not r.stdout.strip():
        raise EngineUnavailable(f"engine exit {r.returncode}")
    return r.stdout


def extract_json(text: str) -> dict | None:
    import json

    if not text:
        return None
    start = text.find("{")
    if start < 0:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None
