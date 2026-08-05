"""Diagnose command — auto-detect installation errors and suggest fixes."""
import sys
from pathlib import Path
from typing import Tuple

# ANSI helpers
def _bold(s: str) -> str:
    return f"\033[1m{s}\033[0m" if sys.stdout.isatty() else s

def _green(s: str) -> str:
    return f"\033[32m{s}\033[0m" if sys.stdout.isatty() else s

def _red(s: str) -> str:
    return f"\033[31m{s}\033[0m" if sys.stdout.isatty() else s

def _yellow(s: str) -> str:
    return f"\033[33m{s}\033[0m" if sys.stdout.isatty() else s


class DiagnosisResult:
    """Result of a single diagnostic check."""
    def __init__(self, name: str, passed: bool, detail: str):
        self.name = name
        self.passed = passed
        self.detail = detail

    def display(self) -> str:
        status = _green("✓") if self.passed else _red("✗")
        return f"  {status} {self.name}: {self.detail}"


def check_http_packages() -> DiagnosisResult:
    """Check for duplicate httpcore2 / httpx2 packages."""
    try:
        import subprocess
        result = subprocess.run(
            [sys.executable, "-m", "pip", "list", "--format=json"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        import json
        packages = {pkg["name"]: pkg["version"] for pkg in json.loads(result.stdout)}

        httpcore_v = packages.get("httpcore")
        httpcore2_v = packages.get("httpcore2")
        httpx_v = packages.get("httpx")
        httpx2_v = packages.get("httpx2")

        if not httpcore_v or not httpx_v:
            return DiagnosisResult("HTTP packages", False, "httpcore or httpx not installed")

        if httpcore2_v or httpx2_v:
            return DiagnosisResult(
                "HTTP packages (CRITICAL)",
                False,
                f"Duplicate packages found: httpcore2={httpcore2_v}, httpx2={httpx2_v}. "
                f"Run: pip uninstall httpcore2 httpx2 -y",
            )

        return DiagnosisResult(
            "HTTP packages",
            True,
            f"OK (httpcore {httpcore_v}, httpx {httpx_v})",
        )
    except Exception as e:
        return DiagnosisResult("HTTP packages", False, f"Error checking: {e}")


def check_ollama_running() -> DiagnosisResult:
    """Check if Ollama is running and reachable."""
    try:
        import urllib.request
        import urllib.error
        urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2)
        return DiagnosisResult("Ollama running", True, "http://localhost:11434 responding")
    except (urllib.error.URLError, Exception):
        return DiagnosisResult(
            "Ollama running",
            False,
            "Not reachable on http://localhost:11434 — "
            "Start Ollama or register Scheduled Task for autostart",
        )


def check_ollama_autostart() -> DiagnosisResult:
    """Check if Ollama is registered for autostart (Windows only)."""
    if sys.platform != "win32":
        return DiagnosisResult("Ollama autostart (Windows)", True, "Not applicable on this OS")

    try:
        import subprocess
        result = subprocess.run(
            ["powershell", "-Command", "Get-ScheduledTask -TaskName Ollama-Autostart -ErrorAction SilentlyContinue"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if "Ollama-Autostart" in result.stdout:
            return DiagnosisResult("Ollama autostart", True, "Scheduled Task registered")
        else:
            return DiagnosisResult(
                "Ollama autostart",
                False,
                "No Scheduled Task — Run PowerShell recovery script in docs/windows-installation-errors.md",
            )
    except Exception as e:
        return DiagnosisResult("Ollama autostart", False, f"Error checking: {e}")


def check_corvin_task() -> DiagnosisResult:
    """Check if CorvinOS-Console Scheduled Task exists (Windows only)."""
    if sys.platform != "win32":
        return DiagnosisResult("CorvinOS autostart (Windows)", True, "Not applicable on this OS")

    try:
        import subprocess
        result = subprocess.run(
            ["powershell", "-Command", "Get-ScheduledTask -TaskName CorvinOS-Console -ErrorAction SilentlyContinue"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if "CorvinOS-Console" in result.stdout:
            return DiagnosisResult("CorvinOS autostart", True, "Scheduled Task registered")
        else:
            return DiagnosisResult(
                "CorvinOS autostart",
                False,
                "No Scheduled Task — Run PowerShell recovery script in docs/windows-installation-errors.md",
            )
    except Exception as e:
        return DiagnosisResult("CorvinOS autostart", False, f"Error checking: {e}")


def check_piper_models() -> DiagnosisResult:
    """Check if Piper speech models are installed."""
    try:
        config_dir = Path.home() / ".config" / "corvin-voice" / "piper"
        onnx_models = list(config_dir.glob("*.onnx"))

        if not config_dir.exists():
            return DiagnosisResult(
                "Piper models",
                False,
                "Config dir not found (~/.config/corvin-voice/piper/). "
                "Run: corvin-voice --lang de --speaker kerstin",
            )

        if not onnx_models:
            return DiagnosisResult(
                "Piper models",
                False,
                "No .onnx model files. Run: corvin-voice --lang de --speaker kerstin",
            )

        models_str = ", ".join(m.stem for m in onnx_models)
        return DiagnosisResult("Piper models", True, f"OK ({models_str})")
    except Exception as e:
        return DiagnosisResult("Piper models", False, f"Error checking: {e}")


def check_pywin32() -> DiagnosisResult:
    """Check pywin32 version and COM registration (Windows only)."""
    if sys.platform != "win32":
        return DiagnosisResult("pywin32 (Windows)", True, "Not applicable on this OS")

    try:
        import win32com.client  # noqa: F401
        import importlib.metadata
        version = importlib.metadata.version("pywin32")
        return DiagnosisResult("pywin32", True, f"OK (v{version}, COM registered)")
    except ImportError:
        return DiagnosisResult("pywin32", False, "Not installed or COM registration missing")
    except Exception as e:
        return DiagnosisResult("pywin32", False, f"Error checking: {e}")


def cmd_diagnose_windows(args) -> int:
    """Run Windows 11 installation diagnostics."""
    print(f"\n{_bold('CorvinOS — Windows 11 Diagnostics')} (v0.10.116+)\n")

    results = [
        check_http_packages(),
        check_ollama_running(),
        check_ollama_autostart(),
        check_corvin_task(),
        check_piper_models(),
        check_pywin32(),
    ]

    for result in results:
        print(result.display())

    print()
    passed = sum(1 for r in results if r.passed)
    total = len(results)

    if passed == total:
        print(_green(f"  All {total} checks passed. ✓"))
        print()
        return 0
    else:
        print(_red(f"  {total - passed} issue(s) found. See details above."))
        print(f"  Documentation: docs/windows-installation-errors.md")
        print()
        return 1


def add_parser(subparsers):
    """Register diagnose subcommands to the main parser."""
    diag = subparsers.add_parser("diagnose", help="Diagnose installation and runtime errors")
    diag_sub = diag.add_subparsers(dest="diagnose_cmd", metavar="subcommand")
    diag_sub.add_parser("windows", help="Run Windows 11 installation diagnostics")
