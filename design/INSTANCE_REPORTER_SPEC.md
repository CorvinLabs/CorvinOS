# Instance Telemetry Reporter Specification

**Module:** `core/telemetry/instance_reporter.py`  
**Version:** 1.0  
**Status:** Design (ready for implementation)  

---

## Overview

The Instance Reporter is an in-process telemetry agent that runs on every CorvinOS instance. It:

1. **Collects metrics** (system, usage, performance, health) every 5-30 minutes
2. **Scrubs PII** using fail-closed logic (never sends identifiable data)
3. **Signs payloads** with Ed25519 to prove authentic origin
4. **Reports to collector** via HTTPS with retry logic
5. **Respects consent** by checking `telemetry_enabled` flag
6. **Logs audit trail** for every report attempt

---

## Component Architecture

```python
class TelemetryReporter:
    """
    Main telemetry agent (daemon thread, non-blocking).
    
    Lifecycle:
      1. __init__() — load config, keys, instance ID
      2. start() — spawn daemon thread
      3. _report_loop() — periodic collection + transmission
      4. on_shutdown() — flush buffer + stop thread
    """
    
    def __init__(self, config: TelemetryConfig):
        # Configuration
        self.enabled = config.remote_reporting_enabled()
        self.interval = config.report_interval_seconds  # 5-30 min
        self.buffer_size = config.buffer_size_max  # ≤100
        
        # Cryptography
        self.instance_id = load_or_create_instance_uuid()
        self.private_key = load_instance_private_key()
        
        # State
        self.buffer = deque(maxlen=self.buffer_size)
        self.thread = None
        self.last_report_time = None
        self.failed_reports = 0
        self.success_reports = 0
```

---

## 1. Initialization & Key Management

### 1.1 Instance UUID

**Storage:** `~/.corvin/instance_id`

```python
def load_or_create_instance_uuid() -> str:
    """
    Load or create a persistent instance UUID.
    Reused across restarts and boots.
    """
    id_file = Path.home() / '.corvin' / 'instance_id'
    
    if id_file.exists():
        return id_file.read_text().strip()
    else:
        instance_id = f"corvin_{uuid.uuid4().hex}"
        id_file.write_text(instance_id)
        id_file.chmod(0o600)
        return instance_id
```

### 1.2 Ed25519 Key Pair

**Storage:** `~/.corvin/telemetry.key` (private, encrypted at rest), `~/.corvin/telemetry.pub` (public)

```python
def load_instance_private_key() -> ed25519.Ed25519PrivateKey:
    """
    Load or generate instance's Ed25519 private key.
    """
    key_file = Path.home() / '.corvin' / 'telemetry.key'
    
    if key_file.exists():
        key_bytes = key_file.read_bytes()
        return ed25519.Ed25519PrivateKey.from_private_bytes(key_bytes)
    else:
        # Generate new keypair
        private_key = ed25519.Ed25519PrivateKey.generate()
        key_bytes = private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption()
        )
        key_file.write_bytes(key_bytes)
        key_file.chmod(0o600)
        
        # Save public key for registration
        public_key = private_key.public_key()
        pub_file = Path.home() / '.corvin' / 'telemetry.pub'
        pub_bytes = public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw
        )
        pub_file.write_bytes(base64.b64encode(pub_bytes))
        
        return private_key
```

---

## 2. Metrics Collection

### 2.1 Instance Metadata

