# Windows Installation E2E — Failure Mode Matrix

**Purpose:** Explicit mapping of which test catches which error class.

---

## Three Error Classes & Test Coverage

| Error Class | Symptom | Root Cause | Tests | Status |
|---|---|---|---|---|
| **SyntaxError: Unquoted Paths** | `unexpected character after line continuation` | Path with spaces passed unquoted to subprocess | `test_install_in_path_with_spaces` `test_plugin_loader_path_quoting_windows` | ✅ Covered |
| **UnicodeDecodeError: Locale Charset** | `'charmap' codec can't decode byte 0xef` | Files opened without `encoding="utf-8"` on Windows | `test_plugin_yaml_utf8_reading` `test_tenant_config_utf8_writing` `test_log_file_encoding_windows` `test_plugin_registration_with_utf8_metadata` | ✅ Covered |
| **TypeError: 'NoneType' Not Container** | `'NoneType' object is not a container` | Missing `plugin_id` field in manifest | `test_extract_metadata_missing_id` `test_registry_enumerate_ignores_malformed` | ✅ Covered |
| **ValueError: Invalid Enum** | `origin X is not one of [...]` | Invalid enum value (origin, boot_layer) | `test_plugin_yaml_invalid_origin` | ✅ Covered |
| **Full E2E: Install → Console Boot** | Console fails to start post-install | Cascading failure from above 4 classes | `test_console_boots_after_install` `test_install_with_spaces_console_boot` | ✅ Covered |

---

## Test Class Breakdown

### TestWindowsPathHandling (3 tests)

Catches: **SyntaxError from unquoted paths with spaces**

| Test | Scenario | Assertion | Expected Failure |
|---|---|---|---|
| `test_install_in_path_with_spaces` | CORVIN_HOME=`C:\Users\Test User\AppData\...` | Process exits 0, "True" in stdout | SyntaxError if path not quoted |
| `test_install_in_path_with_unicode` | Plugin loaded from path with éàü/你好/مرحبا | Process exits 0, "OK" in stdout | SyntaxError or UnicodeError if encoding not handled |
| `test_plugin_loader_path_quoting_windows` | Simulate plugin_cmd.py call with spaced path | Process exits 0, "PASS" in stdout | FileNotFoundError if path not passed as argv |

**Failure Detection:**
```
stderr contains "SyntaxError"
stderr contains "unexpected character"
returncode != 0
```

---

### TestWindowsEncoding (4 tests)

Catches: **UnicodeDecodeError from locale-specific charsets**

| Test | Scenario | Assertion | Expected Failure |
|---|---|---|---|
| `test_plugin_yaml_utf8_reading` | Read plugin.yaml from temp dir (uses subprocess) | Process exits 0, "id=..." in stdout | UnicodeDecodeError if encoding not specified |
| `test_tenant_config_utf8_writing` | Write tenant.corvin.yaml with UTF-8 metadata, re-read | Process exits 0, "UTF8_OK" in stdout | UnicodeDecodeError on read, or loss of éàü |
| `test_log_file_encoding_windows` | Installer writes log with UTF-8, re-reads | Process exits 0, "LOG_OK" in stdout | UnicodeDecodeError, or charmap errors |
| `test_plugin_registration_with_utf8_metadata` | Add plugin with UTF-8 description to config, round-trip | Process exits 0, "PLUGIN_REG_OK" in stdout | UnicodeDecodeError on YAML re-read |

**Failure Detection:**
```
stderr contains "UnicodeDecodeError"
stderr contains "charmap"
stderr contains "codec"
returncode != 0
```

---

### TestPluginRegistryValidation (4 tests)

Catches: **TypeError/ValueError from missing/invalid plugin fields**

| Test | Scenario | Assertion | Expected Failure |
|---|---|---|---|
| `test_extract_metadata_missing_id` | Plugin.yaml with no `id` field | Process exits 0, "VALIDATION_OK" in stdout | `TypeError: 'NoneType' is not ...` if validation missing |
| `test_plugin_yaml_invalid_origin` | Plugin.yaml with `origin: invalid_enum` | Process exits 0, "INVALID_ORIGIN_CAUGHT" in stdout | No validation error (silent failure) |
| `test_registry_enumerate_ignores_malformed` | Registry enumerates 3 plugins (1 valid, 2 malformed) | Process exits 0, "VALID_PLUGINS: 1" in stdout | Crashes on malformed plugins |
| `test_plugin_registry_load_tenant_config` | Load tenant.corvin.yaml as raw dict | Process exits 0, "CONFIG_LOAD_OK" in stdout | FileNotFoundError or validation error |

