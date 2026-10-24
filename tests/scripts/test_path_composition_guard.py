"""scripts/path_composition_guard.py — behaviour pinned on synthetic git repos,
plus one wiring test against the real tree and the committed baseline.

The guard must (a) find hand-built chain/tenant path composition in production
code in all its forms, (b) ignore tests, harnesses and anything git does not
track, (c) key findings so they survive line shifts but not expression changes,
(d) make --check fail on new keys, rising or falling counts, empty reasons and
stale entries, (e) regenerate the baseline shrink-only unless a key is accepted
with its own reason, and (f) spawn no process itself: the tracked-file list is
fed in (scripts/path_composition_guard.sh pipes ``git ls-files -z``). The tests
produce that list with git in tmp repos, exactly like the wrapper does.
"""
from __future__ import annotations

import ast
import io
import json
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import path_composition_guard as g  # noqa: E402

REASON = "measured-2026-10-24, not yet triaged"


# ------------------------------------------------------------------ helpers
def _git_repo(root: Path, files: dict[str, str], untracked: dict[str, str] | None = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    _write(root, files)
    _git_add(root)
    _write(root, untracked or {})
    return root


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


def _git_add(root: Path) -> None:
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)


def _ls_files_z(root: Path) -> bytes:
    """What the wrapper pipes in: ``git ls-files -z -- '*.py'``."""
    return subprocess.run(["git", "-C", str(root), "ls-files", "-z", "--", "*.py"],
                          capture_output=True, check=True).stdout


def _scan(root: Path) -> dict:
    return g.scan(root, g.parse_file_list(_ls_files_z(root), nul=True))


def _main(root: Path, tmp: Path, *args: str) -> int:
    lst = tmp / "files.lst"
    lst.write_bytes(_ls_files_z(root))
    return g.main(["--root", str(root), "--files-from", str(lst), "-0", *args])


def _bootstrap_baseline(root: Path, tmp: Path, reason: str = REASON) -> Path:
    """First baseline: every key accepted explicitly via --accept-file."""
    accept = tmp / "accept.json"
    accept.write_text(json.dumps({k: reason for k in _scan(root)["counts"]}), encoding="utf-8")
    base = tmp / "baseline.json"
    assert _main(root, tmp, "--baseline", str(base), "--write-baseline",
                 "--accept-file", str(accept)) == 0
    return base


def _kinds(report: dict) -> list[tuple[str, str]]:
    return sorted((f["kind"], f["expr"]) for f in report["findings"])


PATHS_PY = '''\
def chain_path(home, tid):
    return home / "tenants" / tid / "global" / "audit.jsonl"
'''


# ---------------------------------------------------------- no process spawn
def test_scanner_spawns_no_process():
    """The path gate refuses ``python3 <file>`` for files that import
    subprocess; the scanner must not need that capability at all."""
    src = (REPO / "scripts" / "path_composition_guard.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    banned_modules = {"subprocess", "pty", "multiprocessing", "os"}
    banned_os_attrs = {"system", "popen", "spawnl", "spawnv", "spawnvp", "execv", "execvp",
                       "fork", "posix_spawn"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in banned_modules, alias.name
        elif isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] not in banned_modules, node.module
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
                and node.value.id == "os":
            assert node.attr not in banned_os_attrs, f"os.{node.attr}"


def test_windows_invocation_is_documented():
    doc = ast.get_docstring(ast.parse(
        (REPO / "scripts" / "path_composition_guard.py").read_text(encoding="utf-8")))
    assert r"python scripts\path_composition_guard.py --files-from - -0" in doc


# ------------------------------------------------------------------- input
def test_file_list_parsing_newline_and_nul():
    assert g.parse_file_list(b"a.py\nb/c.py\n\n", nul=False) == ["a.py", "b/c.py"]
    assert g.parse_file_list(b"a.py\r\nb.py\r\n", nul=False) == ["a.py", "b.py"]
    assert g.parse_file_list(b"a b.py\0x\ny.py\0", nul=True) == ["a b.py", "x\ny.py"]


def test_files_from_stdin(tmp_path, monkeypatch, capsys):
    root = _git_repo(tmp_path / "repo", {"pkg/paths.py": PATHS_PY})
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(_ls_files_z(root))))
    assert g.main(["--root", str(root), "--files-from", "-", "-0"]) == 0
    assert json.loads(capsys.readouterr().out)["total_findings"] == 3