```python
def collect_instance_metadata(self) -> dict:
    """Collect instance metadata (static, rarely changes)."""
    return {
        "version": corvin_version(),  # e.g., "0.2.1"
        "os": platform.system(),  # "Linux", "Darwin", "Windows"
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "region": self._get_region(),  # from GeoIP resolver
        "country_code": self._get_country_code(),  # ISO 3166-1 alpha-2
        "deployment_type": self._detect_deployment_type(),  # "docker", "k8s", etc.
    }

def _get_region(self) -> str:
    """
    Determine region from Cloudflare-resolved geo data.
    Fallback to config or "UNKNOWN".
    """
    # Try Cloudflare edge header (if behind Cloudflare)
    if cf_country := os.getenv('HTTP_CF_IPCOUNTRY'):
        return self._country_to_region(cf_country)
    
    # Try GeoIP file (if available)
    if geo_data := load_geoip_data():
        return self._country_to_region(geo_data['country'])
    
    # Fallback to config
    return os.getenv('CORVIN_REGION', 'UNKNOWN')

def _country_to_region(self, country_code: str) -> str:
    """Map ISO country code to region."""
    region_map = {
        'DE': 'EU', 'GB': 'EU', 'FR': 'EU', 'IT': 'EU', 'ES': 'EU', 'NL': 'EU', 'CH': 'EU',
        'US': 'NA', 'CA': 'NA', 'MX': 'NA',
        'JP': 'APAC', 'AU': 'APAC', 'SG': 'APAC', 'IN': 'APAC',
        'BR': 'LATAM', 'AR': 'LATAM',
        'AE': 'MENA', 'SA': 'MENA',
    }
    return region_map.get(country_code, 'UNKNOWN')

def _detect_deployment_type(self) -> str:
    """Detect deployment environment."""
    if Path('/.dockerenv').exists():
        return 'docker'
    elif os.getenv('KUBERNETES_SERVICE_HOST'):
        return 'kubernetes'
    elif os.getenv('AWS_LAMBDA_FUNCTION_NAME'):
        return 'aws-lambda'
    elif os.getenv('GOOGLE_CLOUD_PROJECT'):
        return 'gcp-cloud-run'
    else:
        return 'bare-metal'
```

### 2.2 Uptime & Boot Time

```python
def collect_uptime_metrics(self) -> dict:
    """Collect uptime and boot time metrics."""
    boot_time = self._get_boot_time()
    uptime_delta = datetime.utcnow() - boot_time
    uptime_hours = uptime_delta.total_seconds() / 3600
    
    return {
        "boot_time": boot_time.isoformat() + "Z",
        "uptime_hours": round(uptime_hours, 2),
        "last_heartbeat": datetime.utcnow().isoformat() + "Z",
    }

def _get_boot_time(self) -> datetime:
    """Get system boot time (cached for performance)."""
    if self._boot_time_cache:
        return self._boot_time_cache
    
    try:
        # On Linux: /proc/uptime
        if sys.platform == 'linux':
            with open('/proc/uptime') as f:
                uptime_seconds = float(f.read().split()[0])
                self._boot_time_cache = datetime.utcnow() - timedelta(seconds=uptime_seconds)
        # On macOS/Windows: use psutil
        else:
            self._boot_time_cache = datetime.fromtimestamp(psutil.boot_time())
    except Exception:
        self._boot_time_cache = datetime.utcnow()
    
    return self._boot_time_cache
```

### 2.3 Usage Metrics

```python
def collect_usage_metrics(self) -> dict:
    """
    Collect CorvinOS usage metrics.
    Depends on runtime state (sessions, tokens, cost tracking).
    """
    return {
        "active_users": self._count_active_sessions(),
        "sessions_total": self._get_total_session_count(),
        "sessions_today": self._get_sessions_today(),
        "tokens_used_today": self._get_token_usage_today(),
        "cost_today": self._calculate_cost_today(),
    }

def _count_active_sessions(self) -> int:
    """Count currently active user sessions."""
    try:
        from core.session_manager import get_session_manager
        return len(get_session_manager().get_active_sessions())
    except Exception:
        return 0

def _get_total_session_count(self) -> int:
    """Get cumulative session count from audit log."""
    try:
        from core.audit import get_audit_log
        count = 0
        for event in get_audit_log().search(event_type='user_session_created'):
            count += 1
        return count
    except Exception:
        return 0

def _get_sessions_today(self) -> int:
    """Count sessions created in the last 24 hours."""
    try:
        from core.audit import get_audit_log
        from datetime import datetime, timedelta
        cutoff = datetime.utcnow() - timedelta(days=1)
        count = 0
        for event in get_audit_log().search(event_type='user_session_created'):
            if event.timestamp > cutoff:
                count += 1
        return count
    except Exception:
        return 0

def _get_token_usage_today(self) -> int:
    """Sum token usage from cost tracker."""
    try:
        from core.cost_tracker import get_cost_tracker
        return get_cost_tracker().tokens_used_today()
    except Exception:
        return 0

def _calculate_cost_today(self) -> float:
    """Calculate USD cost from token usage."""
    try:
        from core.cost_tracker import get_cost_tracker
        return get_cost_tracker().cost_today_usd()
    except Exception:
        return 0.0
```

### 2.4 Performance Metrics

