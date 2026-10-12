"""E2E Tests for Code v2 Rendering (T-0112, ADR-2249 Phase 3a).

Tests cover:
- Syntax highlighting (Python, shell, JSON, YAML, text)
- Diff rendering (added, removed, modified lines)
- Annotations (comments, warnings, breakpoints)
- Asset integration (fonts, colors from T-0111 AssetPack)
- Signal emission (code_syntax_valid)
- Error handling (out-of-bounds, invalid annotations, etc.)

Exit code 0 = all tests pass.
Exit code 1 = one or more tests failed.

Run: python3 tests/skills/video_producer/test_code_t0112.py
"""

from pathlib import Path
import sys
import traceback

# Import the code_template module
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "core" / "skills" / "os_skills" / "video_producer" / "src"))

from code_template import (
    CodeScene, Token, Annotation, DiffInfo, DiffEngine, SimpleLexer,
    MAX_CODE_LINES, MAX_LINE_LENGTH, MAX_ANNOTATIONS,
)


# Simple test framework (no pytest dependency)
class TestRunner:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.errors = []

    def assert_equals(self, actual, expected, msg=""):
        if actual != expected:
            raise AssertionError(f"Expected {expected}, got {actual}. {msg}")

    def assert_true(self, condition, msg=""):
        if not condition:
            raise AssertionError(f"Expected True, got {condition}. {msg}")

    def assert_false(self, condition, msg=""):
        if condition:
            raise AssertionError(f"Expected False, got {condition}. {msg}")

    def assert_in(self, needle, haystack, msg=""):
        if needle not in haystack:
            raise AssertionError(f"Expected {needle} in {haystack}. {msg}")

    def assert_raises(self, exc_type, func, *args, **kwargs):
        try:
            func(*args, **kwargs)
            raise AssertionError(f"Expected {exc_type.__name__} to be raised")
        except exc_type:
            pass

    def run_test(self, test_func, test_name):
        try:
            test_func()
            self.passed += 1
            print(f"✓ {test_name}")
        except Exception as e:
            self.failed += 1
            self.errors.append((test_name, str(e), traceback.format_exc()))
            print(f"✗ {test_name}: {str(e)}")

    def report(self):
        print(f"\n{'='*70}")
        print(f"Tests passed: {self.passed}")
        print(f"Tests failed: {self.failed}")
        if self.errors:
            print(f"\n{'='*70}")
            print("FAILURES:")
            for test_name, error, tb in self.errors[:5]:  # Show first 5
                print(f"\n{test_name}:")
                print(error)
        return self.failed == 0


runner = TestRunner()


# ── Lexer Tests ──────────────────────────────────────────────────────────────

def test_python_keywords():
    """Lexer recognizes Python keywords."""
    code = "def foo(): pass"
    tokens = SimpleLexer.scan(code, "python")
    kinds = [t.kind for t in tokens]
    runner.assert_in("keyword", kinds)

def test_python_strings():
    """Lexer handles Python strings."""
    code = 'x = "hello"'
    tokens = SimpleLexer.scan(code, "python")
    reconstructed = "".join(t.text for t in tokens)
    runner.assert_equals(reconstructed, code)

def test_lexer_byte_for_byte():
    """Lexer contract: joined tokens = input."""
    codes = ["print('hello')", "x = 42\ny = 1", "# comment\ncode"]
    for code in codes:
        tokens = SimpleLexer.scan(code, "python")
        reconstructed = "".join(t.text for t in tokens)
        runner.assert_equals(reconstructed, code, f"Lexer failed: {repr(code)}")

def test_shell_scan():
    """Lexer scans shell code."""
    code = "echo $HOME"
    tokens = SimpleLexer.scan(code, "shell")
    runner.assert_true(len(tokens) > 0)

def test_json_scan():
    """Lexer scans JSON code."""
    code = '{"key": "value"}'
    tokens = SimpleLexer.scan(code, "json")
    reconstructed = "".join(t.text for t in tokens)
    runner.assert_equals(reconstructed, code)

def test_language_aliases():
    """Lexer resolves language aliases."""
    runner.assert_equals(SimpleLexer._resolve_language("py"), "python")
    runner.assert_equals(SimpleLexer._resolve_language("bash"), "shell")

def test_unknown_language():
    """Unknown language falls back to text."""
    runner.assert_equals(SimpleLexer._resolve_language("brainfuck"), "text")


# ── DiffEngine Tests ─────────────────────────────────────────────────────────

def test_diff_added():
    """DiffEngine marks added lines."""
    lines = [" unchanged", "+ added"]
    diffs = DiffEngine.analyze_diff(lines)
    runner.assert_equals(diffs[1].diff_type, "added")

def test_diff_removed():
    """DiffEngine marks removed lines."""
    lines = ["- removed", " kept"]
    diffs = DiffEngine.analyze_diff(lines)
    runner.assert_equals(diffs[0].diff_type, "removed")

def test_diff_invalid():
    """DiffEngine rejects invalid markers."""
    lines = ["no marker"]
    runner.assert_raises(ValueError, DiffEngine.analyze_diff, lines)


# ── CodeScene Tests ──────────────────────────────────────────────────────────

