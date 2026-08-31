"""DataSourceRegistry — 4-tier adapter + connection discovery (ADR-0026 D / ADR-0106 M2).

Discovery order (tenant adapter REPLACES bundle adapter of same name):
  1. system  — /etc/corvin/datasource_adapters/
  2. bundle  — <plugin_root>/corvin_compute/fabric/datasources/builtin/
  3. tenant  — <corvin_home>/tenants/<tenant_id>/datasource_adapters/
  4. user    — ~/.config/corvin/datasource_adapters/

Connections (manifests) are looked up from:
  - <corvin_home>/tenants/<tenant_id>/datasource_connections/

ADR-0106 M2 additions:
  - register(): L34/L35 gate check + audit-first write
  - unregister(): audit-first delete
  - test_connection(): ping with audit
  - describe_adapter(): DSI v1 class-level metadata
  - list_connections_v1(): DSI v1 manifests only
"""
from __future__ import annotations

import dataclasses
import importlib
import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Optional

from .manifest import (
    ConnectionManifest,
    DSIv1ConnectionManifest,
    DSIv1PolicyError,
    is_dsiv1_manifest,
    validate_dsiv1_manifest,
    validate_manifest,
)
from .protocol import (
    BaseDataSourceAdapter,
    DataSourceAdapter,
    PingResult,
    SourceConfig,
)

# AuditWriter: (event_type, severity, details) -> None
AuditWriter = Callable[[str, str, dict[str, Any]], None]


# ---------------------------------------------------------------------------
# Summary dataclasses (returned by list_ methods)
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class AdapterManifest:
    name: str
    module: str
    version: str = "unknown"
    tier: str = "bundle"  # system | bundle | tenant | user


@dataclasses.dataclass
class ConnectionSummary:
    name: str
    adapter: str
    region: str
    tags: list[str] = dataclasses.field(default_factory=list)


# ---------------------------------------------------------------------------
# Built-in adapter registry
# ---------------------------------------------------------------------------

# Maps adapter name → dotted module path within the package.
_BUILTIN_ADAPTERS: dict[str, str] = {
    "local_file":   "corvin_compute.fabric.datasources.builtin.local_file",
    "postgresql":   "corvin_compute.fabric.datasources.builtin.postgresql",
    "mysql":        "corvin_compute.fabric.datasources.builtin.mysql",
    "s3_parquet":   "corvin_compute.fabric.datasources.builtin.s3_parquet",
    "s3_csv":       "corvin_compute.fabric.datasources.builtin.s3_csv",
    "gcs_parquet":  "corvin_compute.fabric.datasources.builtin.gcs_parquet",
    "azure_blob":   "corvin_compute.fabric.datasources.builtin.azure_blob",
    "bigquery":     "corvin_compute.fabric.datasources.builtin.bigquery",
    "snowflake":    "corvin_compute.fabric.datasources.builtin.snowflake",
    "redshift":     "corvin_compute.fabric.datasources.builtin.redshift",
    "delta_lake":   "corvin_compute.fabric.datasources.builtin.delta_lake",
    "http_rest":    "corvin_compute.fabric.datasources.builtin.http_rest",
    "kafka_batch":  "corvin_compute.fabric.datasources.builtin.kafka_batch",
}