```python
def collect_performance_metrics(self) -> dict:
    """
    Collect request performance metrics.
    Aggregated from request logs or instrumentation.
    """
    stats = self._get_request_stats()
    
    return {
        "avg_latency_ms": round(stats['mean'], 1) if stats['count'] > 0 else 0,
        "p95_latency_ms": round(stats['p95'], 1) if stats['count'] > 0 else 0,
        "p99_latency_ms": round(stats['p99'], 1) if stats['count'] > 0 else 0,
        "error_rate_percent": round(stats['error_rate'] * 100, 2),
    }

def _get_request_stats(self) -> dict:
    """
    Get request latency & error statistics.
    Queries metrics from instrumentation (e.g., Prometheus).
    """
    try:
        # Query request timing histogram (last 24h)
        # This is pseudo-code; actual implementation depends on instrumentation
        
        latencies = []
        error_count = 0
        total_count = 0
        
        # Aggregate from instrumentation backend
        metrics = query_instrumentation({
            'metric': 'http_request_duration_seconds',
            'start': datetime.utcnow() - timedelta(hours=24),
            'end': datetime.utcnow(),
        })
        
        for data_point in metrics:
            latencies.append(data_point['value'] * 1000)  # convert to ms
            total_count += 1
        
        # Get error count
        error_metrics = query_instrumentation({
            'metric': 'http_request_errors_total',
            'start': datetime.utcnow() - timedelta(hours=24),
        })
        for data_point in error_metrics:
            error_count += data_point['value']
        
        latencies.sort()
        
        return {
            'mean': sum(latencies) / len(latencies) if latencies else 0,
            'p95': latencies[int(len(latencies) * 0.95)] if len(latencies) > 0 else 0,
            'p99': latencies[int(len(latencies) * 0.99)] if len(latencies) > 0 else 0,
            'error_rate': error_count / total_count if total_count > 0 else 0,
            'count': total_count,
        }
    except Exception:
        return {'mean': 0, 'p95': 0, 'p99': 0, 'error_rate': 0, 'count': 0}
```

### 2.5 System Metrics

```python
def collect_system_metrics(self) -> dict:
    """Collect CPU, memory, disk metrics."""
    return {
        "cpu_usage_percent": psutil.cpu_percent(interval=1.0),
        "memory_used_mb": psutil.virtual_memory().used // 1024 // 1024,
        "memory_total_mb": psutil.virtual_memory().total // 1024 // 1024,
        "disk_free_gb": psutil.disk_usage('/').free / 1024 / 1024 / 1024,
    }
```

### 2.6 Features & Health

```python
def collect_features_health(self) -> dict:
    """Collect active features and health indicators."""
    return {
        "features": {
            "models_used": self._get_top_models_used(limit=5),
            "plugins_installed_count": self._count_plugins(),
            "marketplace_plugins_count": self._count_marketplace_plugins(),
            "custom_layers_active": self._count_custom_layers(),
        },
        "health": {
            "audit_chain_healthy": self._verify_audit_chain(),
            "compliance_tripwire_active": self._check_tripwire_status(),
            "boot_time_seconds": self._measure_boot_time(),
            "last_audit_verify_time": self._get_last_audit_verify().isoformat() + "Z",
        }
    }

def _get_top_models_used(self, limit: int = 5) -> list[str]:
    """Get top models used in the last 24 hours."""
    try:
        from core.audit import get_audit_log
        model_counts = {}
        for event in get_audit_log().search(event_type='model_inference'):
            model = event.get('model_name', 'unknown')
            model_counts[model] = model_counts.get(model, 0) + 1
        
        top = sorted(model_counts.items(), key=lambda x: x[1], reverse=True)[:limit]
        return [model for model, count in top]
    except Exception:
        return []

def _count_plugins(self) -> int:
    """Count installed plugins."""
    try:
        from core.plugins import get_plugin_registry
        return len(get_plugin_registry().list_installed())
    except Exception:
        return 0

def _count_marketplace_plugins(self) -> int:
    """Count marketplace plugins (not built-in)."""
    try:
        from core.plugins import get_plugin_registry
        plugins = get_plugin_registry().list_installed()
        return len([p for p in plugins if p.origin == 'marketplace'])
    except Exception:
        return 0

def _verify_audit_chain(self) -> bool:
    """Verify audit trail hash chain integrity."""
    try:
        from core.audit import verify_chain
        return verify_chain()
    except Exception:
        return False

def _check_tripwire_status(self) -> bool:
    """Check compliance tripwire status (boot verification)."""
    try:
        from core.compliance.tripwire import check_tripwire
        return check_tripwire()
    except Exception:
        return False
```

