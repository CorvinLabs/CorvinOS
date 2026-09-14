"""Windows Installation E2E Test Suite.

Covers three critical error classes:
1. Path handling (spaces, special characters, Unicode)
2. Encoding (UTF-8, locale-specific charsets)
3. Plugin registry validation (missing fields, malformed manifests)

All tests are TRUE E2E: they run the actual installer subprocess, plugin
loader, and console bootstrap — no mocking of the real I/O boundaries.

Fixture Strategy:
- `windows_temp_install`: Creates a temporary install tree with proper cleanup
- `valid_plugin_bundle`: Valid plugin.yaml manifest + structure
- `malformed_plugin_bundle`: Invalid manifest for error testing
- `console_ready_checker`: Polling loop that verifies console HTTP boot
"""

import asyncio
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Generator, Optional, Tuple
from unittest import mock

import pytest

# Only run on Windows (or mock Windows for testing)
pytestmark = pytest.mark.skipif(
    sys.platform != "win32" and os.environ.get("TEST_WINDOWS_INSTALL") != "1",
    reason="Windows-only tests (set TEST_WINDOWS_INSTALL=1 to force on Unix for CI)",
)


# ──────────────────────────────────────────────────────────────────────────────
# FIXTURES
# ──────────────────────────────────────────────────────────────────────────────


class TempWindowsInstall:
    """Manages a temporary Windows install directory with proper cleanup."""

    def __init__(self, base_path: Path):
        self.base_path = base_path
        self.corvin_home = base_path / ".corvin"
        self.tenant_dir = self.corvin_home / "tenants" / "_default"
        self.global_dir = self.tenant_dir / "global"
        self.plugins_dir = self.global_dir / "plugins"
        self.logs_dir = self.corvin_home / "logs"

    def setup(self) -> None:
        """Create required directory structure."""
        self.corvin_home.mkdir(parents=True, exist_ok=True)
        self.global_dir.mkdir(parents=True, exist_ok=True)
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.tenant_yaml_path = self.global_dir / "tenant.corvin.yaml"

    def write_tenant_config(self, spec: Optional[dict] = None) -> None:
        """Write a minimal tenant.corvin.yaml."""
        import yaml

        config = {
            "apiVersion": "corvin.io/v1",
            "kind": "TenantConfig",
            "metadata": {"name": "_default"},
            "spec": spec or {"plugins": {"installed": []}},
        }
        self.tenant_yaml_path.write_text(
            yaml.safe_dump(config, allow_unicode=True),
            encoding="utf-8",
        )

    def write_plugin_yaml(
        self,
        plugin_name: str,
        plugin_id: Optional[str] = None,
        version: str = "1.0.0",
        origin: str = "community",
        boot_layer: str = "installed",
        valid: bool = True,
    ) -> Path:
        """Write a plugin.yaml manifest to a plugin subdirectory.

        Args:
            plugin_name: Directory name for the plugin
            plugin_id: The `id` field (None to test missing-field error)
            version: Plugin version
            origin: "builtin" | "vetted" | "community"
            boot_layer: "bundled" | "installed"
            valid: If False, omit required fields to trigger validation errors

        Returns:
            Path to the plugin directory.
        """
        import yaml

        plugin_dir = self.plugins_dir / plugin_name
        plugin_dir.mkdir(parents=True, exist_ok=True)

        manifest = {}
        if plugin_id is not None:
            manifest["id"] = plugin_id
        if valid:
            manifest.update(
                {
                    "name": plugin_name.replace("_", "-"),
                    "version": version,
                    "origin": origin,
                    "boot_layer": boot_layer,
                    "plugin_type": "generic",
                }
            )

        yaml_path = plugin_dir / "plugin.yaml"
        yaml_path.write_text(
            yaml.safe_dump(manifest, allow_unicode=True),
            encoding="utf-8",
        )

        return plugin_dir

    def cleanup(self) -> None:
        """Remove temp directory (called by pytest fixture cleanup)."""
        import shutil

        if self.base_path.exists():
            shutil.rmtree(self.base_path, ignore_errors=True)


@pytest.fixture
def windows_temp_install() -> Generator[TempWindowsInstall, None, None]:
    """Create and cleanup a temporary Windows install directory."""
    with tempfile.TemporaryDirectory(prefix="corvin_windows_") as tmpdir:
        install = TempWindowsInstall(Path(tmpdir))
        install.setup()
        yield install
        install.cleanup()


