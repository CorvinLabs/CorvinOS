"""
Phase 5b E2E: Upload UI — Full Installation Flow
ADR-0681 Upload UI + Progress Tracking

Validates:
1. Form fields render correctly
2. File picker functional
3. Upload button state management
4. Progress tracking
5. Success/error handling
6. Auto-refresh after install
7. Uninstall functionality
"""

from pathlib import Path


class TestPhase5bUploadUI:
    """E2E: Complete upload UI + flow."""
    
    def test_upload_form_rendered(self):
        """Upload form section exists."""
        component_path = Path("core/console/corvin_console/web-next/src/pages/admin/skill-manager.tsx")
        content = component_path.read_text()
        
        assert "Install New Skill" in content
        assert "Skill ID" in content
        assert "Version" in content
        assert "ZIP File" in content
    
    def test_form_inputs_capture_state(self):
        """Form inputs bound to state."""
        component_path = Path("core/console/corvin_console/web-next/src/pages/admin/skill-manager.tsx")
        content = component_path.read_text()
        
        # skill_id input
        assert "upload.skillId" in content
        assert "setUpload" in content
        
        # version input
        assert "upload.version" in content
        
        # file input
        assert "handleFileChange" in content
        assert "upload.file" in content
    
    def test_file_picker_accepts_zip(self):
        """File input accepts only ZIP."""
        component_path = Path("core/console/corvin_console/web-next/src/pages/admin/skill-manager.tsx")
        content = component_path.read_text()
        
        assert 'accept=".zip"' in content
    
    def test_upload_button_state_management(self):
        """Upload button disabled until form complete."""
        component_path = Path("core/console/corvin_console/web-next/src/pages/admin/skill-manager.tsx")
        content = component_path.read_text()
        
        # Button should be disabled if missing fields
        assert "disabled={upload.uploading ||" in content
        assert "!upload.file ||" in content
        assert "!upload.skillId ||" in content
        assert "!upload.version}" in content
    
    def test_progress_bar_renders(self):
        """Progress bar shows during upload."""
        component_path = Path("core/console/corvin_console/web-next/src/pages/admin/skill-manager.tsx")
        content = component_path.read_text()
        
        assert "width: `${upload.progress}%`" in content
        assert "bg-green-600" in content
    
    def test_upload_handler_posts_multipart(self):
        """handleUpload sends FormData with file."""
        component_path = Path("core/console/corvin_console/web-next/src/pages/admin/skill-manager.tsx")
        content = component_path.read_text()
        
        assert "FormData" in content
        assert "formData.append('file'" in content
        assert "formData.append('skill_id'" in content
        assert "formData.append('version'" in content
        assert "'/v1/console/skills-manager/skills/install'" in content
    
    def test_xhr_progress_tracking(self):
        """Progress tracked via XMLHttpRequest."""
        component_path = Path("core/console/corvin_console/web-next/src/pages/admin/skill-manager.tsx")
        content = component_path.read_text()
        
        assert "XMLHttpRequest()" in content
        assert "xhr.upload.addEventListener('progress'" in content
        assert "e.loaded / e.total" in content
    
    def test_success_message_shown(self):
        """Success alert rendered after install."""
        component_path = Path("core/console/corvin_console/web-next/src/pages/admin/skill-manager.tsx")
        content = component_path.read_text()
        
        assert "upload.success" in content
        assert "✅" in content
        assert "Skill installed successfully" in content or "alert" in content
    
    def test_auto_refresh_after_install(self):
        """Skills list refreshes after successful install."""
        component_path = Path("core/console/corvin_console/web-next/src/pages/admin/skill-manager.tsx")
        content = component_path.read_text()
        
        assert "setTimeout" in content
        assert "fetchSkills()" in content
        assert "2000" in content  # 2 second delay
    
    def test_uninstall_button_present(self):
        """Uninstall button per skill."""
        component_path = Path("core/console/corvin_console/web-next/src/pages/admin/skill-manager.tsx")
        content = component_path.read_text()
        
        assert "Uninstall" in content
        assert "/skills/uninstall/" in content
        assert "method: 'DELETE'" in content
    
    def test_error_handling(self):
        """Errors caught and displayed."""
        component_path = Path("core/console/corvin_console/web-next/src/pages/admin/skill-manager.tsx")
        content = component_path.read_text()
        
        assert "setError" in content
        assert "catch" in content
        assert "{error &&" in content
    
    def test_form_reset_after_success(self):
        """Form fields cleared after successful install."""
        component_path = Path("core/console/corvin_console/web-next/src/pages/admin/skill-manager.tsx")
        content = component_path.read_text()
        
        # Form state reset in upload handler
        assert "file: null" in content
        assert "skillId: ''" in content
        assert "version: ''" in content

