"""Checks the learning-system register (core/learning/learning_systems.yaml).

ADR-2281 §2 (PLAN-0964). The register is the list of every learning system with
its production callers. These tests check that the register is well-formed and
that each recorded caller ("path:line:needle") still names its reference: the
needle must be code in the statement of the recorded line, at most 1 line from it,
and a caller must be a call or registration (an import never counts), a reader a
use of the value (review c120 r3_s3-1..3). They do not check that a caller really
runs: that is the job of the E2E tests named in `tests`. Every ref must point into
production code, never into a test file (r4_s3-3, ``_is_test_path``).

Limits of the reader rule (review c120 r4_s3-4), stated rather than hidden - the check
reads tokens, it does not evaluate code:
- Python: ``target = ... needle ...`` counts when a plain-name target (not ``_``, not an
  attribute or subscript store such as ``self._x = v``, not a parenthesised target) occurs
  ANYWHERE later in the file - not "read later in the same function scope": a name reused
  by another function satisfies it. A call, a deciding statement (``if``/``return``/...)
  or a non-registration call argument counts without asking what happens to the result.
- Code that never executes (``if False:``, an uncalled ``def`` or lambda, code after a
  ``return``) passes; so does a needle inside an f-string replacement field's call.
- JS/TS/TSX: comments (including JSX ``{/* */}``) and string/template literals are blanked
  before matching (r4_s3-2), a template's ``${...}`` included; regex literals are not
  recognised, and a quote that is JSX text blanks the rest of its line. ``<Needle`` also
  matches a type argument (``Array<Needle>``). A use is ``needle(`` or ``<Needle``.

Per state: every A has a caller; every B has a caller and an `intake` (the
condition under which its output may act); every D names the decision that
retires it (`retired_by`, an ADR id that must resolve in Corvin-Knowledge) and
still has at least one of its `modules` on disk (the entry leaves the register
with its last module). The core/vibe_engineering classification (ADR-2281 §6)
is checked against the reachability baseline, and the register table in
docs/claude-ref/learning-loop.md must name the same state per system.

ADR-2303: every B entry carries a ``signal_contract``; a proxy (``proxy_of``
not ``none``) never has a ``target``; ``consumer`` is ``none`` or a resolving
``path:line:needle``; ``validity`` is numbers or ``unmeasured``. Form only, not truth.
"""

import io
import json
import keyword
import os
import re
import tokenize
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
REGISTER = REPO / "core" / "learning" / "learning_systems.yaml"
REACH_BASELINE = REPO / "scripts" / "learning_reachability_baseline.json"
STATES = {"A", "B", "D"}
ADR_ID = re.compile(r"^ADR-\d{4}$")


def _decisions_dir() -> Path | None:
    """The Corvin-Knowledge decisions directory, or None when no checkout is
    reachable: CORVIN_KB_REPO, a ``Corvin-Knowledge`` sibling of the repo or of
    any parent (a worktree lives below the main checkout), then the
    ``corvin_decisions`` submodule."""
    cands = []
    if os.environ.get("CORVIN_KB_REPO"):
        cands.append(Path(os.environ["CORVIN_KB_REPO"]) / "decisions")
    cands += [p / "Corvin-Knowledge" / "decisions" for p in REPO.parents]
    cands.append(REPO / "corvin_decisions" / "decisions")
    for c in cands:
        if c.is_dir() and any(c.glob("ADR-*.md")):
            return c
    return None


def _load() -> list[dict]:
    data = yaml.safe_load(REGISTER.read_text(encoding="utf-8"))
    return data["systems"]


# "path:line:needle" - the needle is a token of the referenced call/import/registration.
_REF = re.compile(r"^(?P<path>[\w./-]+\.\w+):(?P<line>\d+):(?P<needle>\S.*)$")
# The needle must sit in the statement of the recorded line and at most 1 line from it
# (review c120 r3_s3-2: with +/-3 a ref could drift 3 lines or swap to a neighbour call).
NEEDLE_WINDOW = 1


def _callers() -> list[tuple[str, str]]:
    return [(system["id"], ref) for system in _load() for ref in system.get("callers", [])]


_FILE_CACHE: dict[Path, list[str]] = {}


def _lines(target: Path) -> list[str]:
    if target not in _FILE_CACHE:
        _FILE_CACHE[target] = target.read_text(encoding="utf-8", errors="replace").splitlines()
    return _FILE_CACHE[target]


MIN_NEEDLE_CHARS = 4   # a needle of a letter or two matches everywhere (review c120 r2_s3-2)
_SKIP_TOKENS = {tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT,
                tokenize.DEDENT, tokenize.ENCODING, tokenize.ENDMARKER}
_PY_TOKEN_CACHE: dict[Path, list[tuple[str, int, int]]] = {}


def _py_code_tokens(target: Path) -> list[tuple[str, int, int]]:
    """(text, line, logical-line index) of every code token of a Python file. Comments,
    blank lines and the bodies of strings are not code: a name inside a comment or a
    docstring is part of a COMMENT/STRING token and never becomes a NAME token here."""
    if target not in _PY_TOKEN_CACHE:
        out, logical = [], 0
        with open(target, encoding="utf-8", errors="replace") as fh:
            for tok in tokenize.generate_tokens(fh.readline):
                if tok.type == tokenize.NEWLINE:
                    logical += 1
                if tok.type in _SKIP_TOKENS:
                    continue
                out.append((tok.string, tok.start[0], logical))
        _PY_TOKEN_CACHE[target] = out
    return _PY_TOKEN_CACHE[target]


def _needle_tokens(needle: str) -> list[tuple[int, str]]:
    """Tokens of a needle such as ``optimizer.get_stats``, ``admit(`` or
    ``_run(repo, "guidance"`` (an unclosed bracket is fine: the tokens before it count)."""
    toks: list[tuple[int, str]] = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(needle).readline):
            if tok.type not in _SKIP_TOKENS:
                toks.append((tok.type, tok.string))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        pass
    return toks


# A call whose argument is a route/handler registration, not a read of its value.
_REGISTRATION_CALLS = {"include_router", "add_api_route", "add_route", "mount",
                       "add_middleware", "add_event_handler"}
# A logical line opening with one of these consumes the values it names.
_DECIDING_KEYWORDS = {"if", "elif", "while", "assert", "return", "yield", "for", "with"}


def _logical_tokens(toks: list[tuple[str, int, int]], logical: int) -> list[int]:
    return [x for x, t in enumerate(toks) if t[2] == logical]


def _enclosing_open(toks: list[tuple[str, int, int]], i: int) -> int | None:
    """Index of the innermost bracket still open at token ``i`` (same logical line), or None."""
    depth, logical = 0, toks[i][2]
    for back in range(i - 1, -1, -1):
        if toks[back][2] != logical:
            return None
        text = toks[back][0]
        if text in (")", "]", "}"):
            depth += 1
        elif text in ("(", "[", "{"):
            if depth == 0:
                return back
            depth -= 1
    return None