# ---------------------------------------------------------------- coverage
def test_global_join_in_function_is_found(tmp_path):
    root = _git_repo(tmp_path / "repo", {"pkg/paths.py": PATHS_PY})
    report = _scan(root)
    assert {f["function"] for f in report["findings"]} == {"chain_path"}
    exprs = [f["expr"] for f in report["findings"]]
    for lit in ("'global'", "'tenants'", "'audit.jsonl'"):
        assert any(lit in e for e in exprs)
    assert {f["kind"] for f in report["findings"]} == {"binop"}
    assert all(f["key"].startswith("pkg/paths.py::chain_path::") for f in report["findings"])


FORMS_PY = '''\
import os
import os.path as osp
from os.path import join as pjoin
from pathlib import Path, PurePath

AUDIT_CHAIN_NAME = "audit.jsonl"
LABEL = "not-a-path"


class Registry:
    AUDIT_NAME = "audit.jsonl"

    def audit(self):
        return self.root / self.AUDIT_NAME

    def other(self):
        return self.root / self.LABEL


def module_const(tenant):
    return tenant / AUDIT_CHAIN_NAME


def class_ref(r):
    return r / Registry.AUDIT_NAME


def joins(home, tid):
    a = os.path.join(home, "tenants", tid)
    b = osp.join(home, "global")
    c = pjoin(home, "audit_trail.jsonl")
    d = Path(home, "tenants", tid)
    e = PurePath(home, "global")
    f = Path("x/tenants/y")
    g = home.joinpath("global", "forge")
    h = Path("tenants") / tid
    i = home / "tenants/_default"
    j = home / "github-audit.jsonl"
    k = Path("tenants").joinpath(tid)
    return a, b, c, d, e, f, g, h, i, j, k


def not_findings(home):
    x = Path("tenants")
    y = home / "globals"
    z = "/".join([home, "global"])
    w = home / "audit.json"
    return x, y, z, w
'''


def test_all_forms_and_kinds(tmp_path):
    root = _git_repo(tmp_path / "repo", {"pkg/forms.py": FORMS_PY})
    report = _scan(root)
    got = _kinds(report)
    assert got == sorted([
        ("constant", "self.root / self.AUDIT_NAME"),
        ("constant", "tenant / AUDIT_CHAIN_NAME"),
        ("constant", "r / Registry.AUDIT_NAME"),
        ("os.path.join", "os.path.join(home, 'tenants', tid)"),
        ("os.path.join", "osp.join(home, 'global')"),
        ("os.path.join", "pjoin(home, 'audit_trail.jsonl')"),
        ("Path-ctor", "Path(home, 'tenants', tid)"),
        ("Path-ctor", "PurePath(home, 'global')"),
        ("Path-ctor", "Path('x/tenants/y')"),
        ("joinpath", "home.joinpath('global', 'forge')"),
        ("binop", "Path('tenants') / tid"),
        ("binop", "home / 'tenants/_default'"),
        ("binop", "home / 'github-audit.jsonl'"),
        ("joinpath", "Path('tenants').joinpath(tid)"),
    ]), got
    forms = {f["expr"]: f.get("form") for f in report["findings"] if f["kind"] == "constant"}
    assert set(forms.values()) == {"binop"}
    assert report["by_kind"] == {"Path-ctor": 3, "binop": 3, "constant": 3,
                                 "joinpath": 2, "os.path.join": 3}


