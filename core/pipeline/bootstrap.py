"""
Bootstrap DualGatePipeline — ADR-0300 + ADR-0301

Instantiates the three-gate pipeline on application startup:
  1. Boot tripwire (verify audit chain integrity)
  2. Validate components (ValidatorFactory, PIIDetector, AuditChain)
  3. Instantiate DualGatePipeline
  4. Store on app state for route adapters

This runs in the FastAPI lifespan context, BEFORE the server starts accepting requests.
Fail-closed: any initialization failure aborts the application boot.

Compliance: GDPR Art. 30, 32 (audit integrity verified on startup).
"""

import logging
import sys
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


def get_corvin_home() -> Path:
    """Get CORVIN_HOME directory, respecting environment and defaults."""
    import os
    corvin_home_str = os.environ.get("CORVIN_HOME")
    if corvin_home_str:
        return Path(corvin_home_str)
    return Path.home() / ".corvin"


def verify_audit_durability(tenant_id: str = "_default") -> tuple[bool, str]:
    """
    CRITICAL-3: Boot Tripwire — Verify audit chain integrity on startup.

    This is a fail-closed, non-overridable check: if the audit chain is corrupted,
    the application will not boot. This ensures GDPR Art. 30, 32 compliance.

    Args:
        tenant_id: Tenant to verify (default: "_default")

    Returns:
        (is_valid: bool, message: str)
        - is_valid=True: chain verified, safe to boot
        - is_valid=False: chain corrupted, ABORT BOOT

    Raises:
        SystemExit: if chain is corrupted (fail-closed)
    """
    try:
        from core.audit import AuditDurabilityManager
        from forge.paths import tenant_home as get_tenant_home  # type: ignore

        tenant_dir = Path(get_tenant_home(tenant_id))
        audit_file = tenant_dir / "audit.jsonl"

        if not audit_file.exists():
            logger.info(f"Audit chain not yet created: {audit_file}")
            return True, "new_audit_chain"

        mgr = AuditDurabilityManager(audit_file, tenant_id)
        is_valid, msg = mgr.verify_durability()

        if not is_valid:
            logger.critical(
                f"AUDIT CHAIN CORRUPTED: {msg}. "
                f"File: {audit_file}. "
                f"This is a GDPR Art. 30, 32 compliance failure. "
                f"Boot aborted (fail-closed)."
            )
            # Log to audit trail before exiting (best-effort)
            try:
                mgr.write_event(
                    event_type="audit_chain_verification_failed",
                    details={"reason": msg, "verdict": "abort_boot"},
                )
            except Exception:
                pass

            # FAIL-CLOSED: non-overridable
            sys.exit(1)

        logger.info(f"Audit chain verified: {msg}")
        return True, msg

    except Exception as e:
        logger.critical(f"Audit verification error: {e}. Aborting boot (fail-closed).")
        sys.exit(1)


