"""
Quality Gate: Validate video meets production standards
Audit + fail-closed validation
"""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import subprocess
from datetime import datetime
import json


class QualityGateStatus(Enum):
    PASS = "pass"
    FAIL = "fail"
    DEGRADE = "degrade"  # Pass with warning (e.g., bitrate lower than optimal)


@dataclass
class QualityCheckResult:
    """Single quality check result"""
    check_name: str
    passed: bool
    expected: str
    actual: str | float | None
    critical: bool  # If True, check blocks entire validation
    error: str | None = None


@dataclass
class QualityAuditEvent:
    """Audit event for quality validation"""
    timestamp: str
    video_file: str
    duration_sec: float
    resolution: str
    fps: float
    codec: str
    bitrate: int
    quality_gate_status: QualityGateStatus
    checks: list  # List of QualityCheckResult
    audit_id: str  # SHA256 hash for traceability


class VideoQualityValidator:
    """Validate video meets production standards"""

    # Quality standards
    STANDARDS = {
        "resolution": {
            "expected": "1920x1080",
            "alternatives": ["1920x1080", "1280x720"],
            "critical": True
        },
        "fps": {
            "min": 24,
            "max": 60,
            "optimal": 30,
            "critical": True
        },
        "codec": {
            "expected": ["h264", "h.264", "libx264"],
            "critical": True
        },
        "bitrate": {
            "min_kbps": 250,  # YouTube minimum for 1080p
            "optimal_kbps": 3000,
            "critical": False
        },
        "duration": {
            "min_sec": 28,
            "max_sec": 62,
            "critical": True
        }
    }

    def __init__(self, audit_backend=None):
        """Initialize with optional audit backend"""
        self.audit_backend = audit_backend
        self.checks = []

    def validate(self, video_path: Path) -> tuple[QualityGateStatus, list[QualityCheckResult]]:
        """
        Validate video file against all quality standards
        Returns: (overall_status, list_of_check_results)
        """
        self.checks = []
        video_path = Path(video_path)

        if not video_path.exists():
            return (
                QualityGateStatus.FAIL,
                [QualityCheckResult(
                    check_name="file_exists",
                    passed=False,
                    expected="file exists",
                    actual=None,
                    critical=True,
                    error=f"Video file not found: {video_path}"
                )]
            )

        # Extract video metadata
        metadata = self._extract_metadata(video_path)
        if not metadata:
            return (
                QualityGateStatus.FAIL,
                [QualityCheckResult(
                    check_name="ffprobe_metadata",
                    passed=False,
                    expected="ffprobe extract metadata",
                    actual=None,
                    critical=True,
                    error="Failed to extract video metadata"
                )]
            )

        # Run all checks
        self._check_resolution(metadata)
        self._check_fps(metadata)
        self._check_codec(metadata)
        self._check_bitrate(metadata)
        self._check_duration(metadata)

        # Determine overall status
        critical_failures = [c for c in self.checks if c.critical and not c.passed]
        all_pass = all(c.passed for c in self.checks)

        if critical_failures:
            status = QualityGateStatus.FAIL
        elif all_pass:
            status = QualityGateStatus.PASS
        else:
            status = QualityGateStatus.DEGRADE

        return status, self.checks

    def _extract_metadata(self, video_path: Path) -> dict | None:
        """Extract video metadata via ffprobe"""
        try:
            result = subprocess.run(
                [
                    "ffprobe", "-v", "error",
                    "-select_streams", "v:0",
                    "-show_entries",
                    "stream=width,height,r_frame_rate,codec_name,bit_rate",
                    "-of", "json",
                    str(video_path)
                ],
                capture_output=True,
                text=True,
                timeout=10
            )

            data = json.loads(result.stdout)
            if not data.get("streams"):
                return None

            stream = data["streams"][0]

            # Also get format info (duration, bitrate)
            result2 = subprocess.run(
                [
                    "ffprobe", "-v", "error",
                    "-show_entries", "format=duration,bit_rate",
                    "-of", "json",
                    str(video_path)
                ],
                capture_output=True,
                text=True,
                timeout=10
            )

            format_data = json.loads(result2.stdout)

            return {
                "width": stream.get("width"),
                "height": stream.get("height"),
                "fps": self._parse_fps(stream.get("r_frame_rate", "30/1")),
                "codec": stream.get("codec_name", "unknown"),
                "duration_sec": float(format_data.get("format", {}).get("duration", 0)),
                "bitrate": int(stream.get("bit_rate", 0) or 0),
            }
        except Exception as e:
            print(f"[WARN] ffprobe failed: {e}")
            return None

    def _parse_fps(self, fps_str: str) -> float:
        """Parse fps from string like '30/1' → 30.0"""
        try:
            if "/" in fps_str:
                num, den = map(float, fps_str.split("/"))
                return num / den if den > 0 else 0
            return float(fps_str)
        except:
            return 0.0

    def _check_resolution(self, metadata: dict):
        """Check resolution"""
        std = self.STANDARDS["resolution"]
        actual = f"{metadata['width']}x{metadata['height']}"

        passed = actual in std["alternatives"]
        self.checks.append(QualityCheckResult(
            check_name="resolution",
            passed=passed,
            expected=std["expected"],
            actual=actual,
            critical=std["critical"]
        ))

    def _check_fps(self, metadata: dict):
        """Check frames per second"""
        std = self.STANDARDS["fps"]
        actual = metadata["fps"]

        passed = std["min"] <= actual <= std["max"]
        self.checks.append(QualityCheckResult(
            check_name="fps",
            passed=passed,
            expected=f"{std['optimal']} fps (range {std['min']}-{std['max']})",
            actual=f"{actual:.1f} fps",
            critical=std["critical"]
        ))

    def _check_codec(self, metadata: dict):
        """Check video codec"""
        std = self.STANDARDS["codec"]
        actual = metadata["codec"]

        passed = actual in std["expected"]
        self.checks.append(QualityCheckResult(
            check_name="codec",
            passed=passed,
            expected=" or ".join(std["expected"]),
            actual=actual,
            critical=std["critical"]
        ))

    def _check_bitrate(self, metadata: dict):
        """Check bitrate"""
        std = self.STANDARDS["bitrate"]
        actual_kbps = metadata["bitrate"] // 1000 if metadata["bitrate"] else 0

        passed = actual_kbps >= std["min_kbps"]
        self.checks.append(QualityCheckResult(
            check_name="bitrate",
            passed=passed,
            expected=f">= {std['min_kbps']} kbps (optimal: {std['optimal_kbps']})",
            actual=f"{actual_kbps} kbps",
            critical=std["critical"]
        ))

    def _check_duration(self, metadata: dict):
        """Check video duration"""
        std = self.STANDARDS["duration"]
        actual = metadata["duration_sec"]

        passed = std["min_sec"] <= actual <= std["max_sec"]
        self.checks.append(QualityCheckResult(
            check_name="duration",
            passed=passed,
            expected=f"{std['min_sec']}-{std['max_sec']} seconds",
            actual=f"{actual:.1f} seconds",
            critical=std["critical"]
        ))

    def emit_audit_event(self, video_path: Path, status: QualityGateStatus, checks: list) -> QualityAuditEvent:
        """Create and optionally emit audit event"""
        metadata = self._extract_metadata(video_path)

        event = QualityAuditEvent(
            timestamp=datetime.utcnow().isoformat(),
            video_file=str(video_path),
            duration_sec=metadata["duration_sec"] if metadata else 0.0,
            resolution=f"{metadata['width']}x{metadata['height']}" if metadata else "unknown",
            fps=metadata["fps"] if metadata else 0.0,
            codec=metadata["codec"] if metadata else "unknown",
            bitrate=metadata["bitrate"] if metadata else 0,
            quality_gate_status=status,
            checks=[{
                "name": c.check_name,
                "passed": c.passed,
                "expected": c.expected,
                "actual": c.actual,
                "critical": c.critical
            } for c in checks],
            audit_id=self._hash_event(str(video_path), status)
        )

        # Emit to backend if available
        if self.audit_backend:
            try:
                self.audit_backend.write_event({
                    "event_type": "quality_gate_validated",
                    "video_file": str(video_path),
                    "status": status.value,
                    "checks_passed": sum(1 for c in checks if c.passed),
                    "checks_total": len(checks),
                    "audit_id": event.audit_id
                })
            except Exception as e:
                print(f"[WARN] Failed to emit audit event: {e}")

        return event

    def _hash_event(self, video_path: str, status: QualityGateStatus) -> str:
        """Generate audit event ID (SHA256 hash)"""
        import hashlib
        data = f"{video_path}:{status.value}:{datetime.utcnow().isoformat()}"
        return hashlib.sha256(data.encode()).hexdigest()[:16]


def quality_gate_fail_closed(validator: VideoQualityValidator, video_path: Path) -> bool:
    """
    Fail-closed quality gate: reject video if it doesn't meet standards
    Returns: True if video passes, False if rejected
    Raises: RuntimeError if critical checks fail
    """
    status, checks = validator.validate(video_path)

    critical_failures = [c for c in checks if c.critical and not c.passed]

    if critical_failures:
        error_details = "\n".join([
            f"  ❌ {c.check_name}: {c.error or f'expected {c.expected}, got {c.actual}'}"
            for c in critical_failures
        ])
        raise RuntimeError(
            f"Quality gate FAILED (fail-closed) for {video_path}:\n{error_details}"
        )

    # Emit audit event
    validator.emit_audit_event(video_path, status, checks)

    if status == QualityGateStatus.FAIL:
        raise RuntimeError(f"Quality gate FAIL: {video_path}")

    return status == QualityGateStatus.PASS