def test_watched_string_rules():
    for s in ("global", "tenants", "a/tenants/b", "audit.jsonl", "audit_trail.jsonl",
              "github-audit.jsonl", "recv_audit.jsonl", "x/audit.jsonl"):
        assert g.is_watched_string(s), s
    for s in ("globals", "tenant", "audit.json", "auditor.txt", "my_auditing.log", ""):
        assert not g.is_watched_string(s), s


# ------------------------------------------------------------------- scope
def test_test_files_are_excluded(tmp_path):
    root = _git_repo(tmp_path / "repo", {
        "tests/test_paths.py": PATHS_PY,
        "pkg/tests/helpers.py": PATHS_PY,
        "pkg/test_thing.py": PATHS_PY,
        "pkg/conftest.py": PATHS_PY,
        "pkg/real.py": "X = 1\n",
    })
    report = _scan(root)
    assert report["findings"] == []
    assert report["py_in"] == 5
    assert report["production_py"] == 1


def test_scope_harnesses_excluded_scripts_kept(tmp_path):
    root = _git_repo(tmp_path / "repo", {
        "benchmark/run.py": PATHS_PY,
        "benchmarks/run.py": PATHS_PY,
        "pkg/examples/demo.py": PATHS_PY,
        "examples/demo.py": PATHS_PY,
        "scripts/e2e_full_audit_fixture.py": PATHS_PY,
        "scripts/operator_tool.py": PATHS_PY,
        "pkg/benchmark/real.py": PATHS_PY,  # only the TOP-level harness dir is excluded
    })
    files = {f["file"] for f in _scan(root)["findings"]}
    assert files == {"scripts/operator_tool.py", "pkg/benchmark/real.py"}


def test_test_named_production_modules_are_scanned(tmp_path):
    files = {rel: PATHS_PY for rel in g.TEST_NAMED_PRODUCTION}
    files["core/audit/test_other.py"] = PATHS_PY
    root = _git_repo(tmp_path / "repo", files)
    assert {f["file"] for f in _scan(root)["findings"]} == set(g.TEST_NAMED_PRODUCTION)


def test_untracked_file_is_excluded(tmp_path):
    root = _git_repo(tmp_path / "repo", {"pkg/tracked.py": PATHS_PY},
                     untracked={"pkg/untracked.py": PATHS_PY})
    assert {f["file"] for f in _scan(root)["findings"]} == {"pkg/tracked.py"}


def test_deleted_tracked_file_is_missing_not_unparseable(tmp_path):
    root = _git_repo(tmp_path / "repo", {"pkg/gone.py": PATHS_PY, "pkg/bad.py": "def (:\n"})
    (root / "pkg" / "gone.py").unlink()
    errors = {e["file"]: e["error"] for e in _scan(root)["errors"]}
    assert errors["pkg/gone.py"] == "missing in working tree"
    assert errors["pkg/bad.py"].startswith("unparseable")


def test_non_relative_path_in_list_is_an_error(tmp_path):
    root = _git_repo(tmp_path / "repo", {"pkg/real.py": "X = 1\n"})
    report = g.scan(root, ["../escape.py", "/abs.py"])
    assert report["findings"] == []
    assert len(report["errors"]) == 2


# ------------------------------------------------------------------- check
def test_check_passes_on_unchanged_baseline(tmp_path):
    root = _git_repo(tmp_path / "repo", {"pkg/paths.py": PATHS_PY})
    base = _bootstrap_baseline(root, tmp_path)
    assert _main(root, tmp_path, "--baseline", str(base), "--check") == 0
    data = json.loads(base.read_text(encoding="utf-8"))
    assert {e["reason"] for e in data.values()} == {REASON}


def test_check_fails_on_new_key(tmp_path):
    root = _git_repo(tmp_path / "repo", {"pkg/paths.py": PATHS_PY})
    base = _bootstrap_baseline(root, tmp_path)
    _write(root, {"pkg/other.py": 'def other(h):\n    return h / "global"\n'})
    _git_add(root)
    assert _main(root, tmp_path, "--baseline", str(base), "--check") == 1


