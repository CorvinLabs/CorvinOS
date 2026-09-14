"""DataHub Unified API endpoints."""

from flask import Blueprint, request, jsonify
from pathlib import Path
import sys
import json

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "skills" / "os_skills"))

from datahub_unified.datahub import DataHubSkill
from datahub_unified.creator import UnifiedCreator
from datahub_unified.models import DataSourceType, CreationType, DataIngestion, CreationRequest

datahub_bp = Blueprint("datahub", __name__, url_prefix="/v1/datahub")


@datahub_bp.route("/ingest", methods=["POST"])
def ingest_data():
    """Ingest data from source (Phase 1)."""
    try:
        data = request.get_json()

        hub = DataHubSkill()
        ingestion = DataIngestion(
            source_type=DataSourceType(data.get("source_type", "json")),
            source_path=data.get("source_path"),
            sample_rows=data.get("sample_rows", 100)
        )

        if not hub.ingest(ingestion):
            return jsonify({"error": "Ingestion failed"}), 400

        analysis = hub.analyze()

        return jsonify({
            "success": True,
            "analysis": analysis,
            "sample_rows": hub.sample_rows,
            "schema": hub.schema
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@datahub_bp.route("/create", methods=["POST"])
def create_artifact():
    """Create artifact from data (Phase 2)."""
    try:
        data = request.get_json()

        creation_req = CreationRequest(
            data=DataIngestion(
                source_type=DataSourceType(data.get("source_type", "json")),
                source_path=data.get("source_path", ""),
                sample_rows=data.get("sample_rows", 100)
            ),
            creation_type=CreationType(data.get("creation_type", "skill")),
            name=data.get("name"),
            description=data.get("description", "")
        )

        creator = UnifiedCreator()
        result = creator.create(creation_req)

        return jsonify({
            "success": result.success,
            "artifact_name": result.artifact_name,
            "artifact_type": result.artifact_type.value,
            "artifact_body_length": len(result.artifact_body),
            "test_count": result.test_count,
            "validation_errors": result.validation_errors
        }), 201 if result.success else 422

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@datahub_bp.route("/sources", methods=["GET"])
def list_sources():
    """List available data sources."""
    sources = [s.value for s in DataSourceType]
    types = [t.value for t in CreationType]
    return jsonify({"sources": sources, "creation_types": types}), 200
