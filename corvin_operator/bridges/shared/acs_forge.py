"""acs_forge.py - runtime forging for ACS runs (tools and skills created mid-run).

The ACS manager may add a ``forge`` object to a DELEGATE decision::

    "forge": {
      "tools":  [{"name": "dynamic.x", "description": "...", "input_schema": {...},
                  "impl": "<python>", "test_input": {...}, "expect": {...}}],
      "skills": [{"name": "my_method", "description": "...", "body": "<prose>"}]
    }

Nothing a manager writes is trusted. A tool must pass, in this order:

  1. shape and size caps, a ``namespace.name`` identifier inside the workflow's
     ``dynamic_tools.allowed_namespaces``, the Forge policy's ``name_allowed``;
  2. a mandatory working ``bwrap`` sandbox (no sandbox -> no tool; rlimits alone are not enough);
  3. the Forge static import check with an extended deny list (network, processes, FFI, dynamic import);
  4. ``Registry.create`` in a RUN-LOCAL registry (``<run_dir>/dynamic_tools_forge``) - licence gate included
     (``forge.create`` is member-only), so a free tenant is refused fail-closed;
  5. a SELF-TEST in the sandbox with the manager's ``test_input``: it must exit 0, return a JSON object that is
     not the echo template, and contain every key/value of ``expect`` (when given).

Only after step 5 is the tool approved (bound to its sha256 in the permission store) and described to the
workers. The approval is automated, not human: the sandbox, the deny list and the self-test are the controls,
and every outcome is audited (``acs.tool_forged`` / ``acs.tool_forge_rejected``). A tool is never executed
in-process and never outside the Forge runner.

Skills are text. They are linted by the SkillForge registry (``SkillRegistry.create``) and persisted at session
scope when that works; the run-local copy is what workers receive (the manager wrote it in this very run, so it
is not subject to the grade gate that governs OTHER skills - ADR-2278).

Design record: ADR-2278 amendment and the E-036 epic in Corvin-Knowledge.
"""
from __future__ import annotations

import json
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

_OPERATOR = Path(__file__).resolve().parents[2]          # .../corvin_operator
_REPO = _OPERATOR.parent

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}\.[a-z][a-z0-9_]{0,47}$")
SKILL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,47}$")

MAX_TOOLS_HARD = 8
MAX_SKILLS_HARD = 5
MAX_PER_ITERATION = 3
MAX_IMPL_BYTES = 16_000
MAX_SCHEMA_BYTES = 4_000
MAX_TEST_BYTES = 4_000
MAX_DESCRIPTION = 300
MAX_SKILL_BYTES = 6_000
SELF_TEST_TIMEOUT_S = 20.0

# On top of Forge's own policy (socket, subprocess, ctypes, multiprocessing): ACS-forged code gets the
# network and process libraries, dynamic import and (de)serialisers that execute code.
EXTRA_FORBIDDEN_IMPORTS = (
    "importlib", "shutil", "pty", "tty", "urllib", "http", "requests", "httpx", "aiohttp", "ftplib",
    "smtplib", "imaplib", "poplib", "telnetlib", "xmlrpc", "ssl", "marshal", "pickle", "cffi", "asyncio",
)

# reason codes written to the audit chain (never free text from the model)
R_SHAPE, R_SIZE, R_NAME, R_NAMESPACE, R_POLICY = "bad_shape", "too_large", "bad_name", "namespace_not_allowed", "policy_denied"
R_SANDBOX, R_IMPORTS, R_LICENSE, R_CREATE = "sandbox_unavailable", "forbidden_imports", "license_required", "create_failed"
R_SELFTEST, R_ECHO, R_EXPECT, R_LIMIT, R_DUP = "self_test_failed", "echo_tool", "expect_mismatch", "limit_reached", "duplicate"


def _ensure_paths() -> None:
    for p in (_OPERATOR / "forge", _OPERATOR / "skill-forge", _REPO):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))


