"""Base test fixtures for plugin scaffolds (ADR-0262).

Provides:
- CorvinPluginTestCase: base class for plugin unit tests
- plugin_context_fixture: pytest fixture for plugin execution context
- plugin_registry_fixture: pytest fixture for registry access
- temp_plugin_home: pytest fixture for isolated plugin home directory

Each generated scaffold's conftest.py imports these fixtures and auto-wires
them into test functions.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any, Generator, TYPE_CHECKING
from unittest.mock import MagicMock, patch

if TYPE_CHECKING:
    import pytest
else:
    try:
        import pytest
    except ImportError:
        # pytest is optional at import time; only required when decorators are used
        pytest = None  # type: ignore


class PluginContext:
    """Minimal plugin execution context for testing.

    Attributes:
        tenant_id: Tenant scope for the plugin (default '_default')
        session_id: Session identifier for audit trail
        user_id: User identifier for audit trail
        plugin_id: The plugin being tested
        logger: Mock logger for capturing log output
    """

    def __init__(
        self,
        plugin_id: str,
        tenant_id: str = "_default",
        session_id: str = "test-session",
        user_id: str = "test-user",
    ):
        self.plugin_id = plugin_id
        self.tenant_id = tenant_id
        self.session_id = session_id
        self.user_id = user_id
        self.logger = MagicMock()
        self._audit_events: list[dict[str, Any]] = []

    def log_audit_event(self, event_type: str, payload: dict[str, Any]) -> None:
        """Record an audit event (for testing compliance)."""
        self._audit_events.append(
            {
                "event_type": event_type,
                "plugin_id": self.plugin_id,
                "tenant_id": self.tenant_id,
                "session_id": self.session_id,
                "user_id": self.user_id,
                "payload": payload,
            }
        )

    def get_audit_events(self) -> list[dict[str, Any]]:
        """Retrieve recorded audit events."""
        return self._audit_events.copy()


class PluginRegistry:
    """Mock plugin registry for testing plugin registration.

    Attributes:
        _instances: dict of plugin_id -> active plugin instance
        _configs: dict of plugin_id -> plugin configuration
    """

    def __init__(self):
        self._instances: dict[str, Any] = {}
        self._configs: dict[str, dict[str, Any]] = {}

    def register(
        self, plugin_id: str, instance: Any, config: dict[str, Any] | None = None
    ) -> None:
        """Register a plugin instance."""
        self._instances[plugin_id] = instance
        self._configs[plugin_id] = config or {}

    def get_active(self, plugin_id: str) -> Any:
        """Get the active instance of a plugin."""
        return self._instances.get(plugin_id)

    def set_active(self, plugin_id: str, instance: Any) -> None:
        """Set the active instance of a plugin."""
        self._instances[plugin_id] = instance

    def get_config(self, plugin_id: str) -> dict[str, Any]:
        """Get the configuration for a plugin."""
        return self._configs.get(plugin_id, {})

    def set_config(self, plugin_id: str, config: dict[str, Any]) -> None:
        """Set the configuration for a plugin."""
        self._configs[plugin_id] = config

    def clear(self) -> None:
        """Clear all registered plugins."""
        self._instances.clear()
        self._configs.clear()


def _fixture(fn: Any) -> Any:
    """Conditional fixture decorator that works with or without pytest."""
    if pytest is not None:
        return pytest.fixture(fn)
    return fn


@_fixture
def temp_plugin_home() -> Generator[Path, None, None]:
    """Provide an isolated temporary plugin home directory.

    Yields:
        Path: temporary directory, cleaned up after test
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        plugin_home = Path(tmpdir) / ".corvin"
        plugin_home.mkdir(parents=True, exist_ok=True)
        (plugin_home / "audit.jsonl").touch()
        yield plugin_home
        # Cleanup happens automatically via tempfile context


@_fixture
def plugin_context(request: Any) -> PluginContext:
    """Provide a PluginContext for testing.

    Uses the test class name (if available) or the test function name
    as the plugin_id.

    Returns:
        PluginContext: execution context for the plugin under test
    """
    if hasattr(request, "instance") and request.instance is not None:
        plugin_id = request.instance.__class__.__name__
    else:
        plugin_id = request.node.name
    return PluginContext(plugin_id=plugin_id)