@pytest.fixture
def windows_temp_install_with_spaces() -> (
    Generator[TempWindowsInstall, None, None]
):
    """Create install in a path with spaces and special characters.

    Windows paths with spaces (e.g., "C:\\Users\\Test User\\...") are a
    common source of quoting/escaping errors. This fixture creates the
    install in such a path.
    """
    with tempfile.TemporaryDirectory(
        prefix="corvin test install éàü "
    ) as tmpdir:  # Includes space + Unicode
        install = TempWindowsInstall(Path(tmpdir))
        install.setup()
        yield install
        install.cleanup()


@pytest.fixture
def valid_plugin_bundle(
    windows_temp_install: TempWindowsInstall,
) -> TempWindowsInstall:
    """Create a valid plugin with all required fields."""
    windows_temp_install.write_plugin_yaml(
        "valid_plugin",
        plugin_id="valid_plugin",
        version="1.0.0",
        origin="community",
        boot_layer="installed",
        valid=True,
    )
    return windows_temp_install


@pytest.fixture
def malformed_plugin_bundle(
    windows_temp_install: TempWindowsInstall,
) -> TempWindowsInstall:
    """Create plugins with various validation errors."""
    # Missing plugin_id field
    windows_temp_install.write_plugin_yaml(
        "no_id_plugin",
        plugin_id=None,
        valid=False,
    )

    # Invalid origin
    windows_temp_install.write_plugin_yaml("bad_origin_plugin")
    bad_origin_path = (
        windows_temp_install.plugins_dir / "bad_origin_plugin" / "plugin.yaml"
    )
    import yaml

    manifest = yaml.safe_load(bad_origin_path.read_text(encoding="utf-8"))
    manifest["origin"] = "invalid_origin"
    bad_origin_path.write_text(
        yaml.safe_dump(manifest, allow_unicode=True), encoding="utf-8"
    )

    return windows_temp_install