def _call_name(toks: list[tuple[str, int, int]], open_idx: int) -> str | None:
    """The called name when the ``(`` at ``open_idx`` opens a call's arguments, else None
    (a grouping parenthesis, a tuple, a list, a dict)."""
    if toks[open_idx][0] != "(" or open_idx == 0 or toks[open_idx - 1][2] != toks[open_idx][2]:
        return None
    before = toks[open_idx - 1][0]
    if before.isidentifier() and not keyword.iskeyword(before):
        return before
    return ")" if before in (")", "]") else None


def _is_call(toks: list[tuple[str, int, int]], i: int, k: int) -> bool:
    """The needle spells a call (``admit(``, ``_run(repo, "guidance"``) or is followed by
    one (``needle(``, ``needle.attr(``)."""
    j = i + k
    if any(toks[x][0] == "(" and toks[x - 1][0].isidentifier()
           and not keyword.iskeyword(toks[x - 1][0]) for x in range(i + 1, j)):
        return True
    while j + 1 < len(toks) and toks[j][0] == "." and toks[j + 1][0].isidentifier():
        j += 2
    return j < len(toks) and toks[j][0] == "("


def _is_import_or_definition(toks: list[tuple[str, int, int]], i: int) -> str | None:
    logical = toks[i][2]
    line = _logical_tokens(toks, logical)
    if line and toks[line[0]][0] in ("import", "from"):
        return "import"
    if i and toks[i - 1][0] in ("def", "class"):
        prev = _logical_tokens(toks, logical - 1)
        return "decorated" if prev and toks[prev[0]][0] == "@" else "definition"
    return None


def _is_call_or_registration(toks: list[tuple[str, int, int]], i: int, k: int, line: int) -> bool:
    """A caller: the needle tokens ``toks[i:i+k]`` are the statement's own call
    (``needle(`` / ``needle.attr(``), an argument of a call (``include_router(needle.router)``,
    ``target=needle``) or a decorated definition (``@router.get(...)``). An IMPORT never
    proves a caller (review c120 r3_s3-1): deleting the call and keeping its import fails.
    A call nested in another call's arguments counts only on the cited line itself and
    only when the outer call opens on another line (``learning_loops=needle(manifest),``):
    so re-pointing ``record(...)`` at its argument ``meta.get(...)`` fails (r3_s3-2)."""
    kind = _is_import_or_definition(toks, i)
    if kind is not None:
        return kind == "decorated"
    enclosing = _enclosing_open(toks, i)
    in_call = enclosing is not None and _call_name(toks, enclosing) is not None
    if _is_call(toks, i, k):
        return not in_call or (toks[i][1] == line and toks[enclosing][1] != line)
    return in_call


def _is_used_read(toks: list[tuple[str, int, int]], i: int, k: int, line: int = 0) -> bool:
    """A reader/consumer: the needle's VALUE is used (review c120 r3_s3-3) - it is called,
    it sits in a deciding statement (if/while/return/for/...), it is an argument of a call
    that is not a route registration, or it is assigned to a name that is read later.
    Naming it in a bare expression, a discarded tuple, an import or a definition is no read."""
    if _is_import_or_definition(toks, i) is not None:
        return False
    enclosing = _enclosing_open(toks, i)
    name = _call_name(toks, enclosing) if enclosing is not None else None
    if name in _REGISTRATION_CALLS:
        return False
    if _is_call(toks, i, k):
        return True
    line = _logical_tokens(toks, toks[i][2])
    if toks[line[0]][0] in _DECIDING_KEYWORDS:
        return True
    if name is not None:
        return True
    # ``target = ... needle ...``: the target must be read after this statement
    eq = [x for x in line if toks[x][0] == "=" and x < i and _enclosing_open(toks, x) is None]
    if eq:
        # review c120 r4_s3-4: only plain names count as targets - not the base of an
        # attribute or subscript store (``self._x = v`` passed because ``self`` recurs),
        # not an attribute name, not the discard ``_``.
        tgt = [x for x in line if x < eq[0]]
        targets = {toks[x][0] for x in tgt if toks[x][0].isidentifier()
                   and not keyword.iskeyword(toks[x][0]) and toks[x][0] != "_"
                   and toks[x - 1][0] != "." and toks[x + 1][0] not in (".", "[")
                   and _enclosing_open(toks, x) is None}
        later = {t[0] for t in toks[line[-1] + 1:]}
        return bool(targets & later)
    return False


_JS_CODE_CACHE: dict[Path, list[str]] = {}


