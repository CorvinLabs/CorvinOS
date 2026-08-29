# JSON ↔ CSV Data Transformer Plugin

A complete, working example plugin for CorvinOS that demonstrates the full plugin lifecycle and best practices.

## Overview

This plugin provides bidirectional conversion between JSON and CSV formats with validation, error handling, and comprehensive configuration options.

**Plugin ID:** `data-transform-json-csv`  
**Version:** 1.0.0  
**Type:** Custom  
**Origin:** Community  
**Boot Layer:** Installed

## Features

- **JSON to CSV**: Convert JSON arrays to CSV with automatic header detection
- **CSV to JSON**: Parse CSV to JSON array of objects
- **CSV Validation**: Validate CSV structure and detect inconsistencies
- **Configuration**: Customizable row limits, strict/lenient modes, encoding
- **Audit Trail**: All operations are logged to the audit chain
- **Health Monitoring**: Built-in health check with detailed metrics
- **Error Handling**: Comprehensive error messages and recovery

## Installation

### Via Configuration File

Add to `tenant.corvin.yaml`:

```yaml
spec:
  plugins:
    installed:
      - id: "data-transform-json-csv"
        class_path: "plugins.data_transform_json_csv.plugin:DataTransformPlugin"
        config:
          max_rows: 10000
          strict_mode: false
          encoding: "utf-8"
```

### Manual Installation

```bash
# Copy plugin directory to your plugins location
cp -r data-transform-json-csv ~/.corvin/tenants/_default/plugins/installed/

# Or use the plugin CLI (once implemented)
corvin plugin install ./data-transform-json-csv
```

## Usage

### Python API

```python
from plugin import DataTransformPlugin

# Create and initialize plugin
plugin = DataTransformPlugin()
plugin.on_load(context)

# JSON to CSV
json_data = [
    {"id": "1", "name": "Alice", "age": "30"},
    {"id": "2", "name": "Bob", "age": "25"},
]
csv_result = plugin.json_to_csv(json_data)
print(csv_result)
# Output:
# id,name,age
# 1,Alice,30
# 2,Bob,25
```

### JSON to CSV Conversion

```python
# From Python list of dicts
json_list = [{"a": "1", "b": "2"}]
csv = plugin.json_to_csv(json_list)

# From JSON string
json_string = '[{"a": "1", "b": "2"}]'
csv = plugin.json_to_csv(json_string)

# With options
csv = plugin.json_to_csv(json_list, null_value="N/A")
```

### CSV to JSON Conversion

```python
csv_data = """id,name,score
1,Alice,95.5
2,Bob,87.3"""

# Basic conversion
json_result = plugin.csv_to_json(csv_data)
# Result: [{"id": "1", "name": "Alice", "score": "95.5"}, ...]

# With type inference
json_result = plugin.csv_to_json(csv_data, infer_types=True)
# Automatically converts numbers and booleans
```

### CSV Validation

```python
csv_data = "id,name\n1,Alice\n2,Bob"

validation = plugin.validate_csv(csv_data)
print(validation)
# Output:
# {
#   "is_valid": true,
#   "row_count": 2,
#   "field_count": 2,
#   "issues": []
# }
```

## Configuration

Configure via `config` section in plugin registration:

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `max_rows` | int | 10000 | Maximum rows allowed in conversions |
| `strict_mode` | bool | false | Fail on any inconsistency (lenient by default) |
| `encoding` | string | utf-8 | File encoding for CSV operations |

### Configuration Examples

**Strict mode (fail on any issue):**
```yaml
config:
  strict_mode: true
  max_rows: 5000
```

**Lenient mode (skip errors, log warnings):**
```yaml
config:
  strict_mode: false
  max_rows: 100000
```

## API Reference

### Plugin Lifecycle Methods

#### `on_load(ctx: PluginContext) -> None`

Called once after plugin discovery. Initializes the plugin and registers with the audit chain.

**Parameters:**
- `ctx`: PluginContext containing configuration, audit emitter, and registry handles

**Example:**
```python
def on_load(self, ctx):
    self._config = ctx.config or {}
    self._enabled = True
```

#### `on_unload() -> None`

Called on graceful shutdown or tenant reload. Performs cleanup.

#### `health_check() -> HealthStatus`

Called periodically by health monitor. Returns plugin status and metrics.

**Returns:** HealthStatus with:
- `ok`: boolean indicating plugin health
- `message`: human-readable status message
- `details`: dict with metrics (conversions_performed, version, config, etc.)

### Transformation Methods

#### `json_to_csv(json_data, **kwargs) -> str`

Convert JSON to CSV format.

**Parameters:**
- `json_data`: List of dicts or JSON string
- `**kwargs`: 
  - `null_value`: String to use for null values (default: "")

**Returns:** CSV string with headers

**Raises:**
- `ValueError`: If JSON is invalid or data structure is wrong
- `RuntimeError`: If strict_mode is enabled and limits exceeded

**Example:**
```python
data = [{"id": "1", "value": "x"}]
csv = plugin.json_to_csv(data)
```

#### `csv_to_json(csv_data, **kwargs) -> List[Dict[str, Any]]`

Convert CSV to JSON array.

**Parameters:**
- `csv_data`: CSV string with headers in first row
- `**kwargs`:
  - `infer_types`: Attempt type conversion (default: False)

**Returns:** List of dicts, one per CSV row

**Raises:**
- `ValueError`: If CSV is invalid or empty
- `RuntimeError`: If strict_mode is enabled and limits exceeded

**Example:**
```python
csv = "id,name\n1,Alice\n2,Bob"
json = plugin.csv_to_json(csv)
```

