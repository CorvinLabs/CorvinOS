#!/usr/bin/env python3
"""check_prometheus_labels.py — CI linter for Prometheus metric label PII leakage.

ADR-0073 G-017: scans all Python source files for Prometheus metric definitions
and raises if any labelnames contain PII-indicator strings.

Usage:
    python3 check_prometheus_labels.py [--root <path>]
    # exits 0 on clean, 1 on violations found

MUST NOT import anthropic (CI AST lint enforces).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

# PII-indicator label name fragments (case-insensitive substring match).
# These are NOT exhaustive but cover the common developer mistakes.
_PII_INDICATORS: frozenset[str] = frozenset({
    "uid",
    "user_id",
    "userid",
    "email",
    "phone",
    "name",       # catches "username", "fullname", "first_name", etc.
    "ip",         # catches "ip_address", "client_ip", etc.
    "address",
    "jid",        # WhatsApp jabber IDs
    "chat_id",
    "sender",
    "recipient",
    "subject",
})

# Label names that look like PII indicators but are structurally safe.
# Must exactly match the label name (not substring).
_SAFE_LABELS: frozenset[str] = frozenset({
    "channel",       # bridge channel name, not a person
    "engine_id",     # engine identifier
    "persona",       # persona name, not a person
    "tenant_id",     # tenant identifier
    "bridge",        # bridge type
    "outcome",       # success/error/timeout
    "severity",      # log severity
    "layer",         # Corvin layer number
    "framework",     # compliance framework name
    "scope",         # task/session/project/user/tenant
    "reason",        # reason code
    "matched_rule",  # policy rule name
})

_METRIC_CALLS = frozenset({"Counter", "Histogram", "Gauge", "Summary"})


def _is_pii_label(label: str) -> bool:
    """Return True if label name contains a PII indicator and is not in the safe list."""
    if label in _SAFE_LABELS:
        return False
    low = label.lower()
    return any(ind in low for ind in _PII_INDICATORS)


def _extract_labelnames(call: ast.Call) -> list[str]:
    """Extract the labelnames list from a Prometheus metric call.

    Handles both positional and keyword forms:
        Counter("name", "doc", ["label1", "label2"])
        Counter("name", "doc", labelnames=["label1", "label2"])
    """
    labels: list[str] = []

    # Keyword argument: labelnames=[...]
    for kw in call.keywords:
        if kw.arg == "labelnames" and isinstance(kw.value, ast.List):
            for elt in kw.value.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    labels.append(elt.value)
            return labels

    # Positional argument (3rd position, index 2)
    if len(call.args) >= 3:
        arg = call.args[2]
        if isinstance(arg, ast.List):
            for elt in arg.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    labels.append(elt.value)

    return labels


def check_file(path: Path) -> list[tuple[int, str, str]]:
    """Return list of (lineno, metric_name, label_name) PII violations in path."""
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []

    violations: list[tuple[int, str, str]] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Match bare Counter(...) or prometheus_client.Counter(...)
        func = node.func
        func_name: str | None = None
        if isinstance(func, ast.Name):
            func_name = func.id
        elif isinstance(func, ast.Attribute):
            func_name = func.attr

        if func_name not in _METRIC_CALLS:
            continue

        # Extract metric name (first positional arg)
        metric_name = "<unknown>"
        if node.args and isinstance(node.args[0], ast.Constant):
            metric_name = str(node.args[0].value)

        for label in _extract_labelnames(node):
            if _is_pii_label(label):
                violations.append((node.lineno, metric_name, label))

    return violations


def main(argv: list[str]) -> int:
    root = Path(".")
    if "--root" in argv:
        idx = argv.index("--root")
        if idx + 1 < len(argv):
            root = Path(argv[idx + 1])

    py_files = list(root.rglob("*.py"))
    all_violations: list[tuple[Path, int, str, str]] = []

    for path in sorted(py_files):
        for lineno, metric, label in check_file(path):
            all_violations.append((path, lineno, metric, label))

    if not all_violations:
        print(f"check_prometheus_labels: OK — {len(py_files)} files scanned, no PII labels found")
        return 0

    print(f"check_prometheus_labels: FAIL — {len(all_violations)} PII label violation(s):\n")
    for path, lineno, metric, label in all_violations:
        print(f"  {path}:{lineno} — metric={metric!r} label={label!r}")
        print(f"    Hint: rename to a non-personal structural identifier, or add to _SAFE_LABELS if provably safe.")
    print()
    print("GDPR Art. 5 / ADR-0073 G-017: Prometheus label names must not identify natural persons.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
