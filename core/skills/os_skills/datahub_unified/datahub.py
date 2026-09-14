"""Phase 1: DataHub Skill — Unified data ingestion."""

from .models import DataSourceType, DataIngestion, CreationRequest, CreationResult, CreationType
import json
from pathlib import Path
from typing import Optional, List


class DataHubSkill:
    """DataHub Skill — Ingest + analyze data (Phase 1)."""

    def __init__(self):
        self.ingested_data: Optional[dict] = None
        self.schema: Optional[dict] = None
        self.sample_rows: int = 0

    def ingest(self, request: DataIngestion) -> bool:
        """Ingest data from source. Phase 1: Stage 1."""

        if not request.validate():
            return False

        path = Path(request.source_path)

        try:
            if request.source_type == DataSourceType.JSON:
                with open(path) as f:
                    data = json.load(f)
                self.ingested_data = data if isinstance(data, list) else [data]

            elif request.source_type == DataSourceType.CSV:
                # Stub: CSV reader would go here
                self.ingested_data = self._parse_csv_stub(path, request.sample_rows)

            else:
                # Other types: stub
                self.ingested_data = []

            self.sample_rows = len(self.ingested_data[:request.sample_rows])

            if request.infer_schema:
                self.schema = self._infer_schema(self.ingested_data)

            return True

        except Exception as e:
            self.ingested_data = None
            return False

    def analyze(self) -> dict:
        """Analyze ingested data (Phase 1: Stage 2)."""

        if not self.ingested_data:
            return {}

        return {
            "row_count": len(self.ingested_data),
            "sample_rows": self.sample_rows,
            "schema": self.schema,
            "completeness": self._compute_completeness(),
        }

    def generate_creation_request(self,
                                  name: str,
                                  creation_type: CreationType,
                                  description: str) -> Optional[CreationRequest]:
        """Prepare data for creator (Phase 1: Stage 3)."""

        if not self.ingested_data:
            return None

        return CreationRequest(
            data=DataIngestion(
                source_type=DataSourceType.JSON,
                source_path="<in-memory>",
                sample_rows=len(self.ingested_data)
            ),
            creation_type=creation_type,
            name=name,
            description=description,
        )

    def _parse_csv_stub(self, path: Path, limit: int) -> list:
        """Stub CSV parser."""
        return []

    def _infer_schema(self, data: list) -> dict:
        """Infer JSON schema from data."""
        if not data:
            return {}

        first = data[0]
        schema = {}

        for key, val in (first.items() if isinstance(first, dict) else []):
            if isinstance(val, str):
                schema[key] = "string"
            elif isinstance(val, (int, float)):
                schema[key] = "number"
            elif isinstance(val, bool):
                schema[key] = "boolean"
            elif isinstance(val, list):
                schema[key] = "array"
            elif isinstance(val, dict):
                schema[key] = "object"
            else:
                schema[key] = "unknown"

        return schema

    def _compute_completeness(self) -> float:
        """Compute data completeness (0-1)."""
        if not self.ingested_data:
            return 0.0

        total_fields = 0
        filled_fields = 0

        for row in self.ingested_data:
            if isinstance(row, dict):
                total_fields += len(row)
                filled_fields += sum(1 for v in row.values() if v is not None)

        return filled_fields / max(total_fields, 1)
