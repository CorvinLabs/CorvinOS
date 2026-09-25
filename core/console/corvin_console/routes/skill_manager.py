"""Phase 5: Console Skill Manager API Routes (ADR-0681) — FastAPI"""
from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from pathlib import Path
from core.skills.skill_installer import SkillInstaller
import tempfile, hashlib, json

router = APIRouter()

def get_installer():
    install_root = Path.home() / ".corvin" / "skills_installed"
    return SkillInstaller(install_root)

@router.get("/skills/installed", tags=["skill-manager"])
async def list_installed_skills():
    """List all installed skills."""
    try:
        installer = get_installer()
        registry = installer._load_registry()
        skills = []
        for skill_id, versions in registry.items():
            for v in versions:
                skills.append({
                    "skill_id": skill_id,
                    "version": v.get("version"),
                    "boot_layer": v.get("boot_layer", "installed"),
                    "verified": v.get("verified", False)
                })
        return {"skills": skills, "total": len(skills)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/skills/install", tags=["skill-manager"])
async def install_skill(
    file: UploadFile = File(...),
    skill_id: str = Form(...),
    version: str = Form(...)
):
    """Install skill from uploaded ZIP."""
    try:
        if not file.filename.endswith('.zip'):
            raise HTTPException(status_code=400, detail="File must be ZIP")
        
        with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as tmp:
            content = await file.read()
            tmp.write(content)
            tmp.flush()
            
            sha256 = hashlib.sha256()
            sha256.update(content)
            
            installer = get_installer()
            success, msg = installer.install_skill(
                Path(tmp.name),
                sha256.hexdigest(),
                {"skill_id": skill_id, "version": version}
            )
            
            if not success:
                raise HTTPException(status_code=400, detail=msg)
            return {"success": True, "message": msg}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/skills/uninstall/{skill_id}/{version}", tags=["skill-manager"])
async def uninstall_skill(skill_id: str, version: str):
    """Uninstall skill version."""
    try:
        installer = get_installer()
        success, msg = installer.uninstall_skill(skill_id, version)
        if not success:
            raise HTTPException(status_code=400, detail=msg)
        return {"success": True, "message": msg}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/skills/health", tags=["skill-manager"])
async def health_check():
    """Health check for skill manager."""
    try:
        installer = get_installer()
        registry = installer._load_registry()
        return {
            "status": "ok",
            "installed_count": len(registry),
            "registry_path": str(installer.registry_path)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail={"status": "error", "error": str(e)})
