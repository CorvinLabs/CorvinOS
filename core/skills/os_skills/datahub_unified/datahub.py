"""DataHub Skill — ingest a file, scan it, describe it.

Every ingested row goes through the ADR-0661 security scanner
(``data_hub.security.scanner``) before anything is generated from it.
"""

import csv
import json
from pathlib import Path
from typing import Optional

try:
    from core.skills.os_skills.data_hub.security.scanner import SecurityScanner
except ImportError:  # imported as a bare ``datahub_unified`` package
    from data_hub.security.scanner import SecurityScanner  # type: ignore[no-redef]
from .models import CreationRequest, CreationType, DataIngestion, DataSourceType

MAX_SOURCE_BYTES = 50 * 1024 * 1024
SUPPORTED_SOURCES = (DataSourceType.JSON, DataSourceType.CSV)


class IngestionError(ValueError):
    """The source cannot be ingested; the message is safe to show the operator."""


class DataHubSkill:
    """Ingest + analyze data."""

    def __init__(self):
        self.ingested_data: Optional[list] = None
        self.schema: Optional[dict] = None
        self.sample_rows: int = 0
        self.security: dict = {"secret": 0, "pii": 0, "injection": 0}

    def ingest(self, request: DataIngestion) -> bool:
        """Read, scan and summarise the source. Raises IngestionError when it cannot."""
        if not request.validate():
            raise IngestionError("source_path and sample_rows are required")
        if request.source_type not in SUPPORTED_SOURCES:
            raise IngestionError(
                f"source type '{request.source_type.value}' is not supported on this build")

        path = Path(request.source_path)
        if not path.is_file():
            raise IngestionError("source file not found")
        if path.stat().st_size > MAX_SOURCE_BYTES:
            raise IngestionError("source file is larger than 50 MB")

        try:
            if request.source_type == DataSourceType.JSON:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                rows = data if isinstance(data, list) else [data]
            else:
                with open(path, encoding="utf-8", newline="") as f:
                    rows = list(csv.DictReader(f))
        except (json.JSONDecodeError, UnicodeDecodeError, csv.Error) as exc:
            raise IngestionError(f"source is not valid {request.source_type.value}") from exc

        self.ingested_data = rows
        self.sample_rows = min(len(rows), request.sample_rows)
        self.security = self._scan(rows)
        if request.infer_schema:
            self.schema = self._infer_schema(rows)
        return True

    def analyze(self) -> dict:
        if self.ingested_data is None:
            return {}
        return {
            "row_count": len(self.ingested_data),
            "sample_rows": self.sample_rows,
            "schema": self.schema,
            "completeness": self._compute_completeness(),
            "security": dict(self.security),
        }

    def generate_creation_request(self,
                                  name: str,
                                  creation_type: CreationType,
                                  description: str) -> Optional[CreationRequest]:
        if not self.ingested_data:
            return None
        return CreationRequest(
            data=DataIngestion(
                source_type=DataSourceType.JSON,
                source_path="<in-memory>",
                sample_rows=len(self.ingested_data),
            ),
            creation_type=creation_type,
            name=name,
            description=description,
        )

    @staticmethod
    def _scan(rows: list) -> dict:
        scanner = SecurityScanner()
        counts = {"secret": 0, "pii": 0, "injection": 0}
        for row in rows:
            _redacted, issues = scanner.scan_text(json.dumps(row, ensure_ascii=False, default=str))
            for issue in issues:
                counts[issue.type] = counts.get(issue.type, 0) + 1
        return counts

    def _infer_schema(self, data: list) -> dict:
        if not data:
            return {}
        first = data[0]
        schema = {}
        for key, val in (first.items() if isinstance(first, dict) else []):
            # bool before int: bool is an int subclass
            if isinstance(val, bool):
                schema[key] = "boolean"
            elif isinstance(val, str):
                schema[key] = "string"
            elif isinstance(val, (int, float)):
                schema[key] = "number"
            elif isinstance(val, list):
                schema[key] = "array"
            elif isinstance(val, dict):
                schema[key] = "object"
            else:
                schema[key] = "unknown"
        return schema

    def _compute_completeness(self) -> float:
        if not self.ingested_data:
            return 0.0
        total_fields = 0
        filled_fields = 0
        for row in self.ingested_data:
            if isinstance(row, dict):
                total_fields += len(row)
                filled_fields += sum(1 for v in row.values() if v not in (None, ""))
        return filled_fields / max(total_fields, 1)