#### `validate_csv(csv_data: str) -> Dict[str, Any]`

Validate CSV structure.

**Parameters:**
- `csv_data`: CSV string to validate

**Returns:** Dict with:
- `is_valid`: boolean
- `row_count`: int
- `field_count`: int
- `issues`: list of error strings

**Example:**
```python
result = plugin.validate_csv(csv_data)
if result["is_valid"]:
    json = plugin.csv_to_json(csv_data)
```

## Audit Trail

All conversions are logged to the audit chain:

```
plugin.loaded               - Plugin initialization
plugin.unloaded             - Plugin shutdown
plugin.data_transform.json_to_csv  - JSON→CSV conversion started
plugin.data_transform.csv_to_json  - CSV→JSON conversion started
plugin.data_transform.error - Conversion error occurred
```

Each audit event includes:
- `plugin_id`: Plugin identifier
- `operation`: What was performed
- `record_count`: Number of records processed
- `field_count`: Number of fields
- `error_type`: Exception class (for errors)
- `error_message`: Exception message (for errors)

## Known Limitations

1. **No streaming**: Entire dataset loaded into memory. Use `max_rows` limit for large files.
2. **CSV parsing**: Uses Python's built-in `csv` module. Complex edge cases may differ from other CSV parsers.
3. **Type inference**: Best-effort only. Numeric strings that look like IDs will be converted.
4. **Headers required**: CSV input must have headers in the first row.
5. **No schema validation**: JSON to CSV uses first record's keys as schema.

## Error Handling

### Common Errors

**"JSON must be an array"**
- JSON input is a single object, not an array
- Solution: Wrap object in array: `[{...}]`

**"JSON array is empty"**
- Cannot generate CSV headers from empty array
- Solution: Provide at least one record

**"CSV has no data rows"**
- CSV has headers but no data
- Solution: Add data rows after header

**"Row count exceeds limit"**
- Dataset is larger than `max_rows` setting
- Solution: Increase `max_rows` or process in batches

### Lenient vs. Strict Mode

**Lenient (default: `strict_mode: false`)**
- Logs warnings but continues processing
- Silently truncates oversized datasets
- Skips malformed records
- Best for production, permissive on input

**Strict (`strict_mode: true`)**
- Raises exception on any inconsistency
- Fails on oversized datasets
- Requires perfect input format
- Best for data validation, strict on input

## Testing

Run the included tests:

```bash
# Unit tests
python -m pytest tests/test_plugin.py -v

# Integration tests
python -m pytest tests/test_integration.py -v

# All tests
python -m pytest tests/ -v
```

### Test Coverage

- **Lifecycle tests** (4): initialization, load, unload, health check
- **JSON→CSV tests** (8): basic, string input, empty, invalid, nulls, special chars, Unicode, row limits
- **CSV→JSON tests** (5): basic, empty, headers only, quoted fields, type inference
- **Validation tests** (3): valid, empty, inconsistent fields
- **Configuration tests** (2): invalid max_rows, invalid encoding
- **Integration tests** (7+): registration, lifecycle, roundtrips, large datasets, error recovery

**Total: 30+ test cases**

## Architecture Notes

### Plugin Lifecycle

1. **Discovery**: CorvinOS finds `manifest.yaml` and `plugin.py`
2. **Instantiation**: Plugin class is instantiated with no arguments
3. **Load**: `on_load(ctx)` called with PluginContext
4. **Runtime**: Plugin methods called by application code
5. **Unload**: `on_unload()` called during shutdown
6. **Health**: `health_check()` called periodically by monitor

### Data Flow

```
User Input (JSON/CSV)
    ↓
Plugin Method (json_to_csv / csv_to_json)
    ↓
Validation (config, strict_mode, limits)
    ↓
Conversion (csv.DictReader/DictWriter)
    ↓
Audit Trail (via ctx.audit_emit)
    ↓
Output (CSV/JSON)
    ↓
Metrics Update (_conversion_count, _last_error)
```

### Configuration Validation

- `max_rows`: Must be positive integer
- `encoding`: Must be in ("utf-8", "utf-16", "ascii", "latin-1")
- `strict_mode`: Must be boolean

Invalid values are corrected to defaults with a warning log.

## Design Decisions

1. **In-memory processing**: Simplest for common use cases. Stream-processing left for future versions.
2. **First-record schema**: CSV headers are extracted from first record. Consistent with common patterns.
3. **Lenient defaults**: `strict_mode: false` and `max_rows: 10000` provide safe defaults for unknown data.
4. **Audit on every operation**: Comprehensive audit trail supports compliance and debugging.
5. **No external dependencies**: Only uses Python stdlib (csv, json, io, logging).

## Contributing

This is an example plugin meant to demonstrate best practices. Contributions welcome:

1. Add new transformation types (YAML, XML, Parquet, etc.)
2. Add streaming support for large files
3. Add schema validation
4. Add performance optimizations
5. Improve error messages

## License

Apache-2.0 (same as CorvinOS)

## Support

- **Documentation**: See `/core/plugins/corvin_plugins/` for plugin system details
- **Examples**: This plugin directory (`data-transform-json-csv/`) is fully self-contained
- **Tests**: Comprehensive test suite in `tests/` directory
- **ADRs**: Plugin system documented in ADR-0030, ADR-0033, ADR-0233, ADR-0243, ADR-0249

## See Also

- Plugin system: `/core/plugins/corvin_plugins/`
- Templates: `/core/plugins/templates/`
- Protocol reference: `corvin_plugins.protocol`
- Audit system: `core/compliance/`
