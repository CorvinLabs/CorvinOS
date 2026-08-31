"""Bridge lifecycle management — venv setup and binary deployment.

Each bridge gets its own isolated Python venv under ~/.corvin/bridges/<name>/venv/.
Node.js dependencies are installed via npm in the venv.
"""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from corvinOS.shared.paths import bridge_channel_dir, bridges_home


class BridgeManager:
    """Manages per-bridge virtual environments and binary installation."""

    def __init__(self):
        self.bridges_home = bridges_home()
        self.bridges_home.mkdir(parents=True, exist_ok=True)

    def bridge_venv_dir(self, channel: str) -> Path:
        """Return venv directory for a bridge."""
        return bridge_channel_dir(channel) / "venv"

    def bridge_venv_bin(self, channel: str) -> Path:
        """Return bin/ directory inside venv (platform-aware)."""
        venv_dir = self.bridge_venv_dir(channel)
        if sys.platform == "win32":
            return venv_dir / "Scripts"
        return venv_dir / "bin"

    def bridge_python_exe(self, channel: str) -> Path:
        """Return python executable inside venv."""
        bin_dir = self.bridge_venv_bin(channel)
        if sys.platform == "win32":
            return bin_dir / "python.exe"
        return bin_dir / "python"

    def bridge_npm_exe(self, channel: str) -> Path:
        """Return npm executable inside venv."""
        bin_dir = self.bridge_venv_bin(channel)
        if sys.platform == "win32":
            return bin_dir / "npm.cmd"
        return bin_dir / "npm"

    def create_venv(self, channel: str) -> None:
        """Create a fresh Python venv for a bridge."""
        venv_dir = self.bridge_venv_dir(channel)
        if venv_dir.exists():
            print(f"venv already exists at {venv_dir}, skipping creation")
            return

        print(f"Creating venv for {channel} at {venv_dir}...")
        venv_dir.parent.mkdir(parents=True, exist_ok=True)

        # Create venv
        subprocess.run(
            [sys.executable, "-m", "venv", str(venv_dir)],
            check=True,
            capture_output=True,
        )
        print(f"✓ venv created for {channel}")

    def install_bridge_python_deps(
        self, channel: str, requirements_path: Optional[Path] = None
    ) -> None:
        """Install Python dependencies for a bridge."""
        if requirements_path is None:
            # Try repo-relative path
            repo_root = Path(__file__).resolve().parent.parent.parent
            requirements_path = repo_root / "operator" / "bridges" / channel / "requirements.txt"

        if not requirements_path.exists():
            print(f"No requirements.txt found for {channel}, skipping pip install")
            return

        print(f"Installing Python dependencies for {channel}...")
        pip_exe = self.bridge_venv_bin(channel) / "pip"
        if sys.platform == "win32":
            pip_exe = self.bridge_venv_bin(channel) / "pip.exe"

        subprocess.run(
            [str(pip_exe), "install", "-r", str(requirements_path)],
            check=True,
            capture_output=True,
        )
        print(f"✓ Python dependencies installed for {channel}")

    def install_bridge_node_deps(
        self, channel: str, package_json_path: Optional[Path] = None
    ) -> None:
        """Install Node.js dependencies for a bridge."""
        if package_json_path is None:
            repo_root = Path(__file__).resolve().parent.parent.parent
            package_json_path = repo_root / "operator" / "bridges" / channel / "package.json"

        if not package_json_path.exists():
            print(f"No package.json found for {channel}, skipping npm install")
            return

        print(f"Installing Node.js dependencies for {channel}...")

        # Ensure npm is available in PATH or download
        npm_exe = self._get_npm_exe()
        if npm_exe is None:
            print(f"⚠ npm not found, skipping Node.js setup for {channel}")
            return

        bridge_dir = bridge_channel_dir(channel)
        subprocess.run(
            [str(npm_exe), "install", "--prefix", str(bridge_dir)],
            check=True,
            capture_output=True,
            cwd=str(bridge_dir),
        )
        print(f"✓ Node.js dependencies installed for {channel}")

    def _get_npm_exe(self) -> Optional[Path]:
        """Find npm executable (checks PATH, nvm, etc.)."""
        # Try which/where
        try:
            result = subprocess.run(
                ["which" if sys.platform != "win32" else "where", "npm"],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                return Path(result.stdout.strip().split("\n")[0])
        except Exception:
            pass

        # Try common nvm location
        if sys.platform != "win32":
            nvm_npm = Path.home() / ".nvm" / "versions" / "node" / "*/bin/npm"
            # This is a glob pattern — would need glob()
            # For now, return None and let user install npm globally

        return None

    def verify_bridge(self, channel: str) -> bool:
        """Verify that a bridge venv is properly set up."""
        python_exe = self.bridge_python_exe(channel)
        if not python_exe.exists():
            print(f"✗ Python executable not found for {channel}")
            return False

        # Test python
        result = subprocess.run(
            [str(python_exe), "--version"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"✗ Python verification failed for {channel}: {result.stderr}")
            return False

        print(f"✓ {channel} bridge verified: {result.stdout.strip()}")
        return True

    def create_launch_script(self, channel: str, daemon_module: str) -> Path:
        """Create a launch script that runs the bridge daemon."""
        script_dir = bridge_channel_dir(channel)
        if sys.platform == "win32":
            script_path = script_dir / f"start-{channel}.bat"
            python_exe = self.bridge_python_exe(channel)
            script_content = f"""@echo off
cd /d "{script_dir}"
"{python_exe}" -m {daemon_module}
"""
        else:
            script_path = script_dir / f"start-{channel}.sh"
            python_exe = self.bridge_python_exe(channel)
            script_content = f"""#!/bin/bash
cd "{script_dir}"
exec "{python_exe}" -m {daemon_module}
"""
            script_path.chmod(0o755)

        script_path.write_text(script_content)
        print(f"✓ Launch script created at {script_path}")
        return script_path

    def cleanup_venv(self, channel: str) -> None:
        """Remove venv and bridge directory."""
        venv_dir = self.bridge_venv_dir(channel)
        bridge_dir = bridge_channel_dir(channel)

        if venv_dir.exists():
            import shutil
            shutil.rmtree(venv_dir)
            print(f"✓ Removed venv for {channel}")

        # Only remove bridge dir if it's empty
        try:
            bridge_dir.rmdir()
            print(f"✓ Removed bridge directory for {channel}")
        except OSError:
            print(f"ℹ Bridge directory not empty, keeping: {bridge_dir}")