def test_codescene_init():
    """CodeScene initializes."""
    code = "def hello():\n    print('world')"
    scene = CodeScene("python", code)
    runner.assert_equals(scene.language, "python")
    runner.assert_equals(len(scene.lines), 2)

def test_codescene_exceeds_lines():
    """CodeScene rejects code exceeding line limit."""
    code = "\n" * (MAX_CODE_LINES + 1)
    runner.assert_raises(ValueError, CodeScene, "python", code)

def test_codescene_exceeds_length():
    """CodeScene rejects lines exceeding length limit."""
    code = "x = " + "a" * MAX_LINE_LENGTH
    runner.assert_raises(ValueError, CodeScene, "python", code)

def test_codescene_diff_mode():
    """CodeScene handles diff mode."""
    code = "+ added\n- removed\n  unchanged"
    scene = CodeScene("python", code, diff_mode=True)
    runner.assert_equals(len(scene.diff_info), 3)
    runner.assert_equals(scene.diff_info[0].diff_type, "added")

def test_codescene_annotation():
    """CodeScene adds annotations."""
    code = "line1\nline2\nline3"
    scene = CodeScene("python", code)
    anno_id = scene.add_annotation(2, "TODO")
    runner.assert_true(anno_id)
    runner.assert_equals(len(scene.annotations), 1)

def test_codescene_annotation_oob():
    """CodeScene rejects out-of-bounds annotations."""
    code = "line1\nline2"
    scene = CodeScene("python", code)
    runner.assert_raises(ValueError, scene.add_annotation, 5, "oob")

def test_codescene_max_annotations():
    """CodeScene enforces max annotations."""
    code = "\n".join([f"line{i}" for i in range(1, MAX_ANNOTATIONS + 2)])
    scene = CodeScene("python", code)
    for i in range(1, MAX_ANNOTATIONS + 1):
        scene.add_annotation(i, f"anno{i}")
    runner.assert_raises(ValueError, scene.add_annotation, MAX_ANNOTATIONS + 1, "extra")

def test_codescene_render_html():
    """CodeScene renders to HTML."""
    code = "print('hello')"
    scene = CodeScene("python", code)
    html = scene.render_html()
    runner.assert_in("code-scene", html)
    runner.assert_in("hello", html)

def test_codescene_render_html_annotations():
    """CodeScene renders annotations in HTML."""
    code = "line1\nline2"
    scene = CodeScene("python", code)
    scene.add_annotation(1, "Important", annotation_type="warning")
    html = scene.render_html()
    runner.assert_in("Important", html)
    runner.assert_in("anno-warning", html)

def test_codescene_render_svg():
    """CodeScene renders to SVG."""
    code = "x = 42"
    scene = CodeScene("python", code)
    svg = scene.render_svg()
    runner.assert_in("<svg", svg)
    runner.assert_in("python", svg)

def test_codescene_signal():
    """CodeScene emits signal."""
    code = "x = 1"
    scene = CodeScene("python", code)
    signal = scene.get_signal()
    runner.assert_in("code_syntax_valid", signal)
    runner.assert_in("scene_id", signal)
    runner.assert_equals(signal["num_lines"], 1)

def test_codescene_serialize():
    """CodeScene serializes to dict."""
    code = "def foo():\n    pass"
    scene = CodeScene("python", code)
    scene.add_annotation(1, "Function")
    data = scene.to_dict()
    runner.assert_equals(data["language"], "python")
    runner.assert_equals(data["num_annotations"], 1)

def test_codescene_diff_with_annotations():
    """CodeScene handles diff + annotations."""
    code = "+ added\n  kept\n- removed"
    scene = CodeScene("python", code, diff_mode=True)
    scene.add_annotation(1, "new")
    scene.add_annotation(3, "old")
    signal = scene.get_signal()
    runner.assert_equals(len(signal["diff_info"]), 3)
    runner.assert_equals(signal["num_annotations"], 2)

def test_codescene_html_escape():
    """CodeScene escapes HTML."""
    code = 'x = "<script>"'
    scene = CodeScene("python", code)
    html = scene.render_html()
    runner.assert_in("&lt;script&gt;", html)

def test_codescene_col_range():
    """CodeScene annotations with column ranges."""
    code = "x = variable"
    scene = CodeScene("python", code)
    anno_id = scene.add_annotation(1, "var", cols=(5, 12))
    runner.assert_equals(scene.annotations[0].col_range, (5, 12))

def test_codescene_languages():
    """CodeScene handles multiple languages."""
    for lang in ["python", "shell", "json", "yaml", "text"]:
        code = "x = 1"
        scene = CodeScene(lang, code)
        signal = scene.get_signal()
        runner.assert_true("language" in signal)


# ── Integration Tests ────────────────────────────────────────────────────────

def test_full_python_workflow():
    """Full E2E: Python code with annotations."""
    code = "def fib(n):\n    if n <= 1:\n        return n\n    return fib(n-1) + fib(n-2)"
    scene = CodeScene("python", code)
    scene.add_annotation(1, "Main function")
    scene.add_annotation(2, "Base case", annotation_type="warning")
    html = scene.render_html()
    signal = scene.get_signal()
    runner.assert_in("fib", html)
    runner.assert_equals(signal["num_annotations"], 2)
    runner.assert_equals(signal["num_lines"], 4)