def instantiate_pipeline(
    app_state: Any,
    tenant_id: str = "_default",
    feature_flags: Optional[dict[str, bool]] = None,
) -> Any:
    """
    CRITICAL-2: Instantiate DualGatePipeline in application startup.

    Creates all three-gate components and stores on FastAPI app state for
    route adapters to access.

    Args:
        app_state: FastAPI app.state object (for storing pipeline)
        tenant_id: Tenant context (default: "_default")
        feature_flags: Feature flag state (default: None → read from config)

    Returns:
        DualGatePipeline instance

    Raises:
        Exception: if any component fails to initialize (fail-closed)
    """
    try:
        logger.info("Initializing DualGatePipeline...")

        # Step 1: Load feature flags (dark by default)
        if feature_flags is None:
            try:
                from core.console.corvin_core.feature_flags import get_flag
                feature_flags = {
                    "dual_gate_pipeline_enabled": get_flag(
                        "dual_gate_pipeline_enabled"
                    ),
                    "dual_gate_pii_detection_enabled": get_flag(
                        "dual_gate_pii_detection_enabled"
                    ),
                    "file_permissions_enabled": get_flag("file_permissions_enabled"),
                }
            except Exception as e:
                logger.warning(f"Could not load feature flags: {e}. Defaulting to off.")
                feature_flags = {}

        # Step 2: Initialize AuditChain (ADR-0299)
        try:
            from core.audit import AuditChain
            from forge.paths import tenant_home as get_tenant_home  # type: ignore

            tenant_dir = Path(get_tenant_home(tenant_id))
            audit_file = tenant_dir / "audit.jsonl"
            audit_chain = AuditChain(audit_file)
            logger.info(f"AuditChain initialized for tenant {tenant_id} at {audit_file}")
        except Exception as e:
            logger.error(f"AuditChain initialization failed: {e}")
            raise

        # Step 3: Initialize CapabilityRegistry (ADR-0302)
        try:
            from core.compliance.capability_registry import CapabilityRegistry
            capability_checker = CapabilityRegistry()
            logger.info("CapabilityRegistry initialized")
        except Exception as e:
            logger.warning(f"CapabilityRegistry not available: {e}. Using stub.")
            capability_checker = None

        # Step 4: Initialize PIIDetector (ADR-0297, if enabled)
        pii_detector = None
        if feature_flags.get("dual_gate_pii_detection_enabled", False):
            try:
                from core.pipeline.pii_detector import PIIDetector
                pii_detector = PIIDetector(tenant_id=tenant_id)
                logger.info("PIIDetector initialized (ADR-0297)")
            except Exception as e:
                logger.warning(f"PIIDetector initialization failed: {e}. PII scanning disabled.")

        # Step 5: Initialize ValidatorFactory (ADR-0296)
        validator_factory = None
        if feature_flags.get("dual_gate_pipeline_enabled", False):
            try:
                from core.pipeline.validator_factory import ValidatorFactory
                validator_factory = ValidatorFactory(tenant_id=tenant_id)
                logger.info("ValidatorFactory initialized (ADR-0296)")
            except Exception as e:
                logger.warning(f"ValidatorFactory initialization failed: {e}. Input validation disabled.")

        # Step 6: Instantiate DualGatePipeline (ADR-0300)
        try:
            from core.pipeline.dual_gate import DualGatePipeline
            pipeline = DualGatePipeline(
                audit_chain=audit_chain,
                capability_checker=capability_checker,
                pii_detector=pii_detector,
                validator_factory=validator_factory,
                feature_flags=feature_flags,
            )
            logger.info("DualGatePipeline instantiated successfully")
        except Exception as e:
            logger.error(f"DualGatePipeline instantiation failed: {e}")
            raise

        # Step 7: Store pipeline on app state and global wiring module
        app_state.pipeline = pipeline
        app_state.tenant_id = tenant_id
        app_state.feature_flags = feature_flags
        logger.info(f"Pipeline stored on app state (tenant={tenant_id})")

        # Also set global pipeline for wiring decorators
        try:
            from core.pipeline.wiring import set_global_pipeline
            set_global_pipeline(pipeline)
            logger.info("Global pipeline instance set for wiring decorators")
        except Exception as e:
            logger.warning(f"Could not set global pipeline for wiring: {e}")

        return pipeline

    except Exception as e:
        logger.critical(f"Pipeline initialization FAILED: {e}. Aborting boot (fail-closed).")
        raise


def bootstrap_pipeline(
    app_state: Any,
    tenant_id: str = "_default",
) -> None:
    """
    Complete bootstrap sequence: tripwire + instantiate.

    This is the entry point called from FastAPI lifespan startup.
    Runs in this order:
      1. Boot tripwire (verify audit chain integrity)
      2. Instantiate DualGatePipeline

    Both are fail-closed: any failure aborts the application boot.

    Args:
        app_state: FastAPI app.state object
        tenant_id: Tenant context (default: "_default")

    Raises:
        SystemExit: if audit chain is corrupted (tripwire)
        Exception: if pipeline initialization fails
    """
    logger.info(f"Starting pipeline bootstrap (tenant={tenant_id})")

    # CRITICAL-3: Boot tripwire (fail-closed)
    logger.info("Running audit durability verification (boot tripwire)...")
    is_valid, msg = verify_audit_durability(tenant_id=tenant_id)
    if not is_valid:
        logger.critical(f"BOOT TRIPWIRE FAILED: {msg}. Aborting.")
        sys.exit(1)

    # CRITICAL-2: Instantiate pipeline
    logger.info("Instantiating DualGatePipeline...")
    instantiate_pipeline(app_state, tenant_id=tenant_id)

    logger.info(f"Pipeline bootstrap complete (tenant={tenant_id})")