@_fixture
def plugin_registry() -> PluginRegistry:
    """Provide a test plugin registry.

    Returns:
        PluginRegistry: mock registry for testing plugin registration
    """
    return PluginRegistry()


@_fixture
def mock_audit_backend(temp_plugin_home: Path) -> Generator[MagicMock, None, None]:
    """Provide a mock audit backend that writes to the temp home.

    Yields:
        MagicMock: mock audit backend with write_event method
    """
    audit_path = temp_plugin_home / "audit.jsonl"

    def write_event(event: dict[str, Any]) -> None:
        with open(audit_path, "a") as f:
            f.write(json.dumps(event) + "\n")

    mock = MagicMock()
    mock.write_event = write_event
    yield mock


class CorvinPluginTestCase(unittest.TestCase):
    """Base test class for plugin unit tests.

    Provides:
    - setUp/tearDown for plugin lifecycle
    - Helper methods for common test patterns
    - Audit event assertions
    """

    def setUp(self) -> None:
        """Initialize test fixtures."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.plugin_home = Path(self.temp_dir.name) / ".corvin"
        self.plugin_home.mkdir(parents=True, exist_ok=True)

        self.context = PluginContext(
            plugin_id=self.__class__.__name__,
            session_id="test-session",
            user_id="test-user",
        )
        self.registry = PluginRegistry()
        self.audit_events: list[dict[str, Any]] = []

    def tearDown(self) -> None:
        """Clean up test resources."""
        self.temp_dir.cleanup()
        self.audit_events.clear()

    def assert_audit_event(
        self, event_type: str, payload_keys: set[str] | None = None
    ) -> None:
        """Assert that an audit event was recorded.

        Args:
            event_type: The expected event type
            payload_keys: If provided, assert the payload contains these keys
        """
        events = [e for e in self.audit_events if e.get("event_type") == event_type]
        self.assertGreater(
            len(events),
            0,
            f"No audit event of type {event_type!r} recorded",
        )
        if payload_keys:
            for event in events:
                payload = event.get("payload", {})
                for key in payload_keys:
                    self.assertIn(
                        key,
                        payload,
                        f"Key {key!r} not found in {event_type!r} payload",
                    )

    def assert_no_audit_event(self, event_type: str) -> None:
        """Assert that no audit event of a given type was recorded.

        Args:
            event_type: The event type that should not appear
        """
        events = [e for e in self.audit_events if e.get("event_type") == event_type]
        self.assertEqual(
            len(events),
            0,
            f"Unexpected {event_type!r} audit event(s) recorded",
        )

    def register_plugin(
        self, plugin_id: str, instance: Any, config: dict[str, Any] | None = None
    ) -> None:
        """Register a plugin instance in the test registry.

        Args:
            plugin_id: The plugin identifier
            instance: The plugin instance
            config: Optional plugin configuration
        """
        self.registry.register(plugin_id, instance, config)

    def get_plugin(self, plugin_id: str) -> Any:
        """Retrieve a registered plugin instance.

        Args:
            plugin_id: The plugin identifier

        Returns:
            The registered plugin instance or None
        """
        return self.registry.get_active(plugin_id)


# ============================================================================
# Real-LLM Harness (Stream C: ADR-0262 Phase C)
# ============================================================================

import hashlib
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AuditEvent:
    """Immutable audit event with hash-chain integrity."""
    tenant_id: str
    event_type: str
    timestamp: str
    model: str
    input_hash: str
    output_hash: str
    cost_estimate: float
    latency_ms: int
    hash: str  # SHA256 of this event
    prev_hash: Optional[str] = None  # Previous event hash (chain)
    execution_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict."""
        return {
            "tenant_id": self.tenant_id,
            "event_type": self.event_type,
            "timestamp": self.timestamp,
            "model": self.model,
            "input_hash": self.input_hash,
            "output_hash": self.output_hash,
            "cost_estimate": self.cost_estimate,
            "latency_ms": self.latency_ms,
            "hash": self.hash,
            "prev_hash": self.prev_hash,
            "execution_id": self.execution_id,
            "payload": self.payload,
        }