def _js_code_lines(text: str) -> list[str]:
    """The lines of a JS/TS/TSX source with every comment and literal blanked out (review
    c120 r4_s3-2): ``// ...``, ``/* ... */`` (so also a JSX comment ``{/* <X /> */}``), and
    the bodies of '...', "..." and `...` literals (a template's ``${...}`` included) become
    spaces; line breaks are kept, so line numbers do not move. A quote string ends at its
    line's end (JS strings do not span lines), which bounds the damage of a quote that is
    really JSX text (``<p>Don't</p>``) to the rest of that line. Regex literals are not
    recognised."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c == "/" and text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
        elif c == "/" and text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
        elif c in "'\"`":
            j = i + 1
            while j < n and text[j] != c and (c == "`" or text[j] != "\n"):
                j += 2 if text[j] == "\\" else 1
            j = min(n, j + 1) if j < n and text[j] == c else j
        else:
            out.append(c)
            i += 1
            continue
        out.append(re.sub(r"[^\n]", " ", text[i:j]))
        i = j
    return "".join(out).splitlines()


def _js_violation(target: Path, line: int, needle: str, kind: str) -> bool:
    """True when no code line in the window calls ``needle`` (``needle(``) or renders it
    (``<Needle``). An import line never counts (review c120 r3_s3-1), for any kind; a
    comment, a JSX comment or a string/template literal never counts (r4_s3-2)."""
    if target not in _JS_CODE_CACHE:
        _JS_CODE_CACHE[target] = _js_code_lines(target.read_text(encoding="utf-8", errors="replace"))
    lines = _JS_CODE_CACHE[target]
    use = re.compile(r"<" + re.escape(needle) + r"\b|(?<![\w$])" + re.escape(needle) + r"\s*\(")
    for code in lines[max(0, line - 1 - NEEDLE_WINDOW): line + NEEDLE_WINDOW]:
        if re.match(r"\s*import\b", code):
            continue
        if use.search(code):
            return False
    return True


# review c120 r4_s3-3: callers, readers and consumers are PRODUCTION code - a test's call
# proves nothing about the running system (the e2e-wiring-proof rule: "outside test files").
_TEST_DIRS = {"tests", "test", "__tests__", "e2e-tests"}
_TEST_FILE = re.compile(r"^(test_.*|.*_test|conftest|.*\.(test|spec))\.\w+$")


def _is_test_path(rel: str) -> bool:
    parts = Path(rel).parts
    return any(p in _TEST_DIRS for p in parts[:-1]) or bool(_TEST_FILE.match(parts[-1]))


def _ref_violation(ref: str, kind: str = "caller", root: Path = REPO) -> str | None:
    """Why ``ref`` ("path:line:needle") does not point at its reference, or None.

    The needle (at least MIN_NEEDLE_CHARS long, starting with an identifier) must occur as
    CODE tokens - never inside a comment, a docstring or another string - in the statement
    (logical line) of the cited line and at most NEEDLE_WINDOW lines from it. For a
    ``caller`` it must be the statement's call, an argument of a call (a registration) or
    a decorated definition - never an import (review c120 r3_s3-1). For a reader/consumer
    its value must be used (``_is_used_read``, r3_s3-3). So a reference that drifted by 2
    lines or onto a comment fails, a call turned into a comment fails, a deleted call whose
    import or name survives nearby fails (r2_s3-2, r3_s3-1), a needle re-pointed at a
    neighbouring call fails (r3_s3-2), and a reader that only names the value fails."""
    m = _REF.match(str(ref))
    if not m:
        return f"{ref!r} is not path:line:needle"
    if _is_test_path(m["path"]):
        return f"{m['path']} is a test file: a {kind} must be production code"
    target = root / m["path"]
    if not target.is_file():
        return f"{m['path']} does not exist"
    lines, line, needle = _lines(target), int(m["line"]), m["needle"].strip()
    if not 1 <= line <= len(lines):
        return f"{m['path']}:{line} is past the end of the file ({len(lines)} lines)"
    if len(re.sub(r"\s", "", needle)) < MIN_NEEDLE_CHARS:
        return f"{ref!r}: the needle {needle!r} is shorter than {MIN_NEEDLE_CHARS} characters"
    if target.suffix != ".py":
        if not re.fullmatch(r"[A-Za-z_$][\w$]*", needle):
            return f"{ref!r}: a non-Python needle is one identifier"
        if _js_violation(target, line, needle, kind):
            return (f"{m['path']}:{line} has no {needle!r} called or rendered within "
                    f"{NEEDLE_WINDOW} line(s)")
        return None
    ntoks = _needle_tokens(needle)
    if not ntoks or ntoks[0][0] != tokenize.NAME or keyword.iskeyword(ntoks[0][1]):
        return f"{ref!r}: the needle must start with an identifier"
    want = [s for _, s in ntoks]
    try:
        toks, k = _py_code_tokens(target), len(want)
    except (tokenize.TokenError, IndentationError, SyntaxError) as exc:
        return f"{m['path']} does not tokenize: {exc}"
    cited = {t[2] for t in toks if t[1] == line}
    if not cited:
        return f"{m['path']}:{line} has no code (blank, comment or inside a string)"
    starts = [i for i in range(len(toks) - k + 1)
              if [t[0] for t in toks[i:i + k]] == want]
    # the needle belongs to the statement on the cited line, at most NEEDLE_WINDOW lines off
    near = [i for i in starts if abs(toks[i][1] - line) <= NEEDLE_WINDOW and toks[i][2] in cited]
    check = _is_call_or_registration if kind == "caller" else _is_used_read
    near = [i for i in near if check(toks, i, k, line)]
    if not near:
        hits = sorted({toks[i][1] for i in starts})[:5]
        what = "call or registration of" if kind == "caller" else "used read of"
        return (f"{m['path']}:{line} has no {what} {needle!r} in its statement within "
                f"{NEEDLE_WINDOW} line(s) (the needle is code at lines {hits or 'nowhere'})")
    return None


def test_register_exists_and_parses():
    assert REGISTER.is_file()
    assert _load(), "register has no systems"


def test_ids_are_unique_and_states_valid():
    systems = _load()
    ids = [s["id"] for s in systems]
    assert len(ids) == len(set(ids)), "duplicate system id"
    for s in systems:
        assert s["state"] in STATES, f"{s['id']}: unknown state {s['state']!r}"
        assert s.get("name"), f"{s['id']}: missing name"


def _contract_rows(s: dict) -> list[dict]:
    c = s.get("signal_contract")
    return [r for r in (c if isinstance(c, list) else [c] if c else []) if isinstance(r, dict)]


def _state_violations(systems: list[dict]) -> list[str]:
    """State A means a caller runs it AND something reads its output (review c120 r2_s3-3):
    an A entry names a reader - a ``readers`` reference or a signal_contract whose consumer
    is not ``none``. An entry promoted to A because of its contract (``promoted_from``)
    keeps that contract."""
    out = []
    for s in systems:
        if s["state"] != "A":
            continue
        if not s.get("callers"):
            out.append(f"{s['id']} is A but lists no caller")
        consumers = [r for r in _contract_rows(s) if str(r.get("consumer") or "none") != "none"]
        if not s.get("readers") and not consumers:
            out.append(f"{s['id']} is A but names no reader (readers, or a signal_contract consumer)")
        if s.get("promoted_from") and not _contract_rows(s):
            out.append(f"{s['id']} was promoted from {s['promoted_from']} and lost its signal_contract")
        # review c120 r3_s3-3: an output that may act only under an intake condition is not
        # live while that condition is unmet - an A entry states no open intake, and a
        # recorded readiness measurement must say ready on every surface.
        if str(s.get("intake") or "").strip():
            out.append(f"{s['id']} is A but still states an intake (the condition under which "
                       f"its output may act); promotion records that the intake holds and drops it")
        measured = s.get("measured")
        if isinstance(measured, dict):
            unmet = [k for k in ("bridge", "console")
                     if isinstance(measured.get(k), dict) and measured[k].get("ready") is not True]
            if unmet:
                out.append(f"{s['id']} is A but its recorded readiness is not met on: {', '.join(unmet)}")
    return out


def test_live_systems_have_a_caller_and_a_reader():
    systems = _load()
    assert _state_violations(systems) == []


def test_state_check_refuses_a_without_reader_and_a_dropped_contract():
    """Negative controls on copies of the real register (the file is not touched)."""
    import copy

    systems = _load()
    promoted = {s["id"] for s in systems if s.get("promoted_from")}
    assert {"L08", "L19"} <= promoted, "positive control: L08 and L19 record their promotion"
    for sys_id in sorted(promoted):
        broken = copy.deepcopy(systems)
        next(s for s in broken if s["id"] == sys_id).pop("signal_contract")
        assert f"{sys_id} was promoted from B and lost its signal_contract" in _state_violations(broken)
    broken = copy.deepcopy(systems)
    l13 = next(s for s in broken if s["id"] == "L13")
    assert l13["state"] == "B" and _contract_rows(l13)[0]["consumer"] == "none", "positive control"
    l13["state"] = "A"
    assert "L13 is A but names no reader (readers, or a signal_contract consumer)" in _state_violations(broken)
    broken = copy.deepcopy(systems)
    l06 = next(s for s in broken if s["id"] == "L06")
    assert l06.get("readers"), "positive control: L06 names its reader"
    l06.pop("readers")
    assert any(v.startswith("L06 is A but names no reader") for v in _state_violations(broken))


@pytest.mark.parametrize(("sys_id", "ref"), _callers())
def test_caller_exists_at_recorded_line(sys_id, ref):
    assert _ref_violation(ref) is None, f"{sys_id}: {_ref_violation(ref)}"


def _readers() -> list[tuple[str, str]]:
    return [(system["id"], ref) for system in _load() for ref in system.get("readers", [])]


@pytest.mark.parametrize(("sys_id", "ref"), _readers())
def test_reader_exists_at_recorded_line(sys_id, ref):
    why = _ref_violation(ref, kind="reader")
    assert why is None, f"{sys_id}: {why}"


def _mutated_copy(tmp_path: Path, rel: str, old: str, new: str) -> Path:
    """A copy of ``rel`` under ``tmp_path`` with ``old`` replaced by ``new`` (once)."""
    src = (REPO / rel).read_text(encoding="utf-8")
    assert src.count(old) == 1, f"positive control: {old!r} occurs once in {rel}"
    dst = tmp_path / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(src.replace(old, new), encoding="utf-8")
    return tmp_path


def _ref_of(sys_id: str, needle: str, key: str = "callers") -> str:
    return next(r for r in next(s for s in _load() if s["id"] == sys_id).get(key, [])
                if r.endswith(":" + needle))


@pytest.mark.parametrize(("sys_id", "needle", "key", "rel", "old", "new"), [
    # (a) the L05 reader turned into a comment that still names it
    ("L05", "_learned_threshold", "callers", "core/skills/os_skills/delegation_router.py",
     "    threshold = _learned_threshold(tenant_id)\n",
     "    threshold = None  # _learned_threshold(tenant_id) removed\n"),
    # (b) the L19 scheduler start removed, its name left in a comment
    ("L19", "_auto.start_scheduler", "callers", "core/console/corvin_console/routes/autonomous_forge_routes.py",
     "    _auto.start_scheduler(_tenant_roots)\n", "    pass  # _auto.start_scheduler(_tenant_roots)\n"),
    # (c) the L15 guidance call deleted; "guidance" survives in out["guidance"] one line below
    ("L15", '_run(repo, "guidance", "run"', "callers", "core/console/corvin_console/kb_projection.py",
     '        rc, g = _run(repo, "guidance", "run", actor="kb-guidance")\n', "        rc, g = 0, {}\n"),
    # a reader moved into a docstring-like string
    ("L06", "spec.n_grades", "readers", "corvin_operator/bridges/shared/skill_inject.py",
     "                if spec.n_grades < 1 or spec.mean_score <= 0:\n                    continue\n",
     '                if "spec.n_grades < 1":\n                    continue\n'),
])
def test_ref_check_fails_when_the_call_is_gone(tmp_path, sys_id, needle, key, rel, old, new):
    """Review c120 r2_s3-2 mutants, on copies (the tree is not touched): each must turn red,
    and the unmutated copy must stay green (positive control)."""
    ref = _ref_of(sys_id, needle, key)
    kind = "caller" if key == "callers" else "reader"
    clean = _mutated_copy(tmp_path / "clean", rel, old, old)
    assert _ref_violation(ref, kind=kind, root=clean) is None, "positive control"
    mutant = _mutated_copy(tmp_path / "mutant", rel, old, new)
    assert "has no" in (_ref_violation(ref, kind=kind, root=mutant) or ""), f"{sys_id}: mutant passed"


def test_ref_check_refuses_short_and_non_identifier_needles():
    ref = _ref_of("L05", "_learned_threshold")
    path, line = ref.split(":")[:2]
    assert "shorter than" in (_ref_violation(f"{path}:{line}:t") or "")
    assert "start with an identifier" in (_ref_violation(f'{path}:{line}:"threshold"') or "")


def test_caller_check_fails_on_a_shifted_line_and_a_non_existent_needle():
    """Negative control on a copy of a real reference (the register is not touched):
    the same needle cited 40 lines off, or a needle that exists nowhere, fails."""
    sys_id, ref = next((s, r) for s, r in _callers() if s == "L05")
    m = _REF.match(ref)
    assert m and _ref_violation(ref) is None, "positive control: the real L05 reference resolves"
    shifted = f"{m['path']}:{int(m['line']) + 40}:{m['needle']}"
    assert "has no" in (_ref_violation(shifted) or ""), f"{sys_id}: a shifted line passed"
    wrong = f"{m['path']}:{m['line']}:no_such_symbol_anywhere_here"
    assert "has no" in (_ref_violation(wrong) or "")
    assert "is not path:line:needle" in (_ref_violation(f"{m['path']}:{m['line']}") or "")


def _all_refs() -> list[tuple[str, str, str]]:
    """(system, kind, ref) of every caller, reader and contract consumer."""
    out = []
    for s in _load():
        out += [(s["id"], "caller", r) for r in s.get("callers", [])]
        out += [(s["id"], "reader", r) for r in s.get("readers", [])]
        out += [(s["id"], "consumer", str(r["consumer"])) for r in _contract_rows(s)
                if str(r.get("consumer") or "none") != "none"]
    return out


@pytest.mark.parametrize("delta", [2, -2, 3, -3])
def test_every_ref_shifted_by_two_or_more_lines_fails(delta):
    """Review c120 r3_s3-2: with the old +/-3 window all 75 refs stayed green shifted by 2."""
    refs = _all_refs()
    assert len(refs) > 50, "positive control: the register lists its references"
    green = []
    for sys_id, kind, ref in refs:
        m = _REF.match(ref)
        assert _ref_violation(ref, kind=kind) is None, f"positive control: {ref}"
        if _ref_violation(f"{m['path']}:{int(m['line']) + delta}:{m['needle']}", kind=kind) is None:
            green.append(ref)
    assert green == [], f"shifted by {delta} and still green: {green}"


@pytest.mark.parametrize(("real", "swapped"), [
    # the needle re-pointed at a REAL call next to it (review c120 r3_s3-2)
    ("core/console/corvin_console/routes/chat.py:772:grade_followup", "register_live_turn"),
    ("corvin_operator/bridges/shared/adapter.py:9916:record_turn_outcome", "meta.get"),
    ("core/console/corvin_console/app.py:295:autonomous_forge_route", "monitoring_route"),
])
def test_a_needle_swapped_to_a_neighbouring_real_call_fails(real, swapped):
    m = _REF.match(real)
    assert real in {r for _, _, r in _all_refs()}, f"positive control: {real} is in the register"
    assert _ref_violation(real) is None, "positive control: the real reference resolves"
    lines = _lines(REPO / m["path"])
    near = "\n".join(lines[int(m["line"]) - 3: int(m["line"]) + 2])
    assert swapped in near, f"positive control: {swapped!r} is real code next to the reference"
    assert "has no" in (_ref_violation(f"{m['path']}:{m['line']}:{swapped}") or "")


def _delete_statement(tmp_path: Path, ref: str) -> Path:
    """A copy of the ref's file with the WHOLE statement starting at the cited line
    replaced by ``pass`` (its import above it stays)."""
    import ast

    m = _REF.match(ref)
    rel, line = m["path"], int(m["line"])
    text = (REPO / rel).read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    stmt = next(n for n in ast.walk(ast.parse(text)) if isinstance(n, ast.stmt) and n.lineno == line)
    indent = lines[line - 1][: len(lines[line - 1]) - len(lines[line - 1].lstrip())]
    dst = tmp_path / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text("".join(lines[: line - 1] + [indent + "pass  # call removed\n"]
                           + lines[stmt.end_lineno:]), encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize("needle", [
    # review c120 r3_s3-1: each call has its own ``from x import needle`` 1-3 lines above
    "chat_runtime.py:4627:route_and_record",
    "chat_runtime.py:4727:record_turn_outcome",
    "adapter.py:9916:record_turn_outcome",
    "adapter.py:10010:resolve_delegation_route",
    "orchestrator.py:211:emit_review_confidence",
])
def test_a_deleted_call_whose_import_survives_fails(tmp_path, needle):
    ref = next(r for _, k, r in _all_refs() if k == "caller" and r.endswith(needle))
    m = _REF.match(ref)
    imported = m["needle"].split(".")[-1]
    window = _lines(REPO / m["path"])[int(m["line"]) - 5: int(m["line"])]
    assert any("import" in w and imported in w for w in window), "positive control: an import sits above"
    assert _ref_violation(ref) is None, "positive control"
    mutant = _delete_statement(tmp_path, ref)
    assert "has no" in (_ref_violation(ref, root=mutant) or ""), f"{ref}: deleting the call stayed green"


@pytest.mark.parametrize(("ref", "kind", "rel", "old", "new"), [
    # review c120 r3_s3-3 (a): the L06 injection gate removed, its names read into a dead tuple
    ("L06", "reader", "corvin_operator/bridges/shared/skill_inject.py",
     "                if spec.n_grades < 1 or spec.mean_score <= 0:\n                    continue\n",
     "                _log_grades = (spec.n_grades, spec.mean_score)\n"),
    # (b) the L12 readiness gate bypassed, the name left as a bare expression
    ("L12", "consumer", "core/skills/os_skills/monitoring/dual_write.py",
     "    verdict = readiness.cached_evaluate(tenant_id, surface)\n",
     "    readiness.cached_evaluate  # noqa: the gate is no longer consulted\n"
     "    verdict = readiness.Readiness(True, 'ready')\n"),
])
def test_a_reader_that_no_longer_uses_the_value_fails(tmp_path, ref, kind, rel, old, new):
    s = next(x for x in _load() if x["id"] == ref)
    refs = s.get("readers", []) if kind == "reader" else [str(r["consumer"]) for r in _contract_rows(s)]
    real = next(r for r in refs if r.startswith(rel + ":"))
    clean = _mutated_copy(tmp_path / "clean", rel, old, old)
    assert _ref_violation(real, kind=kind, root=clean) is None, "positive control"
    mutant = _mutated_copy(tmp_path / "mutant", rel, old, new)
    assert "has no" in (_ref_violation(real, kind=kind, root=mutant) or ""), f"{ref}: mutant passed"


@pytest.mark.parametrize(("ref", "kind"), [
    # the pre-r3 L01/L11 refs: an import line, the call one line below it
    ("core/console/corvin_console/standalone.py:376:learning_host", "caller"),
    ("corvin_operator/bridges/shared/spawn_gates.py:454:flow_guard_skill", "caller"),
    ("corvin_operator/bridges/shared/spawn_gates.py:454:shadow_compare", "reader"),
])
def test_an_import_line_never_proves_a_caller_or_reader(ref, kind):
    """Review c120 r3_s3-1: an import is not a call."""
    m = _REF.match(ref)
    assert re.match(r"\s*from .* import ", _lines(REPO / m["path"])[int(m["line"]) - 1]), (
        "positive control: the cited line is an import")
    assert "has no" in (_ref_violation(ref, kind=kind) or "")


def test_a_route_registration_is_no_reader():
    """Review c120 r3_s3-3 (c): L19 naming its router registration as its reader."""
    import copy

    reg = "core/console/corvin_console/app.py:295:autonomous_forge_route"
    assert _ref_violation(reg) is None, "positive control: it is a valid CALLER (a registration)"
    assert "has no" in (_ref_violation(reg, kind="reader") or "")
    broken = copy.deepcopy(_load())
    l19 = next(s for s in broken if s["id"] == "L19")
    l19.pop("promoted_from"), l19.pop("signal_contract")
    l19["readers"] = [reg]
    assert [r for r in l19["readers"] if _ref_violation(r, kind="reader") is not None] == [reg]


L18_TSX = "core/console/corvin_console/web-next/src/pages/video-producer.tsx"
L18_LINE = "      <LearningOverview jobId={job.id} csrf={csrf} />\n"


@pytest.mark.parametrize("new", [
    # review c120 r4_s3-2: the usual way to unrender a JSX element, and its relatives
    "      {/* <LearningOverview jobId={job.id} csrf={csrf} /> */}\n",
    "      {null /* <LearningOverview jobId={job.id} csrf={csrf} /> */}\n",
    "      {'<LearningOverview jobId={job.id} />'}\n",
    "      {\"LearningOverview(job)\"}\n",
    "      {`${job.id} <LearningOverview />`}\n",
    "      // <LearningOverview jobId={job.id} csrf={csrf} />\n",
])
def test_a_tsx_reader_inside_a_comment_or_literal_fails(tmp_path, new):
    """On a COPY of the L18 reader's file (the tree is not touched)."""
    ref = next(r for _, _, r in _all_refs() if r.startswith(L18_TSX + ":"))
    clean = _mutated_copy(tmp_path / "clean", L18_TSX, L18_LINE, L18_LINE)
    assert _ref_violation(ref, kind="reader", root=clean) is None, "positive control"
    mutant = _mutated_copy(tmp_path / "mutant", L18_TSX, L18_LINE, new)
    assert "has no" in (_ref_violation(ref, kind="reader", root=mutant) or ""), new


def test_js_masking_keeps_code_after_a_literal_and_line_numbers():
    src = ("const a = 'x // y'; useLoops();\n"
           "/* one\n two */ render(<Panel />)\n"
           "const t = `a\nb ${useLoops()}`; useMaturity()\n")
    code = _js_code_lines(src)
    assert len(code) == len(src.splitlines()), "line numbers do not move"
    assert "useLoops()" in code[0] and "//" not in code[0] and "x" not in code[0]
    assert "one" not in code[1] and "<Panel" in code[2] and "two" not in code[2]
    assert "useLoops" not in code[4] and "useMaturity()" in code[4]


@pytest.mark.parametrize(("path", "is_test"), [
    ("tests/skills/test_l5_routing_loop.py", True),
    ("core/console/tests/helpers.py", True),
    ("core/plugins/corvin_plugins/test_util.py", True),
    ("corvin_operator/bridges/shared/adapter_test.py", True),
    ("core/console/conftest.py", True),
    ("core/console/corvin_console/web-next/src/lib/x.test.tsx", True),
    ("core/console/corvin_console/web-next/src/__tests__/x.tsx", True),
    ("core/learning/outcome_sink.py", False),
    ("core/console/corvin_console/web-next/src/pages/video-producer.tsx", False),
    ("core/skills/os_skills/contest_rules.py", False),
])
def test_test_path_detection(path, is_test):
    assert _is_test_path(path) is is_test


def test_a_ref_into_a_test_file_never_proves_a_caller_or_reader():
    """Review c120 r4_s3-3: an A entry kept live by a test's call. Copies only."""
    import copy

    test_ref = "tests/skills/test_l5_routing_loop.py:923:dp.route_and_record"
    assert "route_and_record" in _lines(REPO / "tests/skills/test_l5_routing_loop.py")[922], (
        "positive control: the cited test line really calls it")
    for kind in ("caller", "reader", "consumer"):
        assert "is a test file" in (_ref_violation(test_ref, kind=kind) or ""), kind
    assert [r for _, _, r in _all_refs() if _is_test_path(_REF.match(r)["path"])] == [], (
        "the register cites a test file")
    broken = copy.deepcopy(_load())
    l06 = next(s for s in broken if s["id"] == "L06")
    assert l06["state"] == "A", "positive control"
    l06["callers"] = l06["readers"] = [test_ref]
    assert [r for r in l06["callers"] + l06["readers"] if _ref_violation(r) is None
            or _ref_violation(r, kind="reader") is None] == []


@pytest.mark.parametrize(("stmt", "used"), [
    # review c120 r4_s3-4: only a plain name read later is a used read
    ("    self._never_read = st.confidence_threshold\n    return self.other\n", False),
    ("    _ = st.confidence_threshold\n    return _\n", False),
    ("    cache[key] = st.confidence_threshold\n    return cache\n", False),
    ("    limit = st.confidence_threshold\n    return limit\n", True),
    ("    limit = st.confidence_threshold\n    return 0\n", False),
])
def test_an_assignment_reader_needs_a_plain_name_read_later(tmp_path, stmt, used):
    f = tmp_path / "core" / "mod.py"
    f.parent.mkdir(parents=True)
    f.write_text("def f(st, cache, key):\n" + stmt, encoding="utf-8")
    why = _ref_violation("core/mod.py:2:st.confidence_threshold", kind="reader", root=tmp_path)
    assert (why is None) is used, why


@pytest.mark.parametrize("sys_id", ["L05", "L12"])
def test_a_gated_router_entry_cannot_be_promoted_to_a(sys_id):
    """Review c120 r3_s3-3: L05/L12 act only in the refused Phase 2 (intake unmet, readiness
    not ready); flipping the state alone - or also dropping the intake - must fail."""
    import copy

    broken = copy.deepcopy(_load())
    s = next(x for x in broken if x["id"] == sys_id)
    assert s["state"] == "B" and s.get("intake"), "positive control"
    s["state"] = "A"
    found = _state_violations(broken)
    assert any(v.startswith(f"{sys_id} is A but still states an intake") for v in found), found
    s.pop("intake")
    assert any(v.startswith(f"{sys_id} is A but its recorded readiness is not met") for v in _state_violations(broken))


@pytest.mark.parametrize("value", [False, True, None, 0, ""])
def test_proxy_of_must_be_a_string(value):
    """Review c120 r3_s3-4: YAML ``proxy_of: no`` loads as False and skipped the proxy checks."""
    import copy

    assert yaml.safe_load("proxy_of: no")["proxy_of"] is False, "positive control: YAML 1.1"
    broken = copy.deepcopy(_load())
    row = next(s for s in broken if s["id"] == "L08")["signal_contract"]
    assert isinstance(row["proxy_of"], str) and row["proxy_of"] != "none", "positive control"
    row.update(proxy_of=value, target="model_tier_choice", writes="model_tier_choice")
    found = _contract_violations(broken)
    assert any(v.startswith("L08") and ("proxy_of must be a non-empty string" in v
                                        or "lacks 'proxy_of'" in v) for v in found), found


@pytest.mark.parametrize(
    ("sys_id", "test_path"),
    [(s["id"], t) for s in _load() for t in s.get("tests", [])],
)
def test_listed_test_file_exists(sys_id, test_path):
    assert (REPO / test_path).is_file(), f"{sys_id}: test file missing: {test_path}"


def test_shadow_systems_state_their_intake():
    for s in _load():
        if s["state"] == "B":
            assert s.get("callers"), f"{s['id']} is B but lists no caller"
            assert str(s.get("intake") or "").strip(), (
                f"{s['id']} is B but has no intake: state when its output may act")


CONTRACT_KEYS = ("signal", "proxy_of", "validity", "consumer", "target", "guard", "control", "rollback")
STRING_CONTRACT_KEYS = ("proxy_of", "target", "consumer")


def _contract_violations(systems: list[dict]) -> list[str]:
    """ADR-2303: every B entry carries a ``signal_contract`` (A may); a proxy is never a
    target; ``validity`` is numbers or ``unmeasured``; ``consumer`` is ``none`` or a
    ``path:line:needle`` that resolves like a caller; a signal that is not a proxy and
    ``writes`` a parameter names it as its ``target``. Returns one message per violation."""
    out: list[str] = []
    for s in systems:
        contract = s.get("signal_contract")
        if contract is None:
            if s["state"] == "B":
                out.append(f"{s['id']} is B but has no signal_contract")
            continue
        entries = contract if isinstance(contract, list) else [contract]
        if not entries or not all(isinstance(e, dict) for e in entries):
            out.append(f"{s['id']}: signal_contract must be a mapping or a non-empty list of mappings")
            continue
        for e in entries:
            where = f"{s['id']}" + (f"/{e['skill']}" if e.get("skill") else "")
            if isinstance(contract, list) and not str(e.get("skill") or "").strip():
                out.append(f"{where}: a contract in a list names its skill")
            for key in CONTRACT_KEYS:
                if e.get(key) is None or str(e.get(key)).strip() == "":
                    out.append(f"{where}: signal_contract lacks {key!r}")
            for key in STRING_CONTRACT_KEYS:
                # review c120 r3_s3-4: YAML 1.1 loads ``no``/``false`` as a bool - a bool or
                # null is not a statement of what the signal stands in for
                value = e.get(key)
                if value is not None and (not isinstance(value, str) or not value.strip()):
                    out.append(f"{where}: {key} must be a non-empty string or 'none', "
                               f"not {value!r} (quote YAML no/false/null)")
            proxy, target = str(e.get("proxy_of") or ""), str(e.get("target") or "")
            if proxy and proxy != "none" and target != "none":
                out.append(f"{where}: a proxy (proxy_of={proxy!r}) cannot be a target ({target!r})")
            validity = e.get("validity")
            if isinstance(validity, dict):
                if not validity or not all(
                        isinstance(v, (int, float)) and not isinstance(v, bool) for v in validity.values()):
                    out.append(f"{where}: validity bounds must be numbers")
            elif validity != "unmeasured":
                out.append(f"{where}: validity is a mapping of numbers or 'unmeasured'")
            writes = e.get("writes")
            if writes is not None:
                if not str(writes).strip() or str(writes).strip() == "none":
                    out.append(f"{where}: 'writes' names the stored parameter (omit it when nothing is written)")
                elif proxy == "none" and target != str(writes).strip():
                    # a non-proxy signal that moves a parameter must call it its target
                    out.append(f"{where}: writes {writes!r} from a signal that is not a proxy, "
                               f"but its target is {target!r}")
            consumer = str(e.get("consumer") or "")
            if consumer != "none":
                why = _ref_violation(consumer, kind="consumer")
                if why is not None:
                    out.append(f"{where}: consumer does not resolve: {why}")
            elif target != "none":
                out.append(f"{where}: a target ({target!r}) needs a named consumer")
    return out


def test_every_shadow_system_carries_a_valid_signal_contract():
    systems = _load()
    assert [s["id"] for s in systems if s["state"] == "B"], "positive control: no B entry"
    assert _contract_violations(systems) == []


def test_contract_check_catches_a_missing_contract_and_a_proxy_target():
    """Negative controls on copies of the real register (the file is not touched)."""
    import copy

    systems = _load()
    b_ids = [s["id"] for s in systems if s["state"] == "B"]
    for sys_id in b_ids:
        broken = copy.deepcopy(systems)
        next(s for s in broken if s["id"] == sys_id).pop("signal_contract", None)
        assert f"{sys_id} is B but has no signal_contract" in _contract_violations(broken)

    broken = copy.deepcopy(systems)
    l07 = next(s for s in broken if s["id"] == "L07")["signal_contract"]
    assert l07["proxy_of"] != "none", "positive control: L07 is recorded as a proxy"
    l07.update(target="stage_selection",
               consumer="core/skills/os_skills/delegation_router.py:143:_learned_threshold")
    assert any("cannot be a target" in v for v in _contract_violations(broken))

    broken = copy.deepcopy(systems)
    broken_l11 = next(s for s in broken if s["id"] == "L11")["signal_contract"]
    broken_l11[0].pop("skill")
    broken_l11[1]["validity"] = {"min_samples": "many"}
    broken_l11[2]["consumer"] = "core/does/not/exist.py:1:anything"
    broken_l11[3]["consumer"] = "core/skills/os_skills/delegation_router.py:143"   # no needle
    found = _contract_violations(broken)
    assert any("names its skill" in v for v in found)
    assert any("bounds must be numbers" in v for v in found)
    assert any("does not exist" in v for v in found)
    assert any("is not path:line:needle" in v for v in found)


def test_router_threshold_writers_are_registered_as_proxies():
    """Review c120 s5_completeness-2: ``confidence_threshold`` is moved by the operator
    star rating (stream4) and by the epoch test on the tenant's last 10 outcomes - both
    proxies of routing correctness. Every L05 row that writes it says so, keeps
    ``target: none``, and both writers are listed callers. The routing-ledger join (L12)
    is not a proxy and learns no parameter."""
    import copy

    systems = _load()
    l05 = next(s for s in systems if s["id"] == "L05")
    rows = l05["signal_contract"]
    writers = [r for r in rows if r.get("writes") == "confidence_threshold"]
    assert len(writers) == 2, "positive control: L05 records two threshold writers"
    for r in writers:
        assert r["proxy_of"] != "none" and r["target"] == "none", r["writer"]
    callers = " ".join(l05["callers"])
    assert "stream4_skill_feedback.py" in callers and "apply_outcome_to_skill" in callers
    assert "method_discovery_api.py" in callers and "run_optimizer_epoch" in callers
    l12 = next(s for s in systems if s["id"] == "L12")["signal_contract"]
    assert l12["proxy_of"] == "none" and l12["target"] == "none" and "writes" not in l12

    # Negative controls on copies: calling a proxy row a target, or relabelling it as
    # "not a proxy" while it keeps writing the threshold without naming it, both fail.
    broken = copy.deepcopy(systems)
    next(s for s in broken if s["id"] == "L05")["signal_contract"][0]["target"] = "confidence_threshold"
    assert any("L05/os.delegation_router: a proxy" in v for v in _contract_violations(broken))
    broken = copy.deepcopy(systems)
    next(s for s in broken if s["id"] == "L05")["signal_contract"][1]["proxy_of"] = "none"
    assert any("writes 'confidence_threshold' from a signal that is not a proxy" in v
               for v in _contract_violations(broken))


def test_dead_systems_name_their_retiring_decision():
    for s in _load():
        if s["state"] != "D":
            continue
        assert ADR_ID.match(str(s.get("retired_by") or "")), (
            f"{s['id']} is D but names no retiring ADR id (retired_by)")
        assert s.get("modules"), f"{s['id']} is D but lists no modules"


def _matches(pattern: str) -> list[Path]:
    if any(ch in pattern for ch in "*?["):
        if pattern.endswith("**"):   # "dir/**" yields directories only on 3.11
            pattern += "/*"
        return [p for p in REPO.glob(pattern) if p.is_file()]
    p = REPO / pattern
    return [p] if p.is_file() else []


@pytest.mark.parametrize("sys_id", [s["id"] for s in _load() if s["state"] == "D"])
def test_dead_entry_still_has_a_module(sys_id):
    s = next(x for x in _load() if x["id"] == sys_id)
    left = [m for m in s["modules"] if _matches(m)]
    assert left, (f"{sys_id}: every module is gone; remove the D entry in the same "
                  f"change that deleted its last module ({s['retired_by']})")


def test_retiring_decisions_resolve_in_corvin_knowledge():
    ids = sorted({s["retired_by"] for s in _load() if s["state"] == "D"})
    if not ids:
        pytest.skip("no D entry")
    d = _decisions_dir()
    if d is None:
        pytest.skip("no Corvin-Knowledge checkout reachable (CORVIN_KB_REPO, a sibling "
                    "Corvin-Knowledge/, or the corvin_decisions submodule)")
    for adr in ids:
        assert list(d.glob(f"{adr}-*.md")), f"{adr} does not resolve in {d}"


def test_docs_register_table_equals_the_register():
    """ADR-2281 / T-0262: docs/claude-ref/learning-loop.md carries the register's
    table; every A/B row there must name the same state as the register, every
    A/B system must have a row, and the D entries are summarised as one row."""
    doc = (REPO / "docs" / "claude-ref" / "learning-loop.md").read_text(encoding="utf-8")
    start, end = "<!-- register-table:start", "<!-- register-table:end -->"
    assert start in doc and end in doc, "the register table markers are missing from learning-loop.md"
    block = doc.split(start, 1)[1].split(end, 1)[0]
    rows = {}
    for line in block.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 3 and cells[0] not in ("Id", "---"):
            rows[cells[0]] = cells[2]
    systems = _load()
    for s in systems:
        if s["state"] == "D":
            continue
        assert rows.get(s["id"]) == s["state"], (
            f"learning-loop.md says {s['id']} is {rows.get(s['id'])!r}, the register says {s['state']!r}")
    d_ids = sorted(s["id"] for s in systems if s["state"] == "D")
    if d_ids:
        d_row = f"{d_ids[0]}–{d_ids[-1]}"
        assert rows.get(d_row) == "D", f"learning-loop.md lacks the D summary row {d_row!r}"
    known = {s["id"] for s in systems} | ({f"{d_ids[0]}–{d_ids[-1]}"} if d_ids else set())
    assert set(rows) <= known, f"learning-loop.md lists ids the register lacks: {set(rows) - known}"


def test_vibe_engineering_unreachable_modules_are_classified():
    """ADR-2281 §6: each unreachable core/vibe_engineering module is classified
    as learning (a register entry lists it) or not-learning (with a reason)."""
    data = yaml.safe_load(REGISTER.read_text(encoding="utf-8"))
    not_learning = {c["path"]: c for c in data.get("classified_not_learning", [])}
    for c in not_learning.values():
        assert str(c.get("reason") or "").strip(), f"{c['path']}: no reason"
        assert (REPO / c["path"]).is_file(), (
            f"{c['path']} is gone; drop its classification")
    in_register = {str(p.relative_to(REPO)) for s in data["systems"]
                   for m in s.get("modules", []) for p in _matches(m)}
    baseline = json.loads(REACH_BASELINE.read_text(encoding="utf-8"))["unreachable"]
    ve = [b for b in baseline if b.startswith("core/vibe_engineering/")]
    assert ve, "positive control: the baseline lists no core/vibe_engineering module"
    for path in ve:
        assert path in not_learning or path in in_register, (
            f"{path} is unreachable and not classified (learning or not-learning)")


@pytest.mark.parametrize("sys_id", ["L05", "L12"])
def test_router_entries_record_the_readiness_measurement(sys_id):
    """T-0280: the router readiness is measured with readiness.evaluate, dated, per surface."""
    entry = next(s for s in _load() if s["id"] == sys_id)
    m = entry.get("measured")
    assert m, f"{sys_id}: no readiness measurement recorded"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(m["date"])), f"{sys_id}: measurement date not ISO"
    for surface in ("bridge", "console"):
        row = m[surface]
        assert {"ready", "reason", "decisions", "join_rate"} <= set(row), f"{sys_id}: {surface} incomplete"
    # Phase 2 stays off here: a measurement that says ready must come with a proposed decision.
    assert not any(m[s]["ready"] for s in ("bridge", "console")) or "decision" in m["verdict"].lower()


