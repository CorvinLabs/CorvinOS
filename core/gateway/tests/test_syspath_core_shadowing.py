"""`core/` must never go on sys.path — its subdirs shadow bridge modules.

The defect this exists to catch took the console down on 2026-08-31. Commit
6ab97601 added, to the gateway host:

    _core_path = _corvin_root / 'core'
    sys.path.insert(0, str(_core_path))

`core/` holds ~40 subpackages with generic top-level names — `audit`,
`console`, `context`, `agent`, `license`, `integration`, `features`,
`monitoring`. Putting that directory FIRST on sys.path makes every one of them
win over the same-named module on PYTHONPATH. The boot tripwire's
`import audit` (ADR-0232/0233) then resolved to `core/audit/`, which is a
different package with no `audit_path()`; `audit_writer_reachable` and
`audit_chain_intact` both failed with AttributeError, the tripwire refused to
boot — correctly, it is fail-closed by design — and `corvin-webui.service`
crash-looped for 45 restarts serving ERR_CONNECTION_REFUSED.

Nothing needs `core/` on the path: every consumer imports the subpackages as
`core.<name>` (`from core.learning import ...`), which resolves from the repo
ROOT. So the fix is structural — never add the directory — and this is the
guard, checked at the source level so it fails on the edit rather than on the
next boot.
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]

#: Modules that run at import time on a boot path and manipulate sys.path.
_BOOT_MODULES = {
    "gateway (corvin-service, corvin-webui.service)":
        _REPO / "core" / "gateway" / "corvin_gateway" / "app.py",
    "console (corvinos-serve, install.sh)":
        _REPO / "core" / "console" / "corvin_console" / "standalone.py",
}

#: Top-level names under core/ that also exist as importable modules elsewhere
#: (bridges/shared, PYTHONPATH entries). Shadowing any of them breaks a caller.
_COLLIDING = ("audit", "console", "context", "agent", "license", "integration")


def _syspath_mutations(tree: ast.AST) -> list[ast.Call]:
    """Every `sys.path.insert(...)` / `sys.path.append(...)` call in a module."""
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in ("insert", "append"):
            continue
        target = node.func.value
        if (
            isinstance(target, ast.Attribute)
            and target.attr == "path"
            and isinstance(target.value, ast.Name)
            and target.value.id == "sys"
        ):
            found.append(node)
    return found


class SysPathCoreShadowing(unittest.TestCase):
    def test_no_boot_module_puts_core_on_syspath(self) -> None:
        """No boot host may add the repo's `core/` directory to sys.path."""
        for label, path in _BOOT_MODULES.items():
            if not path.exists():
                continue
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))

            for call in _syspath_mutations(tree):
                # The pushed value, rendered back to source, must not name the
                # bare `core` directory — as a literal, a joinpath, or a `/`.
                pushed = call.args[-1] if call.args else None
                if pushed is None:
                    continue
                rendered = ast.unparse(pushed)
                self.assertNotRegex(
                    rendered,
                    # NB: no leading \b before core_path — the real offender was
                    # named `_core_path`, and `_` is a word character, so a
                    # boundary there never matches. That exact miss let the
                    # first draft of this guard pass on the code it was
                    # written to reject.
                    r"""['"]core['"]|core_path|/\s*core\b""",
                    f"{label}: {path.relative_to(_REPO)} line {call.lineno} pushes "
                    f"the repo's core/ onto sys.path ({rendered}). core/ holds "
                    f"generic top-level names ({', '.join(_COLLIDING)}) that shadow "
                    f"bridge modules — the tripwire's `import audit` then resolves "
                    f"to core/audit and the boot fails closed. Import subpackages "
                    f"as `core.<name>` from the repo root instead.",
                )

    def test_core_subdirs_still_collide(self) -> None:
        """The collision this guards is real — fail loudly if core/ is reshaped.

        If these directories ever go away the guard above is arguably dead
        weight; make that visible instead of letting it rot silently.
        """
        core = _REPO / "core"
        self.assertTrue(core.is_dir(), "core/ missing — repo layout changed")
        present = [n for n in _COLLIDING if (core / n).is_dir()]
        self.assertTrue(
            present,
            "no colliding top-level names left under core/ — re-evaluate this guard",
        )

    def test_bridge_audit_module_is_the_one_with_audit_path(self) -> None:
        """`operator/bridges/shared/audit.py` — not core/audit — owns audit_path().

        This is the asymmetry that made the shadow fatal rather than merely
        confusing: both modules are named `audit`, only one has the function
        the tripwire calls.
        """
        bridge = _REPO / "operator" / "bridges" / "shared" / "audit.py"
        self.assertTrue(bridge.is_file(), "bridge audit module missing")
        self.assertIn("def audit_path", bridge.read_text(encoding="utf-8"))

        core_audit = _REPO / "core" / "audit" / "__init__.py"
        if core_audit.is_file():
            self.assertNotIn(
                "def audit_path",
                core_audit.read_text(encoding="utf-8"),
                "core/audit now also defines audit_path() — if that is deliberate, "
                "the two modules must agree on the contract the tripwire calls",
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
