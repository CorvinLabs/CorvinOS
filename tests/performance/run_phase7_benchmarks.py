#!/usr/bin/env python3
"""Phase 7.2 Performance Baseline - Standalone Benchmark Runner

Runs critical path benchmarks without pytest dependency.
Uses Python's timeit module for accurate measurements.

Usage:
    python3 run_phase7_benchmarks.py

Output:
    Prints detailed latency measurements and SLA compliance status
"""

import sys
import time
import json
import logging
from pathlib import Path
from decimal import Decimal
from datetime import datetime, timedelta
from typing import List, Dict, Any
import tempfile

# Set up logging
logging.basicConfig(
    level=logging.WARNING,
    format='%(message)s'
)
logger = logging.getLogger(__name__)

# Add CorvinOS to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Licensing imports
from core.licensing.a2a_verifier import A2ADelegationVerifier
from core.licensing.member_credential import (
    MemberCredential,
    SignedTask,
    RSAKeyPair,
    CredentialStore,
)

# Security imports
from core.skills.manifest_v2 import SkillManifestV2, LicenseBindingMetadata
from core.skills.signature import SkillManifestSigner
from core.skills.license_binding import UserLicense
from core.skill_forge.validators.forge_gateway import ForgeSkillValidator


class BenchmarkResult:
    """Stores benchmark results."""
    
    def __init__(self, name: str, latencies: List[float], unit: str = "ms"):
        self.name = name
        self.latencies = latencies
        self.unit = unit
    
    def mean(self) -> float:
        return sum(self.latencies) / len(self.latencies) if self.latencies else 0
    
    def min(self) -> float:
        return min(self.latencies) if self.latencies else 0
    
    def max(self) -> float:
        return max(self.latencies) if self.latencies else 0
    
    def stddev(self) -> float:
        if len(self.latencies) < 2:
            return 0
        mean = self.mean()
        variance = sum((x - mean) ** 2 for x in self.latencies) / len(self.latencies)
        return variance ** 0.5
    
    def total(self) -> float:
        return sum(self.latencies)


def benchmark_licensing_gate() -> List[BenchmarkResult]:
    """Benchmark licensing verification latency."""
    print("\n" + "=" * 80)
    print("LICENSING GATE BENCHMARKS")
    print("=" * 80)
    
    results = []
    
    # Setup
    temp_dir = tempfile.mkdtemp()
    verifier = A2ADelegationVerifier(temp_dir)
    
    # Create test credentials
    rsa_pair = RSAKeyPair.generate()
    now_iso = datetime.utcnow().isoformat() + "Z"
    expires_iso = (datetime.utcnow() + timedelta(days=365)).isoformat() + "Z"
    
    credential = MemberCredential(
        credential_id="test_cred_001",
        member_id="test_member_001",
        license_tier="member",
        public_key_pem=rsa_pair.public_pem().decode(),
        issued_at=now_iso,
        expires_at=expires_iso,
    )
    
    # Store credential
    store = CredentialStore(temp_dir)
    store.save(credential)
    
    def create_signed_task(task_id: str, operation: str = "execute") -> SignedTask:
        """Create a valid signed task."""
        payload = {"action": operation, "target": "test_skill"}
        message_parts = [
            credential.member_id,
            operation,
            json.dumps(payload, sort_keys=True),
            datetime.utcnow().isoformat(),
        ]
        message = "|".join(message_parts).encode()
        signature = RSAKeyPair.sign(rsa_pair, message)
        
        return SignedTask(
            task_id=task_id,
            member_id=credential.member_id,
            credential_id=credential.credential_id,
            operation=operation,
            payload=payload,
            signature=signature.hex(),
            signed_at=datetime.utcnow().isoformat(),
            ttl_seconds=3600,
        )
    
    # Benchmark: Single verification
    print("\n[1] Single Verification")
    signed_task = create_signed_task("task_001")
    start = time.perf_counter()
    result = verifier.verify_signed_task(signed_task)
    elapsed_ms = (time.perf_counter() - start) * 1000
    print(f"    Latency: {elapsed_ms:.2f}ms")
    print(f"    Status: {'✅ PASS' if elapsed_ms < 100 else '❌ FAIL'} (<100ms target)")
    results.append(BenchmarkResult("licensing_gate_single", [elapsed_ms]))
    
    # Benchmark: 10 iterations
    print("\n[2] 10 Verifications")
    latencies = []
    for i in range(10):
        signed_task = create_signed_task(f"task_{i:03d}")
        start = time.perf_counter()
        result = verifier.verify_signed_task(signed_task)
        elapsed_ms = (time.perf_counter() - start) * 1000
        latencies.append(elapsed_ms)
    
    result = BenchmarkResult("licensing_gate_10x", latencies)
    print(f"    Mean:   {result.mean():.2f}ms")
    print(f"    Stddev: {result.stddev():.2f}ms")
    print(f"    Status: {'✅ PASS' if result.mean() < 100 else '❌ FAIL'} (<100ms target)")
    results.append(result)
    
    return results


