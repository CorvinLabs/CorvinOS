"""R2 regression: the python AST code-exec gate also inspects code fed via
stdin (piped echo/printf and heredoc), not only `python -c`."""
import sys
from pathlib import Path

_HOOK = Path(__file__).resolve().parent
if str(_HOOK) not in sys.path:
    sys.path.insert(0, str(_HOOK))

import path_gate as pg  # type: ignore  # noqa: E402


def test_piped_stdin_code_extracted():
    snips = pg._extract_python_snippets("echo 'import os; exec(\"x\")' | python3")
    assert any("exec" in code for code, _ in snips), "piped-stdin code must be extracted"


def test_heredoc_stdin_code_extracted():
    cmd = "python3 <<'EOF'\nimport os\neval('1')\nEOF"
    snips = pg._extract_python_snippets(cmd)
    assert any("eval" in code for code, _ in snips), "heredoc-stdin code must be extracted"


def test_inline_dash_c_still_extracted():
    snips = pg._extract_python_snippets("python3 -c 'import os'")
    assert any("import os" in code for code, _ in snips)


# R3: AST gate must block the named indirection bypasses without false-positives.
def test_importlib_indirection_blocked():
    assert not pg._python_ast_check("importlib.import_module('subprocess')")[0]
    assert not pg._python_ast_check("import importlib")[0]
    assert not pg._python_ast_check("import marshal")[0]


def test_getattr_on_builtins_blocked():
    assert not pg._python_ast_check("getattr(__builtins__,'eval')('x')")[0]
    assert not pg._python_ast_check("vars(builtins)['exec']")[0]


def test_ordinary_getattr_and_os_still_allowed():
    # Defense-in-depth must not break legitimate code.
    assert pg._python_ast_check("getattr(obj, 'attr')")[0]
    assert pg._python_ast_check("import os; os.getcwd()")[0]


def test_literal_eval_still_blocked():
    assert not pg._python_ast_check("eval('1')")[0]