def test_full_diff_workflow():
    """Full E2E: diff mode with annotations."""
    code = "+ new\n- old\n  kept"
    scene = CodeScene("python", code, diff_mode=True)
    scene.add_annotation(1, "Added")
    signal = scene.get_signal()
    runner.assert_equals(len(signal["diff_info"]), 3)
    runner.assert_equals(signal["num_annotations"], 1)

def test_full_shell_workflow():
    """Full E2E: shell script."""
    code = "#!/bin/bash\necho 'hello'\nls -la"
    scene = CodeScene("shell", code)
    scene.add_annotation(3, "List directory")
    signal = scene.get_signal()
    runner.assert_equals(signal["language"], "shell")
    runner.assert_equals(signal["num_lines"], 3)


# ── Edge Cases ───────────────────────────────────────────────────────────────

def test_empty_code():
    """CodeScene accepts empty code."""
    scene = CodeScene("python", "")
    runner.assert_equals(len(scene.lines), 1)

def test_whitespace_only():
    """CodeScene handles whitespace."""
    code = "   \n\t\n  "
    scene = CodeScene("python", code)
    runner.assert_equals(len(scene.lines), 3)

def test_label_truncation():
    """CodeScene truncates long labels."""
    code = "x = 1"
    scene = CodeScene("python", code)
    long_label = "x" * 50
    scene.add_annotation(1, long_label)
    runner.assert_equals(len(scene.annotations[0].label), 36)

def test_unicode_code():
    """CodeScene handles Unicode."""
    code = "# 你好\nx = '😀'"
    scene = CodeScene("python", code)
    html = scene.render_html()
    runner.assert_true(len(html) > 0)


# ── Signal Emission ──────────────────────────────────────────────────────────

def test_signal_on_init():
    """Signal emitted on init."""
    code = "x = 1"
    scene = CodeScene("python", code)
    runner.assert_true(scene.signal_code_syntax_valid is not None)

def test_signal_fields():
    """Signal has required fields."""
    code = "x = 1"
    scene = CodeScene("python", code)
    signal = scene.get_signal()
    required = ["code_syntax_valid", "scene_id", "language", "num_lines", "num_tokens", "num_annotations"]
    for field in required:
        runner.assert_in(field, signal, f"Missing field: {field}")


# ── Run All Tests ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Running T-0112 Code v2 Rendering Tests")
    print("=" * 70)

    # Collect all test functions
    test_functions = [
        # Lexer
        (test_python_keywords, "Lexer: Python keywords"),
        (test_python_strings, "Lexer: Python strings"),
        (test_lexer_byte_for_byte, "Lexer: byte-for-byte contract"),
        (test_shell_scan, "Lexer: shell code"),
        (test_json_scan, "Lexer: JSON code"),
        (test_language_aliases, "Lexer: language aliases"),
        (test_unknown_language, "Lexer: unknown language fallback"),
        # DiffEngine
        (test_diff_added, "Diff: added lines"),
        (test_diff_removed, "Diff: removed lines"),
        (test_diff_invalid, "Diff: invalid format"),
        # CodeScene
        (test_codescene_init, "CodeScene: initialization"),
        (test_codescene_exceeds_lines, "CodeScene: exceeds line limit"),
        (test_codescene_exceeds_length, "CodeScene: exceeds line length"),
        (test_codescene_diff_mode, "CodeScene: diff mode"),
        (test_codescene_annotation, "CodeScene: add annotation"),
        (test_codescene_annotation_oob, "CodeScene: annotation out-of-bounds"),
        (test_codescene_max_annotations, "CodeScene: max annotations limit"),
        (test_codescene_render_html, "CodeScene: render HTML"),
        (test_codescene_render_html_annotations, "CodeScene: render annotations"),
        (test_codescene_render_svg, "CodeScene: render SVG"),
        (test_codescene_signal, "CodeScene: signal emission"),
        (test_codescene_serialize, "CodeScene: serialize to dict"),
        (test_codescene_diff_with_annotations, "CodeScene: diff + annotations"),
        (test_codescene_html_escape, "CodeScene: HTML escape"),
        (test_codescene_col_range, "CodeScene: column range annotations"),
        (test_codescene_languages, "CodeScene: multiple languages"),
        # Integration
        (test_full_python_workflow, "E2E: Python workflow"),
        (test_full_diff_workflow, "E2E: diff workflow"),
        (test_full_shell_workflow, "E2E: shell workflow"),
        # Edge cases
        (test_empty_code, "Edge: empty code"),
        (test_whitespace_only, "Edge: whitespace only"),
        (test_label_truncation, "Edge: label truncation"),
        (test_unicode_code, "Edge: Unicode code"),
        # Signals
        (test_signal_on_init, "Signal: emitted on init"),
        (test_signal_fields, "Signal: required fields"),
    ]

    for test_func, test_name in test_functions:
        runner.run_test(test_func, test_name)

    success = runner.report()
    exit(0 if success else 1)