def benchmark_datahub_aggregation() -> List[BenchmarkResult]:
    """Benchmark DataHub metrics aggregation."""
    print("\n" + "=" * 80)
    print("DATAHUB AGGREGATION BENCHMARKS")
    print("=" * 80)
    
    results = []
    from core.licensing.billing import create_default_billing_schema, ModelTier
    
    schema = create_default_billing_schema()
    
    # Benchmark: Single cost calculation
    print("\n[1] Single Cost Calculation")
    start = time.perf_counter()
    cost = schema.calculate_cost(
        model_id="claude-haiku-4-5",
        input_tokens=1000,
        output_tokens=500,
        cache_read_tokens=200,
        cache_write_tokens=50,
    )
    elapsed_ms = (time.perf_counter() - start) * 1000
    print(f"    Latency: {elapsed_ms:.4f}ms")
    print(f"    Status: {'✅ PASS' if elapsed_ms < 5 else '❌ FAIL'} (<5ms target)")
    results.append(BenchmarkResult("datahub_cost_single", [elapsed_ms]))
    
    # Benchmark: 1000 cost calculations
    print("\n[2] 1000 Cost Calculations")
    latencies = []
    for i in range(1000):
        start = time.perf_counter()
        cost = schema.calculate_cost(
            model_id="claude-haiku-4-5",
            input_tokens=1000 + i,
            output_tokens=500,
            cache_read_tokens=200,
            cache_write_tokens=50,
        )
        elapsed_ms = (time.perf_counter() - start) * 1000
        latencies.append(elapsed_ms)
    
    result = BenchmarkResult("datahub_cost_1000x", latencies)
    print(f"    Total:  {result.total():.2f}ms")
    print(f"    Mean:   {result.mean():.4f}ms")
    print(f"    Status: {'✅ PASS' if result.total() < 1000 else '❌ FAIL'} (<1s target)")
    results.append(result)
    
    # Benchmark: Quota checking
    print("\n[3] 100 Quota Checks")
    latencies = []
    for i in range(100):
        start = time.perf_counter()
        within_quota = schema.check_quota(
            tier=ModelTier.COMMUNITY,
            request_count=i % 50,
            token_count=i * 1000,
        )
        elapsed_ms = (time.perf_counter() - start) * 1000
        latencies.append(elapsed_ms)
    
    result = BenchmarkResult("datahub_quota_100x", latencies)
    print(f"    Mean:   {result.mean():.4f}ms")
    print(f"    Status: {'✅ PASS' if result.mean() < 1 else '❌ FAIL'} (<1ms mean target)")
    results.append(result)
    
    return results


