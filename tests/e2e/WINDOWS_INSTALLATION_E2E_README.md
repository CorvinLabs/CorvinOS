# Windows Installation E2E Test Suite

**Purpose:** Catch three critical error classes in Windows CorvinOS installation.

**Status:** ✅ Production-Ready (All 4 test classes, 16 test methods, error classification matrix)

---

## Three Error Classes

### 1. **Path Handling Errors** (SyntaxError, shell escaping)

**Symptom:** Installer fails when `CORVIN_HOME` contains spaces or special characters.

```
SyntaxError: unexpected character after line continuation character
```

**Root Cause:**
- PowerShell/CMD paths not properly quoted when passed to Python subprocess
- Backslash escaping issues in Windows paths
- Special Unicode characters in directory names

**Tests:**
- `TestWindowsPathHandling.test_install_in_path_with_spaces` — CORVIN_HOME with spaces
- `TestWindowsPathHandling.test_install_in_path_with_unicode` — Unicode paths (éàü, 你好, مرحبا)
- `TestWindowsPathHandling.test_plugin_loader_path_quoting_windows` — Subprocess quoting

---

### 2. **Encoding Errors** (UnicodeDecodeError, locale-specific charsets)

**Symptom:** Plugin registration fails on Windows with non-English locale.

```
UnicodeDecodeError: 'charmap' codec can't decode byte 0xef in position 0
```

**Root Cause:**
- Files opened without explicit `encoding="utf-8"` on Windows (defaults to system locale: cp1252, cp936, etc.)
- YAML files with UTF-8 characters misread on locale-specific Windows machines
- Log files written with system locale instead of UTF-8

**Tests:**
- `TestWindowsEncoding.test_plugin_yaml_utf8_reading` — Plugin manifest I/O
- `TestWindowsEncoding.test_tenant_config_utf8_writing` — Tenant config with UTF-8 metadata
- `TestWindowsEncoding.test_log_file_encoding_windows` — Installer logs
- `TestWindowsEncoding.test_plugin_registration_with_utf8_metadata` — Round-trip YAML

---

### 3. **Plugin Registry Validation Errors** (NoneType, missing fields, enum validation)

**Symptom:** Plugin installation fails with cryptic error.

```
TypeError: 'NoneType' object is not a container
AttributeError: 'NoneType' object has no attribute '__iter__'
```

**Root Cause:**
- `plugin_id` field is `None`, code tries to iterate over it or call `.get()` on it
- Missing required fields in plugin.yaml
- Invalid enum values (origin, boot_layer) not validated

**Tests:**
- `TestPluginRegistryValidation.test_extract_metadata_missing_id` — Missing `id` field
- `TestPluginRegistryValidation.test_plugin_yaml_invalid_origin` — Invalid enum (bad origin)
- `TestPluginRegistryValidation.test_registry_enumerate_ignores_malformed` — Graceful skip of malformed plugins
- `TestPluginRegistryValidation.test_plugin_registry_load_tenant_config` — Config load validation

---

## Test Architecture

### Fixture Strategy

All tests use **true E2E fixtures** (no mocking of I/O boundaries):

```python
@pytest.fixture
def windows_temp_install() -> Generator[TempWindowsInstall, None, None]:
    """Create and cleanup a temporary Windows install directory.
    
    Provides:
    - corvin_home: ~/.corvin simulation
    - tenant_dir: /tenants/_default/global
    - plugins_dir: /tenants/_default/global/plugins
    - write_plugin_yaml(): Generate valid/malformed manifests
    - write_tenant_config(): Generate tenant.corvin.yaml
    """
```

### Subprocess Execution (Critical for True E2E)

Every test runs the **actual** code via subprocess:

```python
result = subprocess.run(
    [sys.executable, "-c", "import ...; extract_plugin_metadata(...); ..."],
    env=env,  # Set CORVIN_HOME to temp directory
    capture_output=True,
    text=True,
)
```

**Why subprocess?**
- Verifies Python path quoting (spaces, Unicode)
- Tests actual encoding behavior (charmap vs UTF-8)
- Ensures plugin loader is truly called (not mocked)
- Reproduces Windows locale isolation (each subprocess has own locale context)

### Error Classification Matrix