class HaikuClassifier:
    """Real-LLM Classifier using Claude 3.5 Haiku (async-capable)."""

    MODEL = "claude-3-5-haiku-20241022"
    COST_ESTIMATE = 0.001  # ~$0.001 per call

    def __init__(self, api_key: Optional[str] = None):
        """Initialize Haiku classifier.

        Args:
            api_key: Anthropic API key (defaults to ANTHROPIC_API_KEY env var)
        """
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY environment variable not set")

        try:
            import anthropic
            self.client = anthropic.Anthropic(api_key=self.api_key)
        except ImportError:
            raise ImportError("anthropic package required: pip install anthropic")

    def classify_plugin_type(self, description: str) -> str:
        """Classify plugin type using real Haiku model.

        Args:
            description: Plugin description

        Returns:
            Plugin type: data_connector, provider, mcp_server, skill, auth_provider

        Raises:
            ValueError: If classification fails
        """
        start_time = time.time()

        prompt = f"""Klassifiziere diesen Plugin basierend auf der Beschreibung als einen von: [data_connector, provider, mcp_server, skill, auth_provider].

Beschreibung: {description}

Antworte NUR mit dem Typ (z.B. "data_connector"), ohne Erklärung."""

        try:
            response = self.client.messages.create(
                model=self.MODEL,
                max_tokens=50,
                messages=[{"role": "user", "content": prompt}],
            )
            result = response.content[0].text.strip().lower()

            # Validate result
            valid_types = ["data_connector", "provider", "mcp_server", "skill", "auth_provider"]
            if result not in valid_types:
                # Fuzzy match
                for vtype in valid_types:
                    if vtype in result:
                        result = vtype
                        break
                else:
                    raise ValueError(f"Invalid classification: {result}")

            latency_ms = int((time.time() - start_time) * 1000)
            return result

        except Exception as e:
            raise ValueError(f"Haiku classification failed: {e}")


class OpusCheckpointer:
    """Real-LLM Analyzer using Claude Opus for deep plugin analysis."""

    MODEL = "claude-opus-4-1-20250805"
    COST_ESTIMATE = 0.05  # ~$0.05 per call

    def __init__(self, api_key: Optional[str] = None):
        """Initialize Opus checkpointer.

        Args:
            api_key: Anthropic API key (defaults to ANTHROPIC_API_KEY env var)
        """
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY environment variable not set")

        try:
            import anthropic
            self.client = anthropic.Anthropic(api_key=self.api_key)
        except ImportError:
            raise ImportError("anthropic package required: pip install anthropic")

    def checkpoint_plugin_phase(self, scaffold_dir: Path) -> dict[str, Any]:
        """Analyze plugin scaffold using real Opus model.

        Args:
            scaffold_dir: Path to plugin scaffold directory

        Returns:
            dict with 'risks', 'test_cases', 'metrics' keys
        """
        start_time = time.time()

        # Load plugin.json metadata
        plugin_json = scaffold_dir / "plugin.json"
        plugin_meta = {}
        if plugin_json.exists():
            plugin_meta = json.loads(plugin_json.read_text())

        plugin_description = plugin_meta.get("description", "Unknown plugin")
        plugin_type = plugin_meta.get("type", "unknown")

        prompt = f"""Analysiere dieses Plugin-Scaffold und identifiziere:
1. Sicherheits- und Compliance-Risiken (nach GDPR Art. 5, 6, 32)
2. Test-Cases, die geschrieben werden sollten
3. Metriken zur Überwachung

Plugin-Typ: {plugin_type}
Beschreibung: {plugin_description}

Antworte als JSON mit folgendem Format:
{{
  "risks": ["Risiko 1", "Risiko 2", ...],
  "test_cases": [
    {{"name": "test_xyz", "description": "...", "expected_coverage": "error_handling"}},
    ...
  ],
  "metrics": {{
    "coverage_target": 0.85,
    "latency_slo_ms": 5000,
    "error_rate_threshold": 0.01
  }},
  "feasible": true,
  "comments": "..."
}}"""

        try:
            response = self.client.messages.create(
                model=self.MODEL,
                max_tokens=2000,
                messages=[{"role": "user", "content": prompt}],
            )

            result_text = response.content[0].text.strip()

            # Extract JSON from response
            try:
                # Try direct JSON parse first
                result = json.loads(result_text)
            except json.JSONDecodeError:
                # Extract JSON block if wrapped in markdown
                if "```json" in result_text:
                    result_text = result_text.split("```json")[1].split("```")[0]
                    result = json.loads(result_text)
                elif "```" in result_text:
                    result_text = result_text.split("```")[1].split("```")[0]
                    result = json.loads(result_text)
                else:
                    raise ValueError("Could not parse Opus response as JSON")

            latency_ms = int((time.time() - start_time) * 1000)

            # Ensure required fields
            if "risks" not in result:
                result["risks"] = []
            if "test_cases" not in result:
                result["test_cases"] = []
            if "metrics" not in result:
                result["metrics"] = {}
            if "feasible" not in result:
                result["feasible"] = True

            return result

        except Exception as e:
            raise ValueError(f"Opus checkpoint failed: {e}")