# Maps adapter name → class name within the module.
_BUILTIN_CLASS_NAMES: dict[str, str] = {
    "local_file":   "LocalFileAdapter",
    "postgresql":   "PostgreSQLAdapter",
    "mysql":        "MySQLAdapter",
    "s3_parquet":   "S3ParquetAdapter",
    "s3_csv":       "S3CSVAdapter",
    "gcs_parquet":  "GCSParquetAdapter",
    "azure_blob":   "AzureBlobAdapter",
    "bigquery":     "BigQueryAdapter",
    "snowflake":    "SnowflakeAdapter",
    "redshift":     "RedshiftAdapter",
    "delta_lake":   "DeltaLakeAdapter",
    "http_rest":    "HTTPRestAdapter",
    "kafka_batch":  "KafkaBatchAdapter",
}


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class DataSourceRegistry:
    """Discovers and loads DataSource adapters and connection manifests.

    Args:
        corvin_home: Base directory ($CORVIN_HOME or ~/.corvin).
                      Defaults to env var or ~/.corvin.
        plugin_root:  Root of the corvin-compute plugin.
                      Defaults to the parent of this file's package.
    """

    def __init__(
        self,
        corvin_home: Optional[Path] = None,
        plugin_root: Optional[Path] = None,
    ) -> None:
        if corvin_home is None:
            corvin_home = Path(
                os.environ.get("CORVIN_HOME", str(Path.home() / ".corvin"))
            )
        self._home = corvin_home
        self._plugin_root = plugin_root or Path(__file__).resolve().parents[4]

    # ------------------------------------------------------------------
    # Adapter discovery
    # ------------------------------------------------------------------

    def discover_adapters(self, tenant_id: str = "_default") -> list[AdapterManifest]:
        """Return all available adapters, with tenant overriding bundle."""
        result: dict[str, AdapterManifest] = {}

        # 1. system tier
        system_dir = Path("/etc/corvin/datasource_adapters")
        if system_dir.is_dir():
            for p in sorted(system_dir.glob("*.json")):
                name = p.stem
                result[name] = AdapterManifest(name=name, module="<system>", tier="system")

        # 2. bundle tier (built-ins)
        for name, module_path in _BUILTIN_ADAPTERS.items():
            result[name] = AdapterManifest(name=name, module=module_path, tier="bundle")

        # 3. tenant tier
        tenant_dir = self._home / "tenants" / tenant_id / "datasource_adapters"
        if tenant_dir.is_dir():
            for p in sorted(tenant_dir.glob("*.py")):
                name = p.stem
                result[name] = AdapterManifest(
                    name=name, module=str(p), tier="tenant"
                )

        # 4. user tier
        user_dir = Path.home() / ".config" / "corvin" / "datasource_adapters"
        if user_dir.is_dir():
            for p in sorted(user_dir.glob("*.py")):
                name = p.stem
                if name not in result or result[name].tier in ("system", "bundle"):
                    result[name] = AdapterManifest(
                        name=name, module=str(p), tier="user"
                    )

        return list(result.values())

    # ------------------------------------------------------------------
    # Adapter loading
    # ------------------------------------------------------------------

    def load_adapter(self, name: str, tenant_id: str = "_default") -> DataSourceAdapter:
        """Instantiate and return the adapter for *name*.

        Raises:
            KeyError: if adapter not found in any tier.
            ImportError: if the adapter's module cannot be imported.
        """
        if name not in _BUILTIN_ADAPTERS:
            raise KeyError(f"No adapter registered under name '{name}'")

        module_path = _BUILTIN_ADAPTERS[name]
        class_name = _BUILTIN_CLASS_NAMES[name]
        mod = importlib.import_module(module_path)
        cls = getattr(mod, class_name)
        return cls()

    # ------------------------------------------------------------------
    # Manifest loading
    # ------------------------------------------------------------------

    def load_manifest(
        self, name: str, tenant_id: str = "_default"
    ) -> ConnectionManifest:
        """Load and validate a ConnectionManifest by name."""
        conn_dir = self._home / "tenants" / tenant_id / "datasource_connections"
        manifest_path = conn_dir / f"{name}.json"
        if not manifest_path.exists():
            raise FileNotFoundError(
                f"No manifest found for datasource '{name}' at {manifest_path}"
            )
        return validate_manifest(manifest_path, self)

    # ------------------------------------------------------------------
    # Connection listing
    # ------------------------------------------------------------------

    def list_connections(self, tenant_id: str = "_default") -> list[ConnectionSummary]:
        """Return summaries of all registered connections for the tenant."""
        conn_dir = self._home / "tenants" / tenant_id / "datasource_connections"
        if not conn_dir.is_dir():
            return []

        summaries: list[ConnectionSummary] = []
        for p in sorted(conn_dir.glob("*.json")):
            try:
                manifest = validate_manifest(p, self)
                summaries.append(ConnectionSummary(
                    name=manifest.name,
                    adapter=manifest.adapter,
                    region=manifest.source.region,
                    tags=manifest.tags,
                ))
            except Exception:
                # Skip malformed manifests; don't crash the list call.
                pass
        return summaries


    # ------------------------------------------------------------------
    # DSI v1 — register / unregister / test (ADR-0106 M2)
    # ------------------------------------------------------------------

    def register(
        self,
        manifest_dict: dict,
        tenant_id: str = "_default",
        *,
        audit_writer: Optional[AuditWriter] = None,
        _l34_guard: Any = None,
        _l35_gate: Any = None,
    ) -> DSIv1ConnectionManifest:
        """Register a new DSI v1 data source connection.

        Steps (in order — audit-first invariant):
        1. Validate manifest_dict as DSI v1.
        2. Verify adapter exists and has required DSI v1 class attrs.
        3. L34 DataFlowGuard check (data_classification × adapter.locality).
        4. L35 EgressGate check (adapter's network_egress vs tenant policy).
        5. Write audit event ``datasource.registered`` BEFORE writing file.
        6. Write manifest JSON to datasource_connections/<name>.json (mode 0600).

        Raises:
            DSIv1PolicyError: manifest validation fails
            KeyError: adapter not found
            PermissionError: L34 or L35 gate rejects the registration
        """
        manifest = validate_dsiv1_manifest(manifest_dict)

        # Load adapter to get compliance metadata
        try:
            adapter_cls = self._load_adapter_class(manifest.adapter, tenant_id)
        except (KeyError, ImportError) as exc:
            raise KeyError(f"Adapter '{manifest.adapter}' not found: {exc}") from exc

        adapter_locality = getattr(adapter_cls, "locality", "any")
        adapter_egress = getattr(adapter_cls, "network_egress", "any")

        # L34 gate (optional — injected for testing; real gate from data_classification.py)
        if _l34_guard is not None:
            decision = _l34_guard.validate(
                classification=manifest.data_classification,
                engine_id=f"datasource:{manifest.adapter}",
                locality=adapter_locality,
                persona="datasource_registry",
                channel="console",
                chat_key="register",
            )
            if not decision.allowed:
                raise PermissionError(
                    f"L34 DataFlowGuard blocked datasource '{manifest.name}': "
                    f"{decision.reason}"
                )

        # L35 gate (optional — injected for testing; real gate from egress_gate.py)
        # Check all config keys that could carry a network-reachable address so
        # adapters using base_url / endpoint / bootstrap_servers / dsn etc. are
        # not silently bypassed just because they don't use 'host' or 'bucket'.
        if _l35_gate is not None and adapter_egress != "none":
            _HOST_KEYS = (
                "host", "bucket", "base_url", "url", "endpoint",
                "server", "bootstrap_servers", "dsn",
            )
            host = next(
                (manifest.config.get(k) for k in _HOST_KEYS if manifest.config.get(k)),
                None,
            )
            if host:
                decision = _l35_gate.validate(
                    host=host,
                    engine_id=f"datasource:{manifest.adapter}",
                    persona="datasource_registry",
                    channel="console",
                    chat_key="register",
                )
                if not decision.allowed:
                    raise PermissionError(
                        f"L35 EgressGate blocked datasource '{manifest.name}': "
                        f"{decision.reason}"
                    )

        # Audit-first invariant — no silent skip: if the audit chain is
        # unavailable the operation must be blocked, not silently completed.
        if audit_writer is None:
            raise RuntimeError(
                "audit_writer is required for register() — "
                "audit-first invariant cannot be satisfied without an active audit chain. "
                "Ensure the forge audit path is reachable before registering data sources."
            )
        audit_writer(
            "datasource.registered",
            "INFO",
            {
                "name": manifest.name,
                "adapter": manifest.adapter,
                "data_classification": manifest.data_classification,
                "data_residency": manifest.data_residency,
                "pii_scan": manifest.pii_scan,
                "tenant_id": tenant_id,
            },
        )

        # Write manifest file
        conn_dir = self._home / "tenants" / tenant_id / "datasource_connections"
        conn_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = conn_dir / f"{manifest.name}.json"
        tmp_path = manifest_path.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps(manifest_dict, indent=2),
            encoding="utf-8",
        )
        tmp_path.chmod(0o600)
        tmp_path.rename(manifest_path)

        return manifest

    def unregister(
        self,
        name: str,
        tenant_id: str = "_default",
        *,
        audit_writer: Optional[AuditWriter] = None,
    ) -> None:
        """Remove a DSI v1 connection manifest (audit-first).

        Raises:
            FileNotFoundError: if no manifest exists for name
            DSIv1PolicyError: if the manifest is not DSI v1
        """
        conn_dir = self._home / "tenants" / tenant_id / "datasource_connections"
        manifest_path = conn_dir / f"{name}.json"
        if not manifest_path.exists():
            raise FileNotFoundError(
                f"No datasource connection '{name}' found for tenant '{tenant_id}'"
            )

        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not is_dsiv1_manifest(raw):
            raise DSIv1PolicyError(
                f"Connection '{name}' is not a DSI v1 manifest — cannot unregister via DSI API."
            )

        # Audit-first invariant — fail-closed if no audit chain available
        if audit_writer is None:
            raise RuntimeError(
                "audit_writer is required for unregister() — "
                "audit-first invariant cannot be satisfied without an active audit chain."
            )
        audit_writer(
            "datasource.unregistered",
            "INFO",
            {
                "name": name,
                "adapter": raw.get("adapter", ""),
                "tenant_id": tenant_id,
            },
        )

        manifest_path.unlink()

    def test_connection(
        self,
        name: str,
        tenant_id: str = "_default",
        *,
        timeout_s: float = 5.0,
        audit_writer: Optional[AuditWriter] = None,
    ) -> PingResult:
        """Test connectivity for a DSI v1 connection (ping).

        Loads the manifest, instantiates the adapter, calls ping().
        Emits ``datasource.connection_tested`` audit event.
        """
        conn_dir = self._home / "tenants" / tenant_id / "datasource_connections"
        manifest_path = conn_dir / f"{name}.json"
        if not manifest_path.exists():
            raise FileNotFoundError(
                f"No datasource connection '{name}' found for tenant '{tenant_id}'"
            )

        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not is_dsiv1_manifest(raw):
            # Legacy manifest — emit warning ping
            if audit_writer:
                audit_writer(
                    "datasource.connection_tested",
                    "INFO",
                    {"name": name, "ok": False, "detail": "legacy-manifest-no-ping", "tenant_id": tenant_id},
                )
            return PingResult(ok=False, latency_ms=0.0, detail="legacy manifest — ping not supported")

        manifest = validate_dsiv1_manifest(raw)
        try:
            adapter_cls = self._load_adapter_class(manifest.adapter, tenant_id)
            adapter = adapter_cls()
            # Pass the NON-SECRET connection config (host/port/path) so adapters
            # can run a credential-free reachability probe. Secrets are NEVER
            # read here — ping() runs outside bwrap and must not touch the vault.
            ping_config = SourceConfig(
                adapter=manifest.adapter,
                region=str(manifest.config.get("region", manifest.data_residency)),
                raw=dict(manifest.config),
            )
            t0 = time.monotonic()
            result = adapter.ping(timeout_s=timeout_s, config=ping_config)
            latency = (time.monotonic() - t0) * 1000
            result = PingResult(ok=result.ok, latency_ms=latency, detail=result.detail)
        except Exception:
            # Never surface raw exception text — driver messages can echo back
            # host/credential fragments. Report a coarse, secret-free category.
            result = PingResult(
                ok=False, latency_ms=0.0, detail="connectivity test failed"
            )

        if audit_writer:
            severity = "INFO" if result.ok else "WARNING"
            audit_writer(
                "datasource.connection_tested",
                severity,
                {
                    "name": name,
                    "adapter": manifest.adapter,
                    "ok": result.ok,
                    "latency_ms": round(result.latency_ms, 1),
                    "tenant_id": tenant_id,
                },
            )

        return result

    def describe_adapter(
        self, adapter_name: str, tenant_id: str = "_default"
    ) -> Optional[dict]:
        """Return DSI v1 class-level metadata for the named adapter.

        Returns None if the adapter is not found or not DSI v1 compliant.
        """
        try:
            cls = self._load_adapter_class(adapter_name, tenant_id)
        except (KeyError, ImportError):
            return None

        if not hasattr(cls, "DSI_VERSION"):
            return None

        return {
            "adapter_name": getattr(cls, "adapter_name", adapter_name),
            "display_name": getattr(cls, "display_name", adapter_name),
            "description": getattr(cls, "description", ""),
            "supported_formats": sorted(getattr(cls, "supported_formats", [])),
            "locality": getattr(cls, "locality", "any"),
            "network_egress": getattr(cls, "network_egress", "any"),
            "config_schema": getattr(cls, "config_schema", {}),
            "dsi_version": cls.DSI_VERSION,
        }

    def list_connections_v1(
        self, tenant_id: str = "_default"
    ) -> list[dict]:
        """Return raw dicts for all DSI v1 manifests in the tenant."""
        conn_dir = self._home / "tenants" / tenant_id / "datasource_connections"
        if not conn_dir.is_dir():
            return []

        results: list[dict] = []
        for p in sorted(conn_dir.glob("*.json")):
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
                if is_dsiv1_manifest(raw):
                    results.append(raw)
            except Exception:
                pass
        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_adapter_class(self, name: str, tenant_id: str = "_default") -> type:
        """Load and return the adapter *class* (not an instance)."""
        if name in _BUILTIN_ADAPTERS:
            module_path = _BUILTIN_ADAPTERS[name]
            class_name = _BUILTIN_CLASS_NAMES[name]
            mod = importlib.import_module(module_path)
            return getattr(mod, class_name)

        # Tenant tier
        tenant_dir = self._home / "tenants" / tenant_id / "datasource_adapters"
        tenant_py = tenant_dir / f"{name}.py"
        if tenant_py.is_file():
            import importlib.util as _impu
            spec = _impu.spec_from_file_location(name, tenant_py)
            mod = _impu.module_from_spec(spec)  # type: ignore[arg-type]
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
            cls_name = "".join(w.capitalize() for w in name.split("_")) + "Adapter"
            return getattr(mod, cls_name)

        raise KeyError(f"No adapter registered under name '{name}'")


__all__ = [
    "AdapterManifest",
    "ConnectionSummary",
    "DataSourceRegistry",
    "AuditWriter",
]