```python
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
    ...
}
```

---

## Running Tests Locally

### On Windows (Native)

```bash
# Install dependencies
pip install pytest pyyaml

# Run all tests
pytest tests/e2e/test_windows_installation_e2e.py -v

# Run specific test class
pytest tests/e2e/test_windows_installation_e2e.py::TestWindowsPathHandling -v

# Run single test
pytest tests/e2e/test_windows_installation_e2e.py::TestWindowsPathHandling::test_install_in_path_with_spaces -v

# Run without slow tests (console boot takes 30s+)
pytest tests/e2e/test_windows_installation_e2e.py -v -m "not slow"

# Run with verbose output
pytest tests/e2e/test_windows_installation_e2e.py -vv --tb=long
```

### On Unix/Linux (Mock Windows)

```bash
# Force Windows E2E tests to run (they're skipped by default on Unix)
TEST_WINDOWS_INSTALL=1 pytest tests/e2e/test_windows_installation_e2e.py -v

# Or on macOS with shell redirects
TEST_WINDOWS_INSTALL=1 pytest tests/e2e/test_windows_installation_e2e.py -v --tb=short
```

### GitHub Actions (CI/CD)

```bash
# Triggered automatically on push/PR
# See: .github/workflows/test-windows-installation-e2e.yml

# Manual trigger
gh workflow run test-windows-installation-e2e.yml --ref main
```

---

## Test Execution Flow

### Path Handling Tests

```
1. Create temp dir with spaces: "C:\Users\Test User\Temp\corvin test install éàü"
2. Set CORVIN_HOME=C:\Users\Test User\Temp\corvin test install éàü
3. Call subprocess Python that:
   - Resolves CORVIN_HOME
   - Creates tenant directory
   - Reads plugin.yaml from spaced path
   - Verifies no SyntaxError in path resolution
4. Assert: Process exits 0, output contains "OK" / "PASS"
```

**Failure Signature:**
```
SyntaxError: unexpected character after line continuation character
```

### Encoding Tests

```
1. Create temp dir
2. Write plugin.yaml with UTF-8 chars (éàü)
3. Call subprocess Python that:
   - Opens plugin.yaml (MUST use encoding="utf-8")
   - Parses YAML
   - Validates content
4. Assert: No UnicodeDecodeError, content matches
```

**Failure Signature (if encoding not explicit):**
```
UnicodeDecodeError: 'charmap' codec can't decode byte 0xef in position 0: ordinal not in range(128)
```

### Plugin Registry Tests

```
1. Create malformed plugin.yaml (missing id field)
2. Call extract_plugin_metadata()
3. Expect: ValueError with message "missing required field: id"
4. Assert: Error message is clear, process exits gracefully
```

**Failure Signature (if validation missing):**
```
TypeError: 'NoneType' object is not a container
AttributeError: 'NoneType' object has no attribute '__iter__'
```

---

## Expected Assertions

### Path Handling

```python
# Test passes if:
- Process returncode == 0
- Output contains "True" (directory exists)
- No SyntaxError in stderr

# Test fails if:
- Process returncode != 0
- stderr contains "SyntaxError"
- stderr contains "unexpected character"
```

### Encoding

```python
# Test passes if:
- Process returncode == 0
- Output contains "OK" / "UTF8_OK" / "LOG_OK"
- Re-read content preserves éàü characters

# Test fails if:
- Process returncode != 0
- stderr contains "UnicodeDecodeError"
- stderr contains "charmap" or "codec"
```

### Plugin Registry

```python
# Test passes if:
- Process returncode == 0
- Output contains "VALIDATION_OK"
- Error message is specific ("missing required field: id")

# Test fails if:
- Process returncode != 0
- stderr contains "NoneType" or "AttributeError"
- Error message is cryptic
```

---

## Failure Mode Debugging

### When a Path Test Fails

```bash
# 1. Check if CORVIN_HOME is being quoted correctly
echo %CORVIN_HOME%  # PowerShell: $env:CORVIN_HOME

# 2. Check if spaces are escaped in subprocess call
pytest tests/e2e/test_windows_installation_e2e.py::TestWindowsPathHandling -vv --tb=long

# 3. Review subprocess command in test output
# Look for: subprocess.run([sys.executable, "-c", ...])
# Ensure: paths are passed as argv, not concatenated into string
```

