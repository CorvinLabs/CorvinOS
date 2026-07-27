"""Every shipped host must run the compliance boot sequence (ADR-0232/0233).

This is a CALL-SITE gate, not a unit test of the sequence itself. The defect it
exists to catch was found on 2026-07-27 by booting a wheel install:

    * ``corvin_console.standalone:create_app`` — what ``corvinos-serve`` runs and
      what ``install.sh`` launches — never called the tripwire. A console with a
      deliberately corrupted audit hash chain booted and answered requests.
    * The same tripwire, invoked by hand in that same process, refused correctly.

So the mechanism worked and nothing reached it: the exact class ADR-0233 is named
for, one level up. A unit test of ``assert_all()`` would have stayed green through
all of it, which is why the assertion here is about the CALLER.

The tests are deliberately cheap and structural — they read the source of each
host and assert it reaches the shared sequence. A behavioural test would need to
boot two full apps with a corrupted chain per host; the E2E in
``test_lifecycle_e2e.py`` covers the behaviour, and this covers the wiring that
E2E cannot see when a host is simply absent from it.
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]

#: Every module that constructs a FastAPI app CorvinOS serves traffic from.
#: Adding a third host means adding it here — that is the point.
_HOSTS = {
    "console (corvinos-serve, install.sh)":
        _REPO / "core" / "console" / "corvin_console" / "standalone.py",
    "gateway (corvin-service, installer wizard)":
        _REPO / "core" / "gateway" / "corvin_gateway" / "app.py",
}

_SEQUENCE = "boot_platform"


def _imported_names(path: Path) -> set[str]:
    """Names this module imports, however deeply nested the import sits.

    An AST walk rather than a substring search: ``"boot_platform" in text`` is
    also true of a comment mentioning it, and a wiring gate that a comment can
    satisfy is the kind of guard that survives the removal of the thing it
    guards.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.rsplit(".", 1)[-1])
    return names


def _called_names(path: Path) -> set[str]:
    """Local names that are actually CALLED somewhere in the module."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    called: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name):
                called.add(fn.id)
            elif isinstance(fn, ast.Attribute):
                called.add(fn.attr)
    return called


class TestEveryHostRunsTheComplianceSequence(unittest.TestCase):
    def test_each_host_imports_the_shared_sequence(self):
        for label, path in _HOSTS.items():
            with self.subTest(host=label):
                self.assertTrue(path.is_file(), f"{label}: {path} is missing")
                self.assertIn(
                    _SEQUENCE, _imported_names(path),
                    f"{label} does not import {_SEQUENCE}. Every host that serves "
                    f"traffic must run the fail-closed compliance tripwires "
                    f"before it accepts a request — there is no override "
                    f"(CLAUDE.md § Compliance Baseline).",
                )

    def test_each_host_actually_calls_it(self):
        """Importing is not invoking — that distinction IS this defect class."""
        for label, path in _HOSTS.items():
            with self.subTest(host=label):
                called = _called_names(path)
                # The hosts bind it to a private alias before calling, so accept
                # either the bare name or the conventional underscore alias.
                self.assertTrue(
                    {_SEQUENCE, f"_{_SEQUENCE}"} & called,
                    f"{label} imports {_SEQUENCE} but never calls it. A bound "
                    f"name is not a call site.",
                )

    def test_the_sequence_is_not_behind_a_feature_flag(self):
        """A default-off switch on a mandatory mechanism is a kill flag."""
        from corvin_plugins import bootstrap

        source = Path(bootstrap.__file__).read_text(encoding="utf-8")
        start = source.index("def boot_platform(")
        end = source.index("\ndef ", start + 1)
        body = source[start:end]
        self.assertNotIn(
            "is_enabled(\"plugin_extension", body,
            "boot_platform must not gate the tripwires on a feature flag",
        )
        for forbidden in ("CORVIN_SKIP_TRIPWIRE", "CORVIN_NO_COMPLIANCE",
                          "skip_tripwire", "disable_compliance"):
            self.assertNotIn(
                forbidden, body,
                f"boot_platform must carry no override switch (found {forbidden!r})",
            )

    def test_the_sequence_runs_the_tripwire_before_loading_plugins(self):
        """Order is load-bearing: a broken audit writer must stop the boot first.

        Read off the AST, not the source text. A substring search over the
        function also matches this very docstring, which named the three steps in
        prose and made the ordering assertion fail against nothing but its own
        explanation — a guard that measures the comment instead of the code.
        """
        from corvin_plugins import bootstrap

        tree = ast.parse(Path(bootstrap.__file__).read_text(encoding="utf-8"))
        fn = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "boot_platform"
        )
        # ast.walk yields breadth-first, so rank by source line instead.
        ranked = sorted(
            (
                (node.lineno,
                 node.func.id if isinstance(node.func, ast.Name) else node.func.attr)
                for node in ast.walk(fn)
                if isinstance(node, ast.Call)
                and isinstance(node.func, (ast.Name, ast.Attribute))
            )
        )
        # The steps are bound to underscore-prefixed aliases before being called,
        # so compare on the unprefixed name.
        names = [name.lstrip("_") for _, name in ranked]
        for required in ("assert_compliance", "bootstrap_all", "assert_post_boot"):
            self.assertIn(required, names, f"boot_platform never calls {required}")
        self.assertLess(
            names.index("assert_compliance"), names.index("bootstrap_all"),
            "the tripwires must run BEFORE any plugin is loaded",
        )
        self.assertLess(
            names.index("bootstrap_all"), names.index("assert_post_boot"),
            "the post-boot tripwire must run AFTER the plugins are loaded — "
            "asked earlier it is vacuously green",
        )


if __name__ == "__main__":
    unittest.main()