class PluginTestContext(PluginContext):
    """Extended PluginContext with real-LLM testing support and audit chain.

    Adds:
    - LLM client support (Haiku + Opus)
    - Audit-chain hash-linking
    - Cost tracking
    - Tenant isolation
    """

    def __init__(
        self,
        plugin_id: str,
        tenant_id: str = "_default",
        session_id: str = "test-session",
        user_id: str = "test-user",
        initialize_clients: bool = True,
    ):
        """Initialize test context with LLM support.

        Args:
            plugin_id: Plugin identifier
            tenant_id: Tenant for isolation
            session_id: Session identifier
            user_id: User identifier
            initialize_clients: If False, skip LLM client initialization (for testing)
        """
        super().__init__(plugin_id, tenant_id, session_id, user_id)
        self.haiku_client = None
        self.opus_client = None

        if initialize_clients:
            try:
                self.haiku_client = HaikuClassifier()
                self.opus_client = OpusCheckpointer()
            except ValueError:
                # API key not available, clients will be None
                pass

        self.total_cost_estimate = 0.0
        self._audit_chain: list[AuditEvent] = []
        self._prev_hash: Optional[str] = None

    def emit_audit_event(
        self,
        event_type: str,
        model: str,
        input_data: Any,
        output_data: Any,
        latency_ms: int,
        cost_estimate: float,
    ) -> AuditEvent:
        """Emit an audit event with hash-chain integrity.

        Args:
            event_type: Type of event (e.g., "test_started", "test_passed")
            model: LLM model used (e.g., "haiku", "opus")
            input_data: Input to the LLM
            output_data: Output from the LLM
            latency_ms: Execution latency in milliseconds
            cost_estimate: Estimated cost in dollars

        Returns:
            AuditEvent: the created event
        """
        from datetime import datetime, timezone

        input_hash = hashlib.sha256(
            json.dumps(input_data, sort_keys=True, default=str).encode()
        ).hexdigest()

        output_hash = hashlib.sha256(
            json.dumps(output_data, sort_keys=True, default=str).encode()
        ).hexdigest()

        timestamp = datetime.now(timezone.utc).isoformat()

        # Create event dict for hashing
        event_data = {
            "tenant_id": self.tenant_id,
            "event_type": event_type,
            "timestamp": timestamp,
            "model": model,
            "input_hash": input_hash,
            "output_hash": output_hash,
            "cost_estimate": cost_estimate,
            "latency_ms": latency_ms,
            "prev_hash": self._prev_hash,
        }

        # Hash this event
        event_hash = hashlib.sha256(
            json.dumps(event_data, sort_keys=True, default=str).encode()
        ).hexdigest()

        event = AuditEvent(
            tenant_id=self.tenant_id,
            event_type=event_type,
            timestamp=timestamp,
            model=model,
            input_hash=input_hash,
            output_hash=output_hash,
            cost_estimate=cost_estimate,
            latency_ms=latency_ms,
            hash=event_hash,
            prev_hash=self._prev_hash,
        )

        self._audit_chain.append(event)
        self._prev_hash = event_hash
        self.total_cost_estimate += cost_estimate

        return event

    def get_audit_events(self) -> list[AuditEvent]:
        """Get all audit events with chain integrity."""
        return self._audit_chain.copy()

    def verify_audit_chain(self) -> bool:
        """Verify hash-chain integrity of all audit events.

        Returns:
            True if chain is valid, False otherwise
        """
        if not self._audit_chain:
            return True

        prev_hash = None
        for event in self._audit_chain:
            if event.prev_hash != prev_hash:
                return False
            prev_hash = event.hash

        return True

    def get_total_cost_estimate(self) -> float:
        """Get total estimated cost for all LLM calls.

        Returns:
            Estimated cost in dollars
        """
        return self.total_cost_estimate