**Failure Detection:**
```
stderr contains "NoneType"
stderr contains "AttributeError"
stdout does not contain "VALIDATION_OK"
returncode != 0
```

---

### TestFullWindowsInstallToConsoleBoot (2 tests)

Catches: **Cascading failures from all above classes**

| Test | Scenario | Assertion | Expected Failure |
|---|---|---|---|
| `test_console_boots_after_install` | Full install → import console app | Process exits 0, "CONSOLE_IMPORT_OK" in stdout | Any of the above 4 errors manifests here |
| `test_install_with_spaces_console_boot` | Full install from spaced path → bootstrap | Process exits 0, "PATH_SPACING_OK" in stdout | Path handling + encoding combined |

**Failure Detection:**
```
stderr contains any of: SyntaxError, UnicodeDecodeError, TypeError, ValueError
returncode != 0
returncode timeout (30s)
```

---

## Which Test Catches Which Error?

### Error: `SyntaxError: unexpected character after line continuation character`

**Caught by:**
- ✅ `TestWindowsPathHandling.test_install_in_path_with_spaces`
- ✅ `TestWindowsPathHandling.test_plugin_loader_path_quoting_windows`
- ✅ `TestFullWindowsInstallToConsoleBoot.test_install_with_spaces_console_boot`

**NOT caught by:**
- ❌ Encoding tests (different error class)
- ❌ Registry validation tests (different root cause)

---

### Error: `UnicodeDecodeError: 'charmap' codec can't decode byte 0xef`

**Caught by:**
- ✅ `TestWindowsEncoding.test_plugin_yaml_utf8_reading`
- ✅ `TestWindowsEncoding.test_tenant_config_utf8_writing`
- ✅ `TestWindowsEncoding.test_log_file_encoding_windows`
- ✅ `TestWindowsEncoding.test_plugin_registration_with_utf8_metadata`
- ✅ `TestFullWindowsInstallToConsoleBoot.test_install_with_spaces_console_boot` (if UTF-8 in path)

**NOT caught by:**
- ❌ Path spacing tests (no UTF-8 content)
- ❌ Registry validation tests (no I/O with encoding)

---

### Error: `TypeError: 'NoneType' object is not a container`

**Caught by:**
- ✅ `TestPluginRegistryValidation.test_extract_metadata_missing_id`
- ✅ `TestPluginRegistryValidation.test_registry_enumerate_ignores_malformed`
- ✅ `TestFullWindowsInstallToConsoleBoot.test_console_boots_after_install`

**NOT caught by:**
- ❌ Path tests (no manifest parsing)
- ❌ Encoding tests (no registry validation)

---

### Error: `ValueError: origin X is not one of [...]`

**Caught by:**
- ✅ `TestPluginRegistryValidation.test_plugin_yaml_invalid_origin`

**NOT caught by:**
- ❌ Other tests (specific to enum validation)

---

## Test Execution Order (Recommended)

```bash
# Phase 1: Quick validation (5 min)
pytest tests/e2e/test_windows_installation_e2e.py::TestWindowsPathHandling -v
pytest tests/e2e/test_windows_installation_e2e.py::TestWindowsEncoding -v
pytest tests/e2e/test_windows_installation_e2e.py::TestPluginRegistryValidation -v

# Phase 2: Integration (30 min, slow)
pytest tests/e2e/test_windows_installation_e2e.py::TestFullWindowsInstallToConsoleBoot -v

# Phase 3: Error matrix validation (documentation)
pytest tests/e2e/test_windows_installation_e2e.py::TestErrorClassificationMatrix -v
```

---

## Subprocess Execution Model (Critical)

Every test runs the **actual** Python code via subprocess:

```python
result = subprocess.run(
    [sys.executable, "-c", "actual_import_and_call_code()"],
    env={"CORVIN_HOME": temp_path},
    capture_output=True,
    text=True,
)
```

**Why subprocess?**
1. ✅ Tests real path quoting (shell argument parsing)
2. ✅ Tests real encoding (charmap vs UTF-8 per system locale)
3. ✅ Tests real plugin loading (not mocked)
4. ✅ Isolates locale context (each subprocess can have different locale settings)

