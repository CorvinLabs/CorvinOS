"""E2E: MultiRegistry shadowing + scope-aware create/promote/delete."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from forge.multi_registry import MultiRegistry  # noqa: E402

PASS = 0
FAIL = 0


def t(label, ok, *, detail=""):
    global PASS, FAIL
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' — ' + detail) if detail else ''}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


SCHEMA = {"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]}
IMPL = "def run(x):\n    return {'doubled': x * 2}\n"


def _clean_env(td):
    for k in (
        "CORVIN_FORCE_SCOPE",
        "CORVIN_DEFAULT_SCOPE",
        "CORVIN_CHANNEL_ID",
        "CORVIN_TASK_ID",
    ):
        os.environ.pop(k, None)
    os.environ["CORVIN_HOME"] = td


def test_shadowing():
    print("\n[shadowing — higher scope wins]")
    with tempfile.TemporaryDirectory() as td:
        _clean_env(td)
        mr = MultiRegistry(channel_id="test-channel", task_id="t1")
        # create in user scope first
        mr.create(
            scope="user", name="dbl", description="user-version",
            input_schema=SCHEMA, impl=IMPL, runtime="python", overwrite=True,
        )
        # then in session scope
        mr.create(
            scope="session", name="dbl", description="session-version",
            input_schema=SCHEMA, impl=IMPL, runtime="python", overwrite=True,
        )
        spec = mr.get("dbl")
        t(
            "get returns session version (higher scope shadowed lower)",
            spec is not None and spec.description == "session-version",
        )
        # explicit scope lookup
        user_spec = mr.get_in_scope("dbl", "user")
        t(
            "get_in_scope user still finds user version",
            user_spec is not None and user_spec.description == "user-version",
        )
        # find_scope reports session
        t("find_scope returns 'session'", mr.find_scope("dbl") == "session")
        # list() yields exactly one entry, the shadowed one
        all_ = mr.list()
        names = [s.name for s in all_]
        t("list() dedupes the shadowed name", names.count("dbl") == 1)


def test_promote():
    print("\n[promote — moves up the hierarchy]")
    with tempfile.TemporaryDirectory() as td:
        _clean_env(td)
        mr = MultiRegistry(channel_id="ch", task_id="t")
        mr.create(
            scope="user", name="prom", description="orig",
            input_schema=SCHEMA, impl=IMPL, runtime="python", overwrite=True,
        )
        spec = mr.promote("prom", to="project")
        t("promote returns spec", spec is not None and spec.name == "prom")
        proj = mr.get_in_scope("prom", "project")
        t("found in project after promote", proj is not None)


def test_delete_scoped():
    print("\n[delete — scoped]")
    with tempfile.TemporaryDirectory() as td:
        _clean_env(td)
        mr = MultiRegistry(channel_id="ch", task_id="t")
        mr.create(
            scope="user", name="z", description="d",
            input_schema=SCHEMA, impl=IMPL, runtime="python", overwrite=True,
        )
        ok = mr.delete("z", scope="user")
        t("delete returned True", ok)
        t("get returns None after delete", mr.get("z") is None)


def test_invalid_scope():
    print("\n[invalid scope rejected]")
    with tempfile.TemporaryDirectory() as td:
        _clean_env(td)
        mr = MultiRegistry()
        try:
            mr.create(
                scope="bogus", name="x", description="d",
                input_schema=SCHEMA, impl=IMPL, runtime="python",
            )
            t("ValueError raised", False, detail="no exception")
        except ValueError as e:
            t("ValueError raised", "bogus" in str(e))


def main() -> int:
    test_shadowing()
    test_promote()
    test_delete_scoped()
    test_invalid_scope()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