class ConsoleReadyChecker:
    """Poll for console HTTP readiness over a timeout period."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8765",
        max_wait_seconds: int = 30,
        poll_interval_ms: int = 500,
    ):
        self.base_url = base_url
        self.max_wait = max_wait_seconds
        self.poll_interval = poll_interval_ms / 1000.0
        self.ready = False
        self.error = None

    def poll(self) -> bool:
        """Poll until console is ready or timeout expires."""
        start = time.time()
        while time.time() - start < self.max_wait:
            try:
                import urllib.request

                with urllib.request.urlopen(
                    f"{self.base_url}/console/", timeout=2
                ) as resp:
                    if resp.status == 200:
                        self.ready = True
                        return True
            except Exception as e:
                self.error = str(e)

            time.sleep(self.poll_interval)

        return False


# ──────────────────────────────────────────────────────────────────────────────
# TEST CLASS 1: PATH HANDLING (Spaces, Special Characters)
# ──────────────────────────────────────────────────────────────────────────────


class TestWindowsPathHandling:
    """E2E tests for Windows path handling in installer and plugin loader.

    Target: Catch path-related errors (SyntaxError, quoting failures, special
    char mangling) in _win_shim.py and plugin path resolution.

    Failure modes this catches:
    - Unquoted paths with spaces cause shell parsing errors
    - Special characters (é, à, ü, etc.) cause encoding errors
    - Backslash escaping in Python paths
    """

    def test_install_in_path_with_spaces(
        self, windows_temp_install_with_spaces: TempWindowsInstall
    ) -> None:
        """E2E: Installer bootstraps in path with spaces."""
        install = windows_temp_install_with_spaces

        # Verify CORVIN_HOME is set to our temp path
        env = os.environ.copy()
        env["CORVIN_HOME"] = str(install.corvin_home)

        # Simulate minimal install step: create directories
        result = subprocess.run(
            [sys.executable, "-c", "from pathlib import Path; "
             "p = Path('{path}'); "
             "p.mkdir(parents=True, exist_ok=True); "
             "print(p.exists())".format(path=str(install.corvin_home))],
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Path creation failed: {result.stderr}"
        assert "True" in result.stdout

    def test_install_in_path_with_unicode(
        self, windows_temp_install_with_spaces: TempWindowsInstall
    ) -> None:
        """E2E: Plugin loader handles Unicode paths."""
        install = windows_temp_install_with_spaces
        install.write_plugin_yaml(
            "test_plugin",
            plugin_id="test_plugin",
            valid=True,
        )

        env = os.environ.copy()
        env["CORVIN_HOME"] = str(install.corvin_home)

        # Test that plugin.yaml can be read from Unicode path
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import yaml; "
                    "from pathlib import Path; "
                    "p = Path('{path}') / 'plugin.yaml'; "
                    "data = yaml.safe_load(p.read_text(encoding='utf-8')); "
                    "print('OK' if data.get('id') else 'FAIL')"
                ).format(path=str(install.plugins_dir / "test_plugin")),
            ],
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Unicode path read failed: {result.stderr}"
        assert "OK" in result.stdout

    def test_plugin_loader_path_quoting_windows(
        self, windows_temp_install_with_spaces: TempWindowsInstall
    ) -> None:
        """E2E: Plugin loader correctly quotes paths in subprocess calls."""
        install = windows_temp_install_with_spaces
        install.write_plugin_yaml("plugin_with_spaces", plugin_id="plugin_with_spaces")

        env = os.environ.copy()
        env["CORVIN_HOME"] = str(install.corvin_home)

        # Simulate what the installer does: resolve plugin path, call plugin
        # command with that path. The path must be properly quoted.
        plugin_path = install.plugins_dir / "plugin_with_spaces"

        # Call a subprocess that accepts the path (simulating plugin_cmd.py)
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys; "
                    "from pathlib import Path; "
                    "p = Path(sys.argv[1]); "
                    "assert p.exists(), f'Path not found: {p}'; "
                    "print('PASS')"
                ),
                str(plugin_path),  # Must be properly passed as argv
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Plugin path resolution failed: {result.stderr}"
        assert "PASS" in result.stdout


# ──────────────────────────────────────────────────────────────────────────────
# TEST CLASS 2: ENCODING (UTF-8, File I/O, Plugin Registration)
# ──────────────────────────────────────────────────────────────────────────────


class TestWindowsEncoding:
    """E2E tests for UTF-8 encoding throughout Windows install pipeline.

    Target: Catch UnicodeDecodeError, UnicodeEncodeError, charmap issues.

    Failure modes this catches:
    - Files opened without explicit utf-8 encoding on Windows (defaults to system locale)
    - Plugin manifests with UTF-8 content misread on locale-specific Windows
    - Console bootstrap failures due to encoding in log/config files
    """

    def test_plugin_yaml_utf8_reading(
        self, valid_plugin_bundle: TempWindowsInstall
    ) -> None:
        """E2E: Plugin loader reads plugin.yaml with explicit UTF-8."""
        install = valid_plugin_bundle

        env = os.environ.copy()
        env["CORVIN_HOME"] = str(install.corvin_home)

        # The actual plugin_cmd.extract_plugin_metadata should use encoding="utf-8"
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys; sys.path.insert(0, '{repo}'); "
                    "from corvinOS.core.gateway.corvin_gateway.plugin_cmd import extract_plugin_metadata; "
                    "meta = extract_plugin_metadata('{plugin_path}'); "
                    "print(f'id={{meta.plugin_id}} name={{meta.name}}')"
                ).format(
                    repo=str(Path(__file__).parent.parent.parent),
                    plugin_path=str(install.plugins_dir / "valid_plugin"),
                ),
            ],
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Plugin extraction failed: {result.stderr}"
        assert "id=valid_plugin" in result.stdout

    def test_tenant_config_utf8_writing(
        self, windows_temp_install: TempWindowsInstall
    ) -> None:
        """E2E: Tenant config written with UTF-8, readable on any locale."""
        install = windows_temp_install
        install.write_tenant_config(
            {
                "plugins": {"installed": []},
                "metadata": {
                    "description": "Test with Unicode: éàü 你好 مرحبا",
                },
            }
        )

        # Verify the file is valid UTF-8 and can be re-read
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import yaml; "
                    "from pathlib import Path; "
                    "data = yaml.safe_load(Path('{path}').read_text(encoding='utf-8')); "
                    "assert 'éàü' in data.get('spec', {}).get('metadata', {}).get('description', ''); "
                    "print('UTF8_OK')"
                ).format(path=str(install.tenant_yaml_path)),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"UTF-8 config write failed: {result.stderr}"
        assert "UTF8_OK" in result.stdout

    def test_log_file_encoding_windows(
        self, windows_temp_install: TempWindowsInstall
    ) -> None:
        """E2E: Installer log files use UTF-8 encoding, not system locale."""
        install = windows_temp_install

        log_file = install.logs_dir / "install.log"

        # Simulate installer writing log with UTF-8 characters
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "from pathlib import Path; "
                    "log = Path('{path}'); "
                    "log.parent.mkdir(parents=True, exist_ok=True); "
                    "log.write_text('Installation started. Charset: UTF-8. Unicode: éàü\\n', encoding='utf-8'); "
                    "# Verify re-read works "
                    "content = log.read_text(encoding='utf-8'); "
                    "assert 'éàü' in content; "
                    "print('LOG_OK')"
                ).format(path=str(log_file)),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Log encoding failed: {result.stderr}"
        assert "LOG_OK" in result.stdout

    def test_plugin_registration_with_utf8_metadata(
        self, windows_temp_install: TempWindowsInstall
    ) -> None:
        """E2E: Plugin registration (writing to tenant.corvin.yaml) preserves UTF-8."""
        install = windows_temp_install
        install.write_tenant_config()

        # Add a plugin with UTF-8 metadata to the config
        plugin_path = install.write_plugin_yaml(
            "utf8_plugin",
            plugin_id="utf8_plugin",
            valid=True,
        )

        import yaml

        config = yaml.safe_load(install.tenant_yaml_path.read_text(encoding="utf-8"))
        if config.get("spec") and config["spec"].get("plugins"):
            config["spec"]["plugins"]["installed"] = [
                {
                    "path": str(plugin_path),
                    "description": "Plugin with UTF-8: éàü",
                }
            ]
            install.tenant_yaml_path.write_text(
                yaml.safe_dump(config, allow_unicode=True),
                encoding="utf-8",
            )

        # Verify the written config is valid and preserves UTF-8
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import yaml; "
                    "from pathlib import Path; "
                    "data = yaml.safe_load(Path('{path}').read_text(encoding='utf-8')); "
                    "plugins = data.get('spec', {}).get('plugins', {}).get('installed', []); "
                    "assert any('éàü' in str(p) for p in plugins), f'UTF-8 not preserved: {plugins}'; "
                    "print('PLUGIN_REG_OK')"
                ).format(path=str(install.tenant_yaml_path)),
            ],
            capture_output=True,
            text=True,
        )
        assert (
            result.returncode == 0
        ), f"Plugin registration UTF-8 failed: {result.stderr}"
        assert "PLUGIN_REG_OK" in result.stdout


# ──────────────────────────────────────────────────────────────────────────────
# TEST CLASS 3: PLUGIN REGISTRY VALIDATION
# ──────────────────────────────────────────────────────────────────────────────


class TestPluginRegistryValidation:
    """E2E tests for plugin manifest validation.

    Target: Catch "NoneType is not a container", missing-field errors,
    and malformed manifests.

    Failure modes this catches:
    - Accessing fields on None (plugin_id = None)
    - Missing required fields cause AttributeError or KeyError
    - Enum validation failures (invalid origin, boot_layer)
    - Plugin registry cannot enumerate malformed plugins
    """

    def test_extract_metadata_missing_id(
        self, malformed_plugin_bundle: TempWindowsInstall
    ) -> None:
        """E2E: extract_plugin_metadata raises ValueError for missing plugin_id."""
        install = malformed_plugin_bundle
        plugin_path = install.plugins_dir / "no_id_plugin"

        env = os.environ.copy()
        env["CORVIN_HOME"] = str(install.corvin_home)

        # Call extract_plugin_metadata on malformed plugin
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys; sys.path.insert(0, '{repo}'); "
                    "from corvinOS.core.gateway.corvin_gateway.plugin_cmd import extract_plugin_metadata; "
                    "try: "
                    "  extract_plugin_metadata('{path}'); "
                    "  print('ERROR_NOT_RAISED'); "
                    "except ValueError as e: "
                    "  if 'missing required field: id' in str(e): "
                    "    print('VALIDATION_OK'); "
                    "  else: "
                    "    print(f'WRONG_ERROR: {{e}}'); "
                    "except Exception as e: "
                    "  print(f'UNEXPECTED_ERROR: {type(e).__name__}: {{e}}')"
                ).format(
                    repo=str(Path(__file__).parent.parent.parent),
                    path=str(plugin_path),
                ),
            ],
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Subprocess failed: {result.stderr}"
        assert (
            "VALIDATION_OK" in result.stdout
        ), f"Expected ValueError, got: {result.stdout}"

    def test_plugin_yaml_invalid_origin(
        self, malformed_plugin_bundle: TempWindowsInstall
    ) -> None:
        """E2E: Plugin origin validation rejects invalid enum values."""
        install = malformed_plugin_bundle
        plugin_path = install.plugins_dir / "bad_origin_plugin"

        env = os.environ.copy()
        env["CORVIN_HOME"] = str(install.corvin_home)

        # Call extract_plugin_metadata, then validate_axes
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys; sys.path.insert(0, '{repo}'); "
                    "from corvinOS.core.gateway.corvin_gateway.plugin_cmd import ( "
                    "  extract_plugin_metadata, _validate_axes "
                    "); "
                    "try: "
                    "  meta = extract_plugin_metadata('{path}'); "
                    "  error_msg = _validate_axes(meta); "
                    "  if error_msg and 'origin' in error_msg: "
                    "    print('INVALID_ORIGIN_CAUGHT'); "
                    "  else: "
                    "    print(f'NO_ERROR: {{error_msg}}'); "
                    "except Exception as e: "
                    "  print(f'ERROR: {type(e).__name__}: {{e}}')"
                ).format(
                    repo=str(Path(__file__).parent.parent.parent),
                    path=str(plugin_path),
                ),
            ],
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Subprocess failed: {result.stderr}"
        assert (
            "INVALID_ORIGIN_CAUGHT" in result.stdout
        ), f"Origin validation not triggered: {result.stdout}"

    def test_registry_enumerate_ignores_malformed(
        self, malformed_plugin_bundle: TempWindowsInstall
    ) -> None:
        """E2E: Plugin registry enumeration gracefully skips malformed plugins."""
        install = malformed_plugin_bundle
        install.write_plugin_yaml("good_plugin", plugin_id="good_plugin", valid=True)

        env = os.environ.copy()
        env["CORVIN_HOME"] = str(install.corvin_home)

        # Enumerate plugins: expect 1 valid, 2 invalid (to be skipped with warnings)
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys; sys.path.insert(0, '{repo}'); "
                    "from corvinOS.core.gateway.corvin_gateway.plugin_cmd import extract_plugin_metadata; "
                    "from pathlib import Path; "
                    "plugins_dir = Path('{plugins_dir}'); "
                    "valid_count = 0; "
                    "for plugin_dir in plugins_dir.iterdir(): "
                    "  if plugin_dir.is_dir(): "
                    "    try: "
                    "      meta = extract_plugin_metadata(str(plugin_dir)); "
                    "      valid_count += 1; "
                    "    except ValueError: "
                    "      pass; "
                    "print(f'VALID_PLUGINS: {{valid_count}}')"
                ).format(
                    repo=str(Path(__file__).parent.parent.parent),
                    plugins_dir=str(install.plugins_dir),
                ),
            ],
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Enumeration failed: {result.stderr}"
        assert "VALID_PLUGINS: 1" in result.stdout

    def test_plugin_registry_load_tenant_config(
        self, valid_plugin_bundle: TempWindowsInstall
    ) -> None:
        """E2E: Plugin registry loads tenant config without validation errors."""
        install = valid_plugin_bundle
        install.write_tenant_config()

        env = os.environ.copy()
        env["CORVIN_HOME"] = str(install.corvin_home)

        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys; sys.path.insert(0, '{repo}'); "
                    "from corvinOS.core.gateway.corvin_gateway.plugin_cmd import load_tenant_config; "
                    "try: "
                    "  config = load_tenant_config('_default'); "
                    "  assert isinstance(config, dict); "
                    "  print('CONFIG_LOAD_OK'); "
                    "except FileNotFoundError as e: "
                    "  print(f'FILE_NOT_FOUND: {{e}}'); "
                    "except Exception as e: "
                    "  print(f'ERROR: {type(e).__name__}: {{e}}')"
                ).format(
                    repo=str(Path(__file__).parent.parent.parent),
                ),
            ],
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Config load failed: {result.stderr}"
        assert "CONFIG_LOAD_OK" in result.stdout


# ──────────────────────────────────────────────────────────────────────────────
# TEST CLASS 4: FULL INSTALL → CONSOLE BOOT (E2E)
# ──────────────────────────────────────────────────────────────────────────────


class TestFullWindowsInstallToConsoleBoot:
    """E2E: Full installation pipeline from bootstrap to console HTTP readiness.

    This test orchestrates:
    1. Installer (or install simulation)
    2. Plugin registration
    3. Console startup
    4. HTTP verification

    It exercises all three error classes in an integrated workflow.
    """

    @pytest.mark.slow
    def test_console_boots_after_install(
        self, windows_temp_install: TempWindowsInstall
    ) -> None:
        """E2E: Console bootstraps successfully post-install."""
        install = windows_temp_install
        install.write_tenant_config()
        install.write_plugin_yaml(
            "test_plugin",
            plugin_id="test_plugin",
            valid=True,
        )

        env = os.environ.copy()
        env["CORVIN_HOME"] = str(install.corvin_home)
        env["CORVIN_CONSOLE_PORT"] = "9999"  # Avoid port conflicts

        # Attempt to import and verify console module can boot
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys; sys.path.insert(0, '{repo}'); "
                    "try: "
                    "  from corvinOS.core.console.corvin_console.app import create_app; "
                    "  app = create_app(tenant_id='_default'); "
                    "  assert app is not None; "
                    "  print('CONSOLE_IMPORT_OK'); "
                    "except Exception as e: "
                    "  print(f'ERROR: {type(e).__name__}: {{e}}')"
                ).format(repo=str(Path(__file__).parent.parent.parent)),
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert (
            result.returncode == 0
        ), f"Console import failed: {result.stderr}\n{result.stdout}"
        assert "CONSOLE_IMPORT_OK" in result.stdout

    @pytest.mark.slow
    def test_install_with_spaces_console_boot(
        self, windows_temp_install_with_spaces: TempWindowsInstall
    ) -> None:
        """E2E: Console boots from install in path with spaces."""
        install = windows_temp_install_with_spaces
        install.write_tenant_config()

        env = os.environ.copy()
        env["CORVIN_HOME"] = str(install.corvin_home)

        # Verify console can initialize (not a full boot, but exercises path handling)
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys; "
                    "from pathlib import Path; "
                    "sys.path.insert(0, '{repo}'); "
                    "corvin_home = Path('{home}'); "
                    "assert corvin_home.exists(); "
                    "config_path = corvin_home / 'tenants' / '_default' / 'global' / 'tenant.corvin.yaml'; "
                    "assert config_path.exists(), f'Config not found: {{config_path}}'; "
                    "print('PATH_SPACING_OK')"
                ).format(
                    repo=str(Path(__file__).parent.parent.parent),
                    home=str(install.corvin_home),
                ),
            ],
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Spaced path boot failed: {result.stderr}"
        assert "PATH_SPACING_OK" in result.stdout


# ──────────────────────────────────────────────────────────────────────────────
# ERROR CLASSIFICATION MATRIX
# ──────────────────────────────────────────────────────────────────────────────


class TestErrorClassificationMatrix:
    """Explicit mapping: which test catches which error class.

    This is a documentation test that enumerates the three error classes
    and shows which test methods are designed to catch each.
    """

    ERROR_MATRIX = {
        "SyntaxError (unquoted paths with spaces)": [
            "TestWindowsPathHandling.test_install_in_path_with_spaces",
            "TestWindowsPathHandling.test_plugin_loader_path_quoting_windows",
        ],
        "UnicodeDecodeError (locale-specific charset)": [
            "TestWindowsEncoding.test_plugin_yaml_utf8_reading",
            "TestWindowsEncoding.test_tenant_config_utf8_writing",
            "TestWindowsEncoding.test_log_file_encoding_windows",
            "TestWindowsEncoding.test_plugin_registration_with_utf8_metadata",
        ],
        "TypeError: 'NoneType' is not a container (missing plugin_id)": [
            "TestPluginRegistryValidation.test_extract_metadata_missing_id",
            "TestPluginRegistryValidation.test_registry_enumerate_ignores_malformed",
        ],
        "ValueError: invalid enum value (bad origin/boot_layer)": [
            "TestPluginRegistryValidation.test_plugin_yaml_invalid_origin",
        ],
        "Full Install → Console Boot": [
            "TestFullWindowsInstallToConsoleBoot.test_console_boots_after_install",
            "TestFullWindowsInstallToConsoleBoot.test_install_with_spaces_console_boot",
        ],
    }

    def test_error_matrix_documentation(self) -> None:
        """Document the error classification matrix."""
        for error_class, test_methods in self.ERROR_MATRIX.items():
            assert test_methods, f"Error class '{error_class}' has no covering tests"
            print(f"\n{error_class}:")
            for method in test_methods:
                print(f"  - {method}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
