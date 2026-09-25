"""Phase 5: Console Skill Manager API Routes (ADR-0681)"""
from flask import Blueprint, jsonify, request
from pathlib import Path
from core.skills.skill_installer import SkillInstaller
import tempfile, hashlib

skill_manager_bp = Blueprint('skill_manager', __name__, url_prefix='/v1/console/skills')

def get_installer():
    install_root = Path.home() / ".corvin" / "skills_installed"
    return SkillInstaller(install_root)

@skill_manager_bp.route('/installed', methods=['GET'])
def list_installed_skills():
    try:
        installer = get_installer()
        registry = installer._load_registry()
        skills = []
        for skill_id, versions in registry.items():
            for v in versions:
                skills.append({
                    "skill_id": skill_id, "version": v.get("version"),
                    "boot_layer": v.get("boot_layer", "installed"),
                    "verified": v.get("verified", False)
                })
        return jsonify({"skills": skills}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@skill_manager_bp.route('/install', methods=['POST'])
def install_skill():
    try:
        if 'file' not in request.files:
            return jsonify({"error": "No file"}), 400
        zip_file = request.files['file']
        metadata = request.form.to_dict()
        skill_id, version = metadata.get('skill_id'), metadata.get('version')
        if not skill_id or not version:
            return jsonify({"error": "Missing skill_id/version"}), 400
        
        with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as tmp:
            zip_file.save(tmp.name)
            sha256 = hashlib.sha256()
            with open(tmp.name, 'rb') as f:
                sha256.update(f.read())
            installer = get_installer()
            success, msg = installer.install_skill(Path(tmp.name), sha256.hexdigest(),
                {"skill_id": skill_id, "version": version})
            return jsonify({"success": success, "message": msg}), (200 if success else 400)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@skill_manager_bp.route('/uninstall/<skill_id>/<version>', methods=['DELETE'])
def uninstall_skill(skill_id, version):
    try:
        installer = get_installer()
        success, msg = installer.uninstall_skill(skill_id, version)
        return jsonify({"success": success, "message": msg}), (200 if success else 400)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@skill_manager_bp.route('/health', methods=['GET'])
def health_check():
    try:
        installer = get_installer()
        registry = installer._load_registry()
        return jsonify({"status": "ok", "installed_count": len(registry)}), 200
    except Exception as e:
        return jsonify({"status": "error"}), 500