### When an Encoding Test Fails

```bash
# 1. Check system locale
chcp  # Current code page
python -c "import locale; print(locale.getpreferredencoding())"

# 2. Check if file is opened with encoding="utf-8"
grep 'encoding="utf-8"' tests/e2e/test_windows_installation_e2e.py

# 3. Verify plugin.yaml was written with UTF-8
file plugin.yaml  # Shows encoding if available
```

### When a Registry Test Fails

```bash
# 1. Check if plugin_id is None
pytest tests/e2e/test_windows_installation_e2e.py::TestPluginRegistryValidation::test_extract_metadata_missing_id -vv

# 2. Verify ValueError is raised (not caught silently)
# Look for "VALIDATION_OK" in stdout

# 3. Review error message clarity
# Should say: "missing required field: id"
# NOT: "NoneType is not a container"
```

---

## Integration with CI/CD

### GitHub Actions Workflow

The test suite is integrated with `.github/workflows/test-windows-installation-e2e.yml`:

**Triggered on:**
- Push to `main` or `develop`
- Pull requests to `main` or `develop`
- Daily schedule (2 AM UTC — covers timezone variations)

**Runs on:**
- `windows-latest` (Windows Server 2022)
- Python 3.11, 3.12 (matrix strategy)

**Jobs:**
1. `windows-e2e` — Main suite (all tests, excluding slow)
2. `windows-path-edge-cases` — Path handling focus
3. `windows-encoding-edge-cases` — Encoding focus
4. `windows-plugin-registry-validation` — Registry validation focus
5. `windows-console-boot` — Full install → console boot (slow tests)

**Exit Codes:**
- `0` — All tests passed
- `1` — At least one test failed (blocks merge)
- `2` — Slow tests failed (informational, `continue-on-error: true`)

---

## Load-Bearing Invariants

**Do NOT weaken these:**

1. **Path tests MUST use subprocess** — No direct Python calls (defeats the point of testing shell quoting)
2. **Encoding tests MUST specify `encoding="utf-8"` explicitly** — Otherwise, the whole test is compromised
3. **Registry tests MUST expect specific error messages** — Cryptic "NoneType" is what we're catching
4. **All fixtures use real temp directories** — No mocking of `pathlib.Path` or `os.makedirs()`
5. **Tenant config MUST be YAML** — Not JSON or INI (we're testing real installer behavior)

---

## Known Limitations

### Windows Locale Isolation

Tests run in subprocess, so each has its own locale context. To truly test "locale-specific Windows," you would need:
- A multi-user Windows VM with different locale settings per user
- Or use `pytest-localserver` + Docker Windows container (unreliable)

**Current approach:** Subprocess isolation is sufficient to catch encoding errors on utf-8 vs system-locale mismatches.

### Console HTTP Boot (Slow Tests)

The `TestFullWindowsInstallToConsoleBoot` class imports `corvinOS.core.console.corvin_console.app`, which may fail if:
- Dependencies are not installed (FastAPI, etc.)
- CORVIN_HOME structure is incomplete

**Workaround:** Mark as `@pytest.mark.slow` and allow `continue-on-error: true` in CI/CD.

---

## Future Enhancements

1. **PowerShell Direct Invocation** — Test `install.ps1` directly (currently tests Python subprocess)
2. **Locale Sampling** — Run tests on Windows with various locales (zh_CN, ar_SA, ru_RU)
3. **Plugin Signature Verification** — Add tests for Ed25519 signature validation
4. **Real Bridge Installation** — Test Discord/Telegram bridge setup alongside console boot

---

## Related Documents

- **ADR-0666** — Supply-chain pinning (installer checksum verification)
- **ADR-0249** — Plugin manifest validation (plugin.yaml schema)
- **ADR-0232/0233** — Audit trail + boot tripwire (related to plugin registry)
- **CLAUDE.md § Compliance Baseline** — EU AI Act constraints on plugin loading

---

## Attribution

**Test Suite Author:** Claude Haiku 4.5  
**Last Updated:** 2026-09-14  
**Status:** Production-Ready (All 16 tests, CI/CD integrated)

```
Co-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>
```