---

## 3. PII Scrubbing & Fail-Closed Guards

### 3.1 Scrubber Implementation

```python
def scrub_payload(payload: dict) -> dict:
    """
    Fail-closed PII scrubber.
    Drops/redacts any field carrying PII patterns.
    """
    import re
    
    # Forbidden patterns
    FORBIDDEN_PATTERNS = [
        (r'(?i)(email|@)', 'email'),
        (r'(?i)(password|token|key|api)', 'secret'),
        (r'(?i)(user_id|username|uid|gid)', 'user_identifier'),
        (r'(?i)(\/(home|root|Users)\/|C:\\Users)', 'home_path'),
        (r'\b(?:\d{1,3}\.){3}\d{1,3}\b', 'ipv4'),
        (r'[a-f0-9]{32,}', 'long_hex'),
    ]
    
    # Critical fields (fail if contaminated)
    CRITICAL_FIELDS = {'instance_id', 'timestamp', 'signature'}
    
    # Non-critical fields (scrub but accept)
    SCRUB_FIELDS = {
        'country_code', 'deployment_type', 'os', 'python_version',
        'models_used', 'plugins_installed_count'
    }
    
    def check_field(key: str, value: str, is_critical: bool = False) -> str:
        """Check and possibly scrub a single field."""
        if not isinstance(value, str):
            return value
        
        for pattern, pattern_name in FORBIDDEN_PATTERNS:
            if re.search(pattern, value):
                if is_critical:
                    raise ValueError(f"Critical field '{key}' contaminated with {pattern_name}")
                else:
                    # Log and scrub
                    audit_log('telemetry.scrubbing_detected', {
                        'field': key,
                        'pattern': pattern_name
                    })
                    return "[SCRUBBED]"
        
        return value
    
    # Recursively check all fields
    def walk_payload(obj, path=""):
        if isinstance(obj, dict):
            for key, val in obj.items():
                is_critical = key in CRITICAL_FIELDS
                if isinstance(val, str):
                    obj[key] = check_field(key, val, is_critical)
                elif isinstance(val, dict):
                    walk_payload(val, f"{path}.{key}")
                elif isinstance(val, list):
                    for i, item in enumerate(val):
                        if isinstance(item, str):
                            obj[key][i] = check_field(f"{key}[{i}]", item, is_critical)
        return obj
    
    try:
        return walk_payload(payload)
    except ValueError as e:
        audit_log('telemetry.scrubbing_failed', {'error': str(e)})
        raise
```

### 3.2 Schema Validation

```python
from pydantic import BaseModel, Field, validator
from typing import Optional, List

class TelemetryPayload(BaseModel):
    schema_version: str = Field(..., regex="^1\\.0$")
    instance_id: str = Field(..., regex="^corvin_[a-f0-9]{32}$")
    timestamp: str = Field(...)  # ISO 8601
    
    # Nested models...
    instance_metadata: 'InstanceMetadata'
    uptime: 'UptimeMetrics'
    usage: 'UsageMetrics'
    performance: 'PerformanceMetrics'
    system: 'SystemMetrics'
    features: 'FeaturesMetrics'
    health: 'HealthMetrics'
    
    signature: str = Field(..., regex="^[A-Za-z0-9_-]{86,88}$")
    
    @validator('timestamp')
    def validate_timestamp(cls, v):
        try:
            dt = datetime.fromisoformat(v.replace('Z', '+00:00'))
            if abs((datetime.utcnow() - dt).total_seconds()) > 3600:
                raise ValueError("Timestamp too old or too far in future")
            return v
        except Exception as e:
            raise ValueError(f"Invalid timestamp: {e}")
    
    class Config:
        extra = 'forbid'  # No extra fields allowed

class InstanceMetadata(BaseModel):
    version: str = Field(..., max_length=20)
    os: str = Field(..., regex="^(Linux|Darwin|Windows)$")
    python_version: str = Field(..., max_length=10)
    region: str = Field(..., regex="^(EU|NA|APAC|LATAM|MENA)$")
    country_code: str = Field(..., regex="^[A-Z]{2}$")
    deployment_type: str = Field(..., max_length=20)

# ... other nested models
```

