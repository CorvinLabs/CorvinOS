"""Skill Forge v2.0 API — Generator endpoints."""

from flask import Blueprint, request, jsonify
from pathlib import Path
import sys

# Import generator
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "skill_forge"))
from generators.manifest import SkillType, SkillScope
from generators.skeleton import SkeletonGenerator
from generators.validator import ManifestValidator

skill_forge_bp = Blueprint("skill_forge", __name__, url_prefix="/v1/skill-forge")


@skill_forge_bp.route("/generate", methods=["POST"])
def generate_skill_skeleton():
    """Generate skill skeleton."""
    try:
        data = request.get_json()

        name = data.get("name")
        title = data.get("title", name.replace("_", " ").title())
        description = data.get("description", "")
        skill_type = data.get("skill_type", "learned-experience")
        scope = data.get("scope", "task")

        # Validate inputs
        if not name:
            return jsonify({"error": "name required"}), 400

        try:
            skill_type_enum = SkillType(skill_type)
            scope_enum = SkillScope(scope)
        except ValueError as e:
            return jsonify({"error": f"Invalid enum: {e}"}), 400

        # Generate skeleton
        gen = SkeletonGenerator(skill_type_enum)
        manifest = gen.generate(name, title, description, scope_enum)

        # Validate
        validator = ManifestValidator()
        is_valid, errors, warnings = validator.validate(manifest)

        if not is_valid:
            return jsonify({
                "error": "Validation failed",
                "errors": errors,
                "warnings": warnings
            }), 422

        return jsonify({
            "success": True,
            "manifest": manifest.to_dict(),
            "warnings": warnings
        }), 201

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@skill_forge_bp.route("/validate", methods=["POST"])
def validate_manifest():
    """Validate a manifest."""
    try:
        data = request.get_json()

        validator = ManifestValidator()
        # Create manifest from dict
        from generators.manifest import SkillManifest
        manifest = SkillManifest.from_dict(data)
        is_valid, errors, warnings = validator.validate(manifest)

        return jsonify({
            "valid": is_valid,
            "errors": errors,
            "warnings": warnings
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@skill_forge_bp.route("/types", methods=["GET"])
def list_skill_types():
    """List available skill types."""
    types = [t.value for t in SkillType]
    scopes = [s.value for s in SkillScope]

    return jsonify({
        "skill_types": types,
        "scopes": scopes
    }), 200