@dataclass
class ForgeConfig:
    tools_enabled: bool = False
    skills_enabled: bool = False
    allowed_namespaces: tuple = ("dynamic",)
    max_tools: int = 5
    max_skills: int = 3
    # Also register a tool that passed its self-test in the tenant's user-scope Forge, where the other agentic
    # compute runtimes (sweeps, Bayesian optimisation, HAC, pipelines) resolve tools. Never overwrites a tool.
    publish: bool = False

    @property
    def enabled(self) -> bool:
        return self.tools_enabled or self.skills_enabled


def config_from_spec(spec: dict | None) -> ForgeConfig:
    """Read ``dynamic_tools`` and ``dynamic_skills`` of a workflow spec. Only a real ``True`` enables."""
    spec = spec or {}
    dt = spec.get("dynamic_tools") if isinstance(spec.get("dynamic_tools"), dict) else {}
    ds = spec.get("dynamic_skills") if isinstance(spec.get("dynamic_skills"), dict) else {}
    cfg = ForgeConfig()
    cfg.tools_enabled = dt.get("enabled") is True
    cfg.skills_enabled = ds.get("enabled") is True and ds.get("forge") is True
    cfg.publish = cfg.tools_enabled and dt.get("publish") is True
    ns = dt.get("allowed_namespaces")
    if isinstance(ns, list) and ns and all(isinstance(x, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,31}", x) for x in ns):
        cfg.allowed_namespaces = tuple(ns)
    for key, attr, hard in (("max_tools", "max_tools", MAX_TOOLS_HARD), ("max_skills", "max_skills", MAX_SKILLS_HARD)):
        src = dt if key == "max_tools" else ds
        try:
            v = int(src.get(key, getattr(cfg, attr)))
        except (TypeError, ValueError):
            v = getattr(cfg, attr)
        setattr(cfg, attr, max(1, min(v, hard)))
    return cfg


def _json_size(obj: Any) -> int:
    try:
        return len(json.dumps(obj, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return 10 ** 9


def subset_match(actual: Any, expected: Any) -> bool:
    """Every key/value of ``expected`` is present in ``actual`` (floats compared with a tolerance)."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and subset_match(actual[k], v) for k, v in expected.items())
    if isinstance(expected, list):
        return (isinstance(actual, list) and len(actual) == len(expected)
                and all(subset_match(a, e) for a, e in zip(actual, expected)))
    if isinstance(expected, bool) or isinstance(actual, bool):
        return actual is expected
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return math.isclose(float(actual), float(expected), rel_tol=1e-6, abs_tol=1e-9)
    return actual == expected


def _is_echo(data: Any, test_input: dict) -> bool:
    """True when the output is the Forge echo template's answer (``{"ok": true, "input": <the input>}``).

    The runner adds ``_artifacts_dir`` to the payload before the tool sees it, so the echoed ``input`` carries
    that extra key; comparing it verbatim missed every echo (found by the echo-template test, 2026-10-24).
    """
    if not isinstance(data, dict) or set(data) - {"ok", "input"}:
        return False
    echoed = data.get("input")
    if not isinstance(echoed, dict):
        return False
    return {k: v for k, v in echoed.items() if k != "_artifacts_dir"} == test_input


@dataclass
class ForgedTool:
    name: str
    description: str
    input_schema: dict
    sha256: str


class RunForge:
    """Per-run forge: validates, creates, self-tests and describes tools and skills. Thread-compatible (the manager
    loop calls ``apply`` from one thread at a time)."""

    def __init__(self, *, run_dir: Path, tenant_id: str, run_id: str, cfg: ForgeConfig,
                 audit: Callable[[str, str, dict], None]) -> None:
        self.run_dir = Path(run_dir)
        self.tenant_id = tenant_id
        self.run_id = run_id
        self.cfg = cfg
        self._audit = audit
        self.root = self.run_dir / "dynamic_tools_forge"
        self.tools: dict[str, ForgedTool] = {}
        self.skills: dict[str, str] = {}
        self.rejected = 0

    # ---- audit -------------------------------------------------------------------------------------
    def _emit(self, event: str, details: dict) -> None:
        try:
            self._audit(self.tenant_id, event, {"run_id": self.run_id, **details})
        except Exception:  # noqa: BLE001 - audit trouble must never decide a forge outcome (it is best-effort here)
            pass

    # ---- entry point -------------------------------------------------------------------------------
    def apply(self, request: Any, iteration: int) -> list[dict]:
        """Process a manager ``forge`` object. Returns one result dict per requested artefact (bounded)."""
        results: list[dict] = []
        if not isinstance(request, dict):
            return [{"kind": "request", "name": "", "ok": False, "reason": R_SHAPE}]
        tools = request.get("tools") if self.cfg.tools_enabled else None
        skills = request.get("skills") if self.cfg.skills_enabled else None
        if request.get("tools") and not self.cfg.tools_enabled:
            results.append({"kind": "tool", "name": "", "ok": False, "reason": "tools_not_enabled"})
        if request.get("skills") and not self.cfg.skills_enabled:
            results.append({"kind": "skill", "name": "", "ok": False, "reason": "skills_not_enabled"})
        for req in (tools if isinstance(tools, list) else [])[:MAX_PER_ITERATION]:
            results.append(self.forge_tool(req, iteration))
        for req in (skills if isinstance(skills, list) else [])[:MAX_PER_ITERATION]:
            results.append(self.forge_skill(req, iteration))
        return results

    # ---- tools -------------------------------------------------------------------------------------
    def _reject_tool(self, name: str, reason: str, iteration: int, detail: str = "") -> dict:
        self.rejected += 1
        self._emit("acs.tool_forge_rejected", {"iteration": iteration, "name": name[:80], "reason": reason})
        out = {"kind": "tool", "name": name[:80], "ok": False, "reason": reason}
        if detail:
            out["detail"] = detail[:240]
        return out

    def forge_tool(self, req: Any, iteration: int) -> dict:
        if not isinstance(req, dict):
            return self._reject_tool("", R_SHAPE, iteration)
        name = req.get("name")
        description = req.get("description")
        schema = req.get("input_schema")
        impl = req.get("impl")
        test_input = req.get("test_input")
        expect = req.get("expect")
        if not (isinstance(name, str) and isinstance(description, str) and isinstance(schema, dict)
                and isinstance(impl, str) and isinstance(test_input, dict)
                and (expect is None or isinstance(expect, dict))):
            return self._reject_tool(name if isinstance(name, str) else "", R_SHAPE, iteration)
        if len(self.tools) >= self.cfg.max_tools:
            return self._reject_tool(name, R_LIMIT, iteration)
        if (len(impl.encode("utf-8")) > MAX_IMPL_BYTES or _json_size(schema) > MAX_SCHEMA_BYTES
                or _json_size(test_input) > MAX_TEST_BYTES or _json_size(expect) > MAX_TEST_BYTES
                or len(description) > MAX_DESCRIPTION):
            return self._reject_tool(name, R_SIZE, iteration)
        if not NAME_RE.fullmatch(name):
            return self._reject_tool(name, R_NAME, iteration)
        if name.split(".", 1)[0] not in self.cfg.allowed_namespaces:
            return self._reject_tool(name, R_NAMESPACE, iteration)
        if name in self.tools:
            return self._reject_tool(name, R_DUP, iteration)
        if schema.get("type") != "object":
            return self._reject_tool(name, R_SHAPE, iteration, "input_schema.type must be 'object'")

        try:
            _ensure_paths()
            from forge.policy import Policy
            from forge.registry import Registry
            from forge.runner import ToolError, run_tool
            from forge.sandbox import have_bwrap
            from forge.static_check import StaticCheckError, assert_imports_ok
            from forge.runner import _bwrap_preflight_check
        except Exception as exc:  # noqa: BLE001 - no Forge, no tool
            return self._reject_tool(name, R_CREATE, iteration, f"forge unavailable: {type(exc).__name__}")

        policy = Policy()
        allowed, _why = policy.name_allowed(name)
        if not allowed:
            return self._reject_tool(name, R_POLICY, iteration)
        ok_bwrap, bwrap_err = (False, "bwrap missing")
        if have_bwrap():
            ok_bwrap, bwrap_err = _bwrap_preflight_check()
        if not ok_bwrap:
            return self._reject_tool(name, R_SANDBOX, iteration, bwrap_err)
        try:
            assert_imports_ok(impl, forbidden=sorted(set(policy.forbidden_imports) | set(EXTRA_FORBIDDEN_IMPORTS)),
                              runtime="python")
        except StaticCheckError as exc:
            return self._reject_tool(name, R_IMPORTS, iteration, ", ".join(exc.violations)[:200])

        registry = Registry(self.root)
        try:
            spec = registry.create(
                name=name, description=description, input_schema=schema, impl=impl, runtime="python",
                scope="session", overwrite=False, tenant_id=self.tenant_id,
                meta={"origin": "acs", "run_id": self.run_id, "deterministic": False,
                      "budget": {"cpu_seconds": 5, "wall_seconds": 15}},
            )
        except PermissionError as exc:
            return self._reject_tool(name, R_LICENSE, iteration, str(exc)[:200])
        except Exception as exc:  # noqa: BLE001
            return self._reject_tool(name, R_CREATE, iteration, f"{type(exc).__name__}: {exc}"[:200])

        # self-test inside the sandbox; the first successful sandboxed run also records the approval (sha-bound)
        try:
            res = run_tool(registry, name, dict(test_input), timeout=SELF_TEST_TIMEOUT_S, permission_mode="yes",
                           use_sandbox=True, policy=policy)
        except Exception as exc:  # noqa: BLE001 - schema error, tamper, tool error, timeout
            self._discard(registry, name)
            return self._reject_tool(name, R_SELFTEST, iteration, f"{type(exc).__name__}: {exc}"[:200])
        if not res.ok or res.sandbox != "bwrap":
            self._discard(registry, name)
            why = (res.stderr or "")[-200:] if not res.ok else f"ran in {res.sandbox}, not bwrap"
            return self._reject_tool(name, R_SELFTEST, iteration, why)
        data = res.data
        if not isinstance(data, dict) or not data:
            self._discard(registry, name)
            return self._reject_tool(name, R_SELFTEST, iteration, "output is not a non-empty JSON object")
        if _is_echo(data, test_input):
            self._discard(registry, name)
            return self._reject_tool(name, R_ECHO, iteration)
        if expect and not subset_match(data, expect):
            self._discard(registry, name)
            return self._reject_tool(name, R_EXPECT, iteration, "output "
                                     + json.dumps(data, default=str)[:160] + " lacks the expected values")

        self.tools[name] = ForgedTool(name=name, description=description, input_schema=schema, sha256=spec.sha256)
        self._emit("acs.tool_forged", {
            "iteration": iteration, "name": name, "sha256": spec.sha256, "impl_bytes": len(impl.encode("utf-8")),
            "self_test_ms": int(res.duration_s * 1000), "sandbox": res.sandbox,
        })
        out = {"kind": "tool", "name": name, "ok": True, "sha256": spec.sha256}
        if self.cfg.publish:
            out["published"] = self._publish(name, description, schema, impl, test_input, expect, spec.sha256, iteration)
        return out

    def _publish(self, name: str, description: str, schema: dict, impl: str, test_input: dict,
                 expect: dict | None, sha: str, iteration: int) -> bool:
        """Register the vetted tool in the tenant's user-scope Forge so the other compute runtimes can use it.

        Same sandbox self-test again, in the user-scope registry; an existing tool of that name (any case) is never
        replaced. A failure leaves the tool usable for this run only and is audited with a reason code.
        """
        def _skip(reason: str) -> bool:
            self._emit("acs.tool_publish_rejected", {"iteration": iteration, "name": name[:80], "reason": reason})
            return False

        try:
            from forge.policy import Policy
            from forge.registry import Registry
            from forge.runner import run_tool
            from forge.scope import scope_root
            user_root = scope_root("user", tenant_id=self.tenant_id)
            user_root.mkdir(parents=True, exist_ok=True)
            reg = Registry(user_root)
            folded = name.casefold()
            if any(t.name.casefold() == folded for t in reg.list()):
                return _skip(R_DUP)
            reg.create(name=name, description=description, input_schema=schema, impl=impl, runtime="python",
                       scope="user", overwrite=False, tenant_id=self.tenant_id,
                       meta={"origin": "acs", "run_id": self.run_id, "deterministic": False,
                             "budget": {"cpu_seconds": 5, "wall_seconds": 15}})
        except PermissionError:
            return _skip(R_LICENSE)
        except Exception:  # noqa: BLE001 - publishing is an extra; the run-local tool already works
            return _skip(R_CREATE)
        try:
            res = run_tool(reg, name, dict(test_input), timeout=SELF_TEST_TIMEOUT_S, permission_mode="yes",
                           use_sandbox=True, policy=Policy())
            good = (res.ok and res.sandbox == "bwrap" and isinstance(res.data, dict)
                    and (not expect or subset_match(res.data, expect)))
        except Exception:  # noqa: BLE001
            good = False
        if not good:
            self._discard(reg, name)
            return _skip(R_SELFTEST)
        self._emit("acs.tool_published", {"iteration": iteration, "name": name, "sha256": sha})
        return True

    @staticmethod
    def _discard(registry: Any, name: str) -> None:
        try:
            registry.delete(name)
        except Exception:  # noqa: BLE001 - an orphaned run-local artefact is not a grant (it was never approved)
            pass

    # ---- skills ------------------------------------------------------------------------------------
    def _reject_skill(self, name: str, reason: str, iteration: int, detail: str = "") -> dict:
        self.rejected += 1
        self._emit("acs.skill_forge_rejected", {"iteration": iteration, "name": name[:80], "reason": reason})
        out = {"kind": "skill", "name": name[:80], "ok": False, "reason": reason}
        if detail:
            out["detail"] = detail[:240]
        return out

    def forge_skill(self, req: Any, iteration: int) -> dict:
        if not isinstance(req, dict):
            return self._reject_skill("", R_SHAPE, iteration)
        name, description, body = req.get("name"), req.get("description"), req.get("body")
        if not (isinstance(name, str) and isinstance(body, str) and isinstance(description, (str, type(None)))):
            return self._reject_skill(name if isinstance(name, str) else "", R_SHAPE, iteration)
        if len(self.skills) >= self.cfg.max_skills:
            return self._reject_skill(name, R_LIMIT, iteration)
        if not SKILL_NAME_RE.fullmatch(name):
            return self._reject_skill(name, R_NAME, iteration)
        if len(body.encode("utf-8")) > MAX_SKILL_BYTES or len(description or "") > MAX_DESCRIPTION or not body.strip():
            return self._reject_skill(name, R_SIZE, iteration)
        if name in self.skills:
            return self._reject_skill(name, R_DUP, iteration)

        persisted, lint_error = False, ""
        try:
            _ensure_paths()
            from corvin_operator.forge.forge.paths import tenant_home
            from skill_forge.registry import LinterError, SkillRegistry
            root = Path(tenant_home(self.tenant_id)) / "skill-forge"
            root.mkdir(parents=True, exist_ok=True)
            try:
                SkillRegistry(root).create(
                    name=f"acs.{name}", type="learned-experience", body_md=body,
                    description=(description or body[:120]).replace("\n", " ").strip()[:MAX_DESCRIPTION],
                    claim={}, scope="session", overwrite=True, created_by="acs",
                    meta={"lifecycle": "turn", "run_id": self.run_id, "origin": "acs"},
                )
                persisted = True
            except LinterError as exc:
                lint_error = "; ".join(exc.violations)[:200]
            except Exception as exc:  # noqa: BLE001 - quota, licence, lock: the run-local copy still works
                persisted = False
                lint_error = ""
                self._emit("acs.skill_persist_failed", {"iteration": iteration, "name": f"acs.{name}",
                                                        "reason": type(exc).__name__})
        except Exception:  # noqa: BLE001 - SkillForge not importable: run-local only
            persisted = False
        if lint_error:
            return self._reject_skill(name, "lint_rejected", iteration, lint_error)

        self.skills[name] = body.strip()
        self._emit("acs.skill_forged", {"iteration": iteration, "name": f"acs.{name}",
                                        "body_bytes": len(body.encode("utf-8")), "persisted": persisted})
        return {"kind": "skill", "name": f"acs.{name}", "ok": True, "persisted": persisted}

    # ---- text for the manager and the workers ------------------------------------------------------
    def manager_protocol(self) -> str:
        """The block that teaches the manager the forge protocol (only when forging is enabled)."""
        parts = ["RUNTIME FORGING (enabled for this run). Before delegating you may extend what your workers can do by "
                 "adding a \"forge\" object to a DELEGATE decision:", "\"forge\": {"]
        if self.cfg.tools_enabled:
            ns = "|".join(self.cfg.allowed_namespaces)
            parts += [
                f"  \"tools\": [ {{\"name\": \"<{ns}>.<snake_name>\", \"description\": \"...\", "
                "\"input_schema\": {\"type\": \"object\", \"properties\": {...}, \"required\": [...]}, "
                "\"impl\": \"<python source>\", \"test_input\": {...}, \"expect\": {...}} ],",
            ]
        if self.cfg.skills_enabled:
            parts += ["  \"skills\": [ {\"name\": \"<snake_name>\", \"description\": \"...\", \"body\": \"<prose instructions>\"} ]"]
        parts += ["}"]
        if self.cfg.tools_enabled:
            parts += [
                "Tool rules: Python 3 standard library only. The program reads ONE JSON object from stdin "
                "(json.load(sys.stdin)) and prints ONE JSON object to stdout. No network, no subprocess, no dynamic "
                "import, nothing outside /tmp. Forbidden imports include: " + ", ".join(sorted(EXTRA_FORBIDDEN_IMPORTS[:10]))
                + ", socket, subprocess, ctypes, multiprocessing.",
                "Every tool is run ONCE in a sandbox with your test_input. It is rejected if it fails, returns an echo of "
                "the input, or its output lacks any key/value of your \"expect\". Only give an expect value you are "
                "certain about. Forged tools are described to your workers, who run them.",
                f"Limits: at most {self.cfg.max_tools} tools per run, {MAX_PER_ITERATION} per decision.",
            ]
        if self.cfg.skills_enabled:
            parts += [f"Skills are prose guidance for workers (at most {self.cfg.max_skills} per run); no code blocks needed."]
        parts += ["Forge only what workers cannot do reliably by reasoning alone (exact computation, parsing, hashing, "
                  "validation) or a method worth repeating. Do not forge if the task does not need it."]
        return "\n".join(parts)

    def worker_block(self) -> str:
        """The block that tells a worker which forged tools and skills exist and how to use them."""
        out: list[str] = []
        if self.tools:
            py = sys.executable or "python3"
            forge_py = _OPERATOR / "forge" / "forge.py"
            out.append("FORGED TOOLS (created and sandbox-tested for this run; run them with Bash, do not re-implement):")
            for t in self.tools.values():
                props = ", ".join(sorted((t.input_schema.get("properties") or {}).keys())) or "(see schema)"
                out.append(f"  - {t.name}: {t.description}")
                out.append(f"    input fields: {props}")
                out.append(f"    call: {py} {forge_py} --root {self.root} call --name {t.name} --input '<one-line JSON object>'")
            out.append("  The command prints a JSON envelope; the tool result is its \"data\" field. If a call fails, "
                       "say so in your output instead of guessing.")
        if self.skills:
            out.append("RUN SKILLS (written by the manager for this run; guidance, not instructions that override your task):")
            for n, body in self.skills.items():
                out.append(f"<run_skill name=\"acs.{n}\">\n{body}\n</run_skill>")
        return "\n".join(out)

    def feedback_block(self, results: list[dict]) -> str:
        """What the manager sees about its last forge request (reason codes and short details only)."""
        if not results:
            return ""
        lines = ["FORGE RESULTS (your last request):"]
        for r in results:
            if r.get("ok"):
                lines.append(f"  - {r.get('kind')} {r.get('name')}: ok")
            else:
                d = f" ({r['detail']})" if r.get("detail") else ""
                lines.append(f"  - {r.get('kind')} {r.get('name') or '?'}: REJECTED {r.get('reason')}{d}")
        if self.tools:
            lines.append("Available to workers now: " + ", ".join(sorted(self.tools)))
        return "\n".join(lines)