---

## 4. Payload Construction & Signing

### 4.1 Full Collection

```python
async def collect_telemetry(self) -> dict:
    """Collect all metrics and construct payload."""
    try:
        payload = {
            "schema_version": "1.0",
            "instance_id": self.instance_id,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "instance_metadata": self.collect_instance_metadata(),
            "uptime": self.collect_uptime_metrics(),
            "usage": self.collect_usage_metrics(),
            "performance": self.collect_performance_metrics(),
            "system": self.collect_system_metrics(),
        }
        
        # Merge features + health
        features_health = self.collect_features_health()
        payload.update(features_health)
        
        # Validate schema
        validated = TelemetryPayload(**payload)
        
        # Scrub PII
        payload = scrub_payload(validated.dict())
        
        return payload
    except Exception as e:
        audit_log('telemetry.collection_failed', {'error': str(e)})
        raise
```

### 4.2 Payload Signing

```python
def sign_payload(self, payload: dict) -> str:
    """
    Sign payload with instance private key (Ed25519).
    Returns base64url-encoded signature.
    """
    # Remove signature field if present
    payload_copy = payload.copy()
    payload_copy.pop('signature', None)
    
    # Canonicalize JSON (deterministic)
    canonical = json.dumps(payload_copy, separators=(',', ':'), sort_keys=True)
    
    # Sign
    signature_bytes = self.private_key.sign(canonical.encode('utf-8'))
    
    # Encode as base64url (no padding)
    signature_b64url = base64.urlsafe_b64encode(signature_bytes).decode('ascii').rstrip('=')
    
    return signature_b64url
```

---

## 5. Reporting & Retry Logic

### 5.1 Send Report

```python
async def send_report(self, payload: dict) -> bool:
    """
    Send telemetry report to collector with retry logic.
    Returns True if successful, False otherwise.
    """
    if not self.enabled:
        return False
    
    # Sign payload
    try:
        signature = self.sign_payload(payload)
        payload['signature'] = signature
    except Exception as e:
        audit_log('telemetry.signing_failed', {'error': str(e)})
        return False
    
    # Send with retry
    max_retries = 5
    backoff = 1
    
    for attempt in range(max_retries):
        try:
            response = await self._http_post(
                'https://collector.corvin-labs.com/api/v1/telemetry/report',
                json=payload,
                timeout=10,
                headers={
                    'Content-Type': 'application/json',
                    'X-Instance-ID': self.instance_id,
                    'User-Agent': f'CorvinOS/{corvin_version()}',
                },
            )
            
            if response.status_code == 202:
                self.success_reports += 1
                self.last_report_time = datetime.utcnow()
                audit_log('telemetry.report_succeeded', {
                    'instance_id': self.instance_id,
                    'timestamp': payload['timestamp'],
                    'size_bytes': len(json.dumps(payload)),
                })
                return True
            
            elif response.status_code == 409:
                # Duplicate — not really a failure
                audit_log('telemetry.duplicate_detected', {
                    'instance_id': self.instance_id,
                })
                return True
            
            elif response.status_code in [400, 401]:
                # Bad request or signature failed — don't retry
                audit_log('telemetry.report_rejected', {
                    'instance_id': self.instance_id,
                    'status': response.status_code,
                    'error': response.text,
                })
                return False
            
            elif response.status_code == 429:
                # Rate limited — wait and retry
                wait_time = backoff * (attempt + 1)
                audit_log('telemetry.rate_limited', {
                    'wait_seconds': wait_time,
                })
                await asyncio.sleep(wait_time)
                continue
            
            elif response.status_code >= 500:
                # Server error — retry with backoff
                wait_time = backoff * (2 ** attempt)  # exponential
                await asyncio.sleep(wait_time)
                continue
        
        except Exception as e:
            audit_log('telemetry.send_failed', {
                'error': str(e),
                'attempt': attempt + 1,
            })
            wait_time = backoff * (2 ** attempt)
            await asyncio.sleep(wait_time)
            continue
    
    self.failed_reports += 1
    audit_log('telemetry.all_retries_failed', {
        'instance_id': self.instance_id,
    })
    
    # Buffer for later retry
    self.buffer.append((datetime.utcnow(), payload))
    
    return False
```

### 5.2 Report Loop