READINESS_SRC = REPO / "core" / "skills" / "os_skills" / "monitoring" / "readiness.py"


def _readiness_reasons() -> set:
    """Every reason readiness.evaluate() can return, read from its source (a trailing
    colon marks the ``reason:<bucket>`` variants)."""
    src = READINESS_SRC.read_text(encoding="utf-8")
    fixed = set(re.findall(r'Readiness\((?:True|False),\s*"([a-z_]+)"', src))
    prefixed = set(re.findall(r'Readiness\(False,\s*f"([a-z_]+):\{', src))
    return fixed | {p + ":" for p in prefixed}


@pytest.mark.parametrize("sys_id", ["L05", "L12"])
def test_recorded_readiness_reasons_are_ones_the_evaluator_can_return(sys_id):
    reasons = _readiness_reasons()
    assert {"insufficient_samples", "console_soak_incomplete", "ready"} <= reasons, (
        "positive control: the reason scan found too little in readiness.py")
    m = next(s for s in _load() if s["id"] == sys_id)["measured"]
    for surface in ("bridge", "console"):
        row = m[surface]
        ok = row["reason"] in reasons or any(
            p.endswith(":") and row["reason"].startswith(p) for p in reasons)
        assert ok, f"{sys_id}/{surface}: {row['reason']!r} is not a reason evaluate() returns"
        assert row["ready"] == (row["reason"] == "ready")


def _stale_classifications(classified: set, unreachable: set) -> list:
    """Classified-as-not-learning paths that are no longer unreachable.

    A classification answers "is this unreachable module a learning system?". Once the module
    is wired (or deleted) the question is gone and the entry is dead weight that a reader
    would still trust; the ratchet baseline shrinks, so the classification must shrink with it."""
    return sorted(p for p in classified if p not in unreachable)


def test_not_learning_classifications_are_still_about_unreachable_modules():
    """T-0260: a core/vibe_engineering classification does not outlive its module's
    unreachability."""
    data = yaml.safe_load(REGISTER.read_text(encoding="utf-8"))
    classified = {c["path"] for c in data.get("classified_not_learning", [])}
    unreachable = set(json.loads(REACH_BASELINE.read_text(encoding="utf-8"))["unreachable"])
    assert classified, "positive control: the register classifies no module"
    assert _stale_classifications(classified, unreachable) == []
    # the detector itself: a path missing from the baseline is reported, one inside is not
    assert _stale_classifications({"a.py", "b.py"}, {"a.py"}) == ["b.py"]