ONE = 'def f(h):\n    return h / "global"\n'
TWO = 'def f(h):\n    a = h / "global"\n    return h / "global"\n'


def test_check_fails_on_rising_count(tmp_path):
    root = _git_repo(tmp_path / "repo", {"pkg/x.py": ONE})
    base = _bootstrap_baseline(root, tmp_path)
    _write(root, {"pkg/x.py": TWO})
    assert _main(root, tmp_path, "--baseline", str(base), "--check") == 1


def test_check_fails_on_falling_count_until_baseline_shrinks(tmp_path, capsys):
    root = _git_repo(tmp_path / "repo", {"pkg/x.py": TWO})
    base = _bootstrap_baseline(root, tmp_path)
    _write(root, {"pkg/x.py": ONE})
    capsys.readouterr()
    assert _main(root, tmp_path, "--baseline", str(base), "--check") == 1
    assert "count fell 2 -> 1" in capsys.readouterr().err
    # Plain regeneration is allowed to shrink, and then the check is green.
    assert _main(root, tmp_path, "--baseline", str(base), "--write-baseline") == 0
    entry = json.loads(base.read_text(encoding="utf-8"))["pkg/x.py::f::h / 'global'"]
    assert entry == {"count": 1, "reason": REASON}
    assert _main(root, tmp_path, "--baseline", str(base), "--check") == 0


def test_check_fails_on_stale_key(tmp_path):
    root = _git_repo(tmp_path / "repo", {"pkg/paths.py": PATHS_PY})
    base = _bootstrap_baseline(root, tmp_path)
    _write(root, {"pkg/paths.py": "X = 1\n"})
    assert _main(root, tmp_path, "--baseline", str(base), "--check") == 1


def test_check_fails_on_empty_reason(tmp_path):
    root = _git_repo(tmp_path / "repo", {"pkg/paths.py": PATHS_PY})
    base = _bootstrap_baseline(root, tmp_path)
    data = json.loads(base.read_text(encoding="utf-8"))
    for entry in data.values():
        entry["reason"] = ""
    base.write_text(json.dumps(data), encoding="utf-8")
    assert _main(root, tmp_path, "--baseline", str(base), "--check") == 1


def test_rename_is_hinted_with_exact_command(tmp_path, capsys):
    root = _git_repo(tmp_path / "repo", {"pkg/x.py": ONE})
    base = _bootstrap_baseline(root, tmp_path, reason="owner: R2 S-6")
    _write(root, {"pkg/x.py": ONE.replace("def f(", "def renamed(")})
    capsys.readouterr()
    assert _main(root, tmp_path, "--baseline", str(base), "--check") == 1
    err = capsys.readouterr().err
    assert "possible rename: pkg/x.py::f::h / 'global'  ->  pkg/x.py::renamed::h / 'global'" in err
    new_key = "pkg/x.py::renamed::h / 'global'"
    assert ("scripts/path_composition_guard.sh --write-baseline --accept "
            f"{shlex.quote(new_key)} --reason 'owner: R2 S-6'") in err
    # The printed command works: accept the new key with the carried reason.
    assert _main(root, tmp_path, "--baseline", str(base), "--write-baseline",
                 "--accept", "pkg/x.py::renamed::h / 'global'", "--reason", "owner: R2 S-6") == 0
    assert json.loads(base.read_text(encoding="utf-8")) == {
        "pkg/x.py::renamed::h / 'global'": {"count": 1, "reason": "owner: R2 S-6"}}


# ------------------------------------------------------------------ ratchet
def test_plain_regeneration_refuses_new_key(tmp_path):
    root = _git_repo(tmp_path / "repo", {"pkg/paths.py": PATHS_PY})
    base = _bootstrap_baseline(root, tmp_path)
    before = base.read_bytes()
    _write(root, {"pkg/other.py": ONE})
    _git_add(root)
    assert _main(root, tmp_path, "--baseline", str(base), "--write-baseline") == 1
    assert base.read_bytes() == before