def benchmark_adversarial_detection() -> List[BenchmarkResult]:
    """Benchmark threat detection and manifest validation."""
    print("\n" + "=" * 80)
    print("ADVERSARIAL THREAT DETECTION BENCHMARKS")
    print("=" * 80)
    
    results = []
    
    # Setup
    signer = SkillManifestSigner()
    public_key, private_key = signer.generate_operator_keypair()
    free_user = UserLicense(user_id="test_user", license_tier="free")
    
    # Benchmark: Single manifest validation
    print("\n[1] Single Manifest Validation")
    manifest = SkillManifestV2(
        skill_id="test.skill",
        version="1.0.0",
        boot_layer="bundled",
        license_binding=LicenseBindingMetadata(
            required_tier="free",
            binding_hash="abc123",
            operator_signature="sig",
            timestamp=datetime.utcnow().isoformat(),
        ),
    )
    signature = signer.sign_manifest(manifest, private_key)
    validator = ForgeSkillValidator(operator_public_key=public_key)
    
    start = time.perf_counter()
    try:
        validator.validate_and_load(manifest, signature, free_user)
    except Exception:
        pass
    elapsed_ms = (time.perf_counter() - start) * 1000
    print(f"    Latency: {elapsed_ms:.2f}ms")
    print(f"    Status: {'✅ PASS' if elapsed_ms < 500 else '❌ FAIL'} (<500ms target)")
    results.append(BenchmarkResult("adversarial_validation_single", [elapsed_ms]))
    
    # Benchmark: 100 validations
    print("\n[2] 100 Manifest Validations")
    latencies = []
    for i in range(100):
        manifest = SkillManifestV2(
            skill_id=f"test.skill_{i}",
            version="1.0.0",
            boot_layer="bundled",
            license_binding=LicenseBindingMetadata(
                required_tier="free",
                binding_hash=f"hash_{i}",
                operator_signature="sig",
                timestamp=datetime.utcnow().isoformat(),
            ),
        )
        signature = signer.sign_manifest(manifest, private_key)
        
        start = time.perf_counter()
        try:
            validator.validate_and_load(manifest, signature, free_user)
        except Exception:
            pass
        elapsed_ms = (time.perf_counter() - start) * 1000
        latencies.append(elapsed_ms)
    
    result = BenchmarkResult("adversarial_validation_100x", latencies)
    print(f"    Mean:   {result.mean():.2f}ms")
    print(f"    Status: {'✅ PASS' if result.mean() < 500 else '❌ FAIL'} (<500ms target)")
    results.append(result)
    
    return results


def print_summary(all_results: List[List[BenchmarkResult]]) -> Dict[str, bool]:
    """Print summary report and check SLA compliance."""
    print("\n" + "=" * 80)
    print("PHASE 7.2 PERFORMANCE BASELINE SUMMARY")
    print("=" * 80)
    
    # Flatten results
    all_benchmarks = {}
    for category_results in all_results:
        for result in category_results:
            all_benchmarks[result.name] = result
    
    # Check SLA compliance
    compliance_status = {}
    
    print("\nSLA COMPLIANCE:")
    print("-" * 80)
    
    slas = {
        "licensing_gate_single": (100, "mean"),
        "licensing_gate_10x": (100, "mean"),
        "datahub_cost_single": (5, "mean"),
        "datahub_cost_1000x": (1000, "total"),
        "datahub_quota_100x": (1, "mean"),
        "adversarial_validation_single": (500, "mean"),
        "adversarial_validation_100x": (500, "mean"),
    }
    
    for name, (sla_target, metric_type) in slas.items():
        if name in all_benchmarks:
            result = all_benchmarks[name]
            actual = result.total() if metric_type == "total" else result.mean()
            passed = actual < sla_target
            compliance_status[name] = passed
            
            status = "✅ PASS" if passed else "❌ FAIL"
            print(f"{status} {name:40s} {actual:8.2f}ms / {sla_target:8.2f}ms")
    
    print("\n" + "-" * 80)
    all_passed = all(compliance_status.values())
    if all_passed:
        print("✅ ALL SLA TARGETS MET")
    else:
        failed = sum(1 for v in compliance_status.values() if not v)
        print(f"❌ {failed} benchmarks FAILED SLA")
    print("-" * 80)
    
    return compliance_status


def main():
    """Run all benchmarks."""
    print("\n" + "=" * 80)
    print("PHASE 7.2 PERFORMANCE BASELINE MEASUREMENTS")
    print("=" * 80)
    
    try:
        licensing_results = benchmark_licensing_gate()
        datahub_results = benchmark_datahub_aggregation()
        adversarial_results = benchmark_adversarial_detection()
        
        all_results = [licensing_results, datahub_results, adversarial_results]
        compliance_status = print_summary(all_results)
        
        exit_code = 0 if all(compliance_status.values()) else 1
        print(f"\n✅ Benchmarks completed (exit code: {exit_code})")
        return exit_code
        
    except Exception as e:
        print(f"\n❌ Benchmark failed: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