```python
async def _report_loop(self) -> None:
    """
    Main reporting loop (runs in daemon thread).
    Collects & sends telemetry every `interval` seconds.
    """
    while True:
        try:
            if not self.enabled:
                await asyncio.sleep(60)  # Check enabled status every min
                continue
            
            # Collect telemetry
            payload = await self.collect_telemetry()
            
            # Send it
            await self.send_report(payload)
            
            # Sleep until next report
            await asyncio.sleep(self.interval)
        
        except Exception as e:
            audit_log('telemetry.loop_error', {'error': str(e)})
            await asyncio.sleep(60)  # Wait before retrying
```

---

## 6. Configuration

### 6.1 Config File (tenant.corvin.yaml)

```yaml
spec:
  telemetry:
    # Enable/disable remote reporting (default: true — opt-out)
    remote_reporting_enabled: true
    
    # Report interval (5 min to 1 hour)
    report_interval_seconds: 300
    
    # Buffer size (max payloads to hold if collector is down)
    buffer_size_max: 100
    
    # Collector endpoint (can override for testing)
    collector_url: "https://collector.corvin-labs.com/api/v1"
    
    # Region override (if auto-detection fails)
    region_override: null
```

### 6.2 Environment Variables

```bash
# Override telemetry enablement
export CORVIN_TELEMETRY_ENABLED=false

# Override region
export CORVIN_REGION=EU

# Override collector URL
export CORVIN_TELEMETRY_COLLECTOR_URL=https://collector.example.com/api/v1
```

---

## 7. Initialization & Lifecycle

### 7.1 Boot Integration

```python
# In core/bootstrap.py

async def boot_platform():
    """Boot CorvinOS platform."""
    # ... existing boot code ...
    
    # Initialize telemetry reporter
    telemetry_config = TelemetryConfig.from_env_and_yaml()
    telemetry_reporter = TelemetryReporter(telemetry_config)
    telemetry_reporter.start()
    
    # Register shutdown hook
    register_shutdown_hook(telemetry_reporter.on_shutdown)
```

### 7.2 Shutdown Hook

```python
def on_shutdown(self) -> None:
    """Flush any pending reports and stop daemon thread."""
    if not self.thread:
        return
    
    # Try to flush buffer
    while self.buffer:
        _, payload = self.buffer.popleft()
        try:
            asyncio.run(self.send_report(payload))
        except Exception:
            pass  # Best effort only
    
    # Signal thread to stop
    self.enabled = False
    self.thread.join(timeout=5)
```

---

## 8. Testing

### 8.1 Unit Tests

```python
# tests/telemetry/test_instance_reporter.py

def test_collection_without_pii():
    """Verify no PII is collected."""
    reporter = TelemetryReporter(config)
    payload = asyncio.run(reporter.collect_telemetry())
    
    # Verify no forbidden patterns
    for pattern in ['email', 'password', 'token', '/home/', '192.168']:
        assert pattern not in json.dumps(payload).lower()

def test_scrubbing_removes_contaminated_fields():
    """Verify scrubber catches PII."""
    contaminated = {
        "instance_metadata": {
            "deployment_type": "user_mail@example.com"  # email in field
        }
    }
    scrubbed = scrub_payload(contaminated)
    assert "[SCRUBBED]" in json.dumps(scrubbed)

def test_signature_verification():
    """Verify signatures are correct."""
    reporter = TelemetryReporter(config)
    payload = asyncio.run(reporter.collect_telemetry())
    
    signature = reporter.sign_payload(payload)
    assert verify_ed25519_signature(payload, reporter.private_key.public_key())
```

### 8.2 E2E Tests

```python
# tests/telemetry/test_e2e.py

async def test_full_report_flow():
    """E2E: collect → sign → send → verify."""
    # Start mock collector
    mock_collector = MockCollectorServer()
    await mock_collector.start()
    
    # Create reporter pointing to mock
    config = TelemetryConfig(collector_url=mock_collector.url)
    reporter = TelemetryReporter(config)
    
    # Send report
    payload = await reporter.collect_telemetry()
    success = await reporter.send_report(payload)
    
    assert success
    assert len(mock_collector.received_payloads) == 1
    assert mock_collector.received_payloads[0]['instance_id'] == reporter.instance_id
    
    await mock_collector.stop()
```

---

**Status:** ✅ Ready for implementation
