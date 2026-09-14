"""Skill Distribution — ZIP packaging for Marketplace"""

import zipfile
import json
from pathlib import Path


class SkillDistributor:
    """Package Skills as ZIP for distribution"""

    @staticmethod
    def package(skill_path: Path, output_path: Path) -> bool:
        """Create ZIP package with skill source + manifest + tests"""
        try:
            with zipfile.ZipFile(output_path, "w") as zf:
                for file in skill_path.rglob("*"):
                    if file.is_file():
                        arcname = file.relative_to(skill_path)
                        zf.write(file, arcname)
            return True
        except Exception as e:
            print(f"Packaging failed: {e}")
            return False

    @staticmethod
    def verify_checksum(zip_path: Path, expected_sha256: str) -> bool:
        """Verify ZIP integrity"""
        import hashlib
        sha256 = hashlib.sha256()
        with open(zip_path, "rb") as f:
            sha256.update(f.read())
        return sha256.hexdigest() == expected_sha256