def test_plain_regeneration_refuses_rising_count(tmp_path):
    root = _git_repo(tmp_path / "repo", {"pkg/x.py": ONE})
    base = _bootstrap_baseline(root, tmp_path)
    before = base.read_bytes()
    _write(root, {"pkg/x.py": TWO})
    assert _main(root, tmp_path, "--baseline", str(base), "--write-baseline") == 1
    assert base.read_bytes() == before


def test_bootstrap_without_accept_is_refused(tmp_path):
    root = _git_repo(tmp_path / "repo", {"pkg/x.py": ONE})
    base = tmp_path / "baseline.json"
    assert _main(root, tmp_path, "--baseline", str(base), "--write-baseline") == 1
    assert not base.exists()


def test_accept_new_key_and_rise_keeps_history(tmp_path):
    root = _git_repo(tmp_path / "repo", {"pkg/x.py": ONE})
    base = _bootstrap_baseline(root, tmp_path)
    _write(root, {"pkg/x.py": TWO, "pkg/y.py": PATHS_PY.replace("chain_path", "y")})
    _git_add(root)
    accept_y = {k: "new in y, owner R2" for k in _scan(root)["counts"] if k.startswith("pkg/y.py")}
    args = ["--baseline", str(base), "--write-baseline",
            "--accept", "pkg/x.py::f::h / 'global'", "--reason", "second use, ticket T-1"]
    for k, r in accept_y.items():
        args += ["--accept", k, "--reason", r]
    assert _main(root, tmp_path, *args) == 0
    data = json.loads(base.read_text(encoding="utf-8"))
    assert data["pkg/x.py::f::h / 'global'"] == {
        "count": 2, "reason": "second use, ticket T-1",
        "history": [{"count": 1, "reason": REASON}]}
    assert all(data[k]["reason"] == "new in y, owner R2" for k in accept_y)
    assert _main(root, tmp_path, "--baseline", str(base), "--check") == 0


def test_accept_with_empty_reason_is_refused(tmp_path):
    root = _git_repo(tmp_path / "repo", {"pkg/x.py": ONE})
    base = tmp_path / "baseline.json"
    assert _main(root, tmp_path, "--baseline", str(base), "--write-baseline",
                 "--accept", "pkg/x.py::f::h / 'global'", "--reason", "  ") == 1
    assert not base.exists()


def test_accept_without_reason_is_a_usage_error(tmp_path):
    root = _git_repo(tmp_path / "repo", {"pkg/x.py": ONE})
    with pytest.raises(SystemExit) as exc:
        _main(root, tmp_path, "--baseline", str(tmp_path / "b.json"), "--write-baseline",
              "--accept", "pkg/x.py::f::h / 'global'")
    assert exc.value.code == 2


# ------------------------------------------------------------- stable keys
def test_key_survives_line_shift_but_not_expression_change(tmp_path):
    root = _git_repo(tmp_path / "repo", {"pkg/paths.py": PATHS_PY})
    key_before = {f["key"] for f in _scan(root)["findings"]}
    _write(root, {"pkg/paths.py": "\n\n\n" + PATHS_PY})
    assert {f["key"] for f in _scan(root)["findings"]} == key_before
    _write(root, {"pkg/paths.py": PATHS_PY.replace('"global"', '"global_v2"')})
    key_changed = {f["key"] for f in _scan(root)["findings"]}
    assert key_changed != key_before
    assert {k for k in key_changed if "global_v2" in k}


# ------------------------------------------------- wiring: the real tree
@pytest.mark.skipif(shutil.which("git") is None or not (REPO / ".git").exists(),
                    reason="needs a git checkout of the repository")
def test_real_tree_matches_committed_baseline():
    """Drift gate in the normal test run: the canonical pipeline over the real
    tracked list must agree with scripts/path_composition_baseline.json."""
    proc = subprocess.run(["bash", str(REPO / "scripts" / "path_composition_guard.sh"), "--check"],
                          cwd=REPO, capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-4000:]
    assert "check OK" in proc.stdout