**What subprocess DOES NOT test:**
- ❌ PowerShell/CMD quoting (would need `powershell -Command "..."`)
- ❌ Windows Defender firewall rules
- ❌ Registry (Windows Registry, not plugin registry)
- ❌ Service installation (Windows Services API)

---

## Failure Scenario Examples

### Scenario 1: User installs to `C:\Users\John Doe\AppData\Roaming\CorvinOS`

**What fails:** Installer subprocess call

```powershell
# WRONG (path not quoted):
python -c "import os; os.makedirs(C:\Users\John Doe\AppData\Roaming\CorvinOS)"
# → SyntaxError: unexpected character after line continuation

# RIGHT (path quoted):
python -c "import os; os.makedirs('C:\\Users\\John Doe\\AppData\\Roaming\\CorvinOS')"
# → Works
```

**Caught by:** `TestWindowsPathHandling.test_install_in_path_with_spaces`

---

### Scenario 2: User runs CorvinOS on Windows with Chinese locale (zh_CN, cp936)

**What fails:** Plugin manifest loading

```python
# WRONG (no encoding specified):
data = yaml.safe_load(Path("plugin.yaml").read_text())
# → UnicodeDecodeError: 'charmap' codec can't decode byte 0xef in position 0
# (Windows tries cp936, fails on UTF-8 BOM)

# RIGHT (UTF-8 explicit):
data = yaml.safe_load(Path("plugin.yaml").read_text(encoding="utf-8"))
# → Works
```

**Caught by:** `TestWindowsEncoding.test_plugin_yaml_utf8_reading`

---

### Scenario 3: User adds a malformed plugin (missing `id` field)

**What fails:** Plugin registry enumeration

```python
# WRONG (no validation):
plugin_id = manifest.get("id")  # Returns None
for char in plugin_id:  # Tries to iterate None
    ...
# → TypeError: 'NoneType' object is not iterable

# RIGHT (explicit validation):
plugin_id = manifest.get("id")
if not plugin_id:
    raise ValueError("plugin.yaml missing required field: id")
# → Raises clear error
```

**Caught by:** `TestPluginRegistryValidation.test_extract_metadata_missing_id`

---

## Integration with CI/CD

### GitHub Actions Workflow Structure

```
.github/workflows/test-windows-installation-e2e.yml
├── Job: windows-e2e (main suite, matrix Python 3.11/3.12)
├── Job: windows-path-edge-cases
├── Job: windows-encoding-edge-cases
├── Job: windows-plugin-registry-validation
├── Job: windows-console-boot (slow, continue-on-error)
└── Job: test-summary (reports overall result)
```

**Triggered on:**
- Push to main/develop
- Pull request to main/develop
- Daily schedule (2 AM UTC)

**Exit code:**
- 0 = All tests pass (PR can merge)
- 1 = At least one test fails (PR blocked)
- 2 = Slow tests fail (informational only)

---

## Known Issues & Workarounds

### Issue 1: UnicodeError in Test Output

**Symptom:** GitHub Actions logs show garbled characters

**Cause:** Output encoding not set to UTF-8

**Workaround:**
```yaml
env:
  PYTHONIOENCODING: utf-8
```

---

### Issue 2: Slow Tests Timeout

**Symptom:** `TestFullWindowsInstallToConsoleBoot` times out after 60s

**Cause:** Console boot takes time to initialize all subsystems

**Workaround:**
```yaml
continue-on-error: true
timeout-minutes: 30
```

---

### Issue 3: Locale Variations

**Symptom:** Test passes on Windows Server 2022 (en_US), fails on user machine (zh_CN)

**Cause:** Subprocess locale context not isolated

**Workaround:** Tests are designed for subprocess isolation; if failures occur, check system locale:
```powershell
chcp  # Current code page
```

---

## Validation Checklist

Before merging a change to the installer or plugin loader:

- [ ] All tests in `TestWindowsPathHandling` pass
- [ ] All tests in `TestWindowsEncoding` pass
- [ ] All tests in `TestPluginRegistryValidation` pass
- [ ] GitHub Actions workflow completed successfully
- [ ] Error classification matrix is up to date
- [ ] No new subprocess calls are added without explicit `encoding="utf-8"`
- [ ] All paths in subprocess calls are passed as argv, not concatenated

---

## Attribution

**Matrix Author:** Claude Haiku 4.5  
**Last Updated:** 2026-09-14  
**Version:** 1.0.0 (Production)

```
Co-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>
```
