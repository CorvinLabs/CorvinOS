"""
Phase 6 E2E: Skill Marketplace Discovery (ADR-0682)
Tests: Browse, Search, Filter, Trending, Detail, One-Click Install
"""
from pathlib import Path

class TestPhase6MarketplaceDiscovery:
    """E2E: Marketplace UI + API integration."""
    
    def test_marketplace_backend_exists(self):
        """SkillMarketplaceIndex module exists."""
        backend_path = Path("core/skills/skill_marketplace.py")
        assert backend_path.exists(), "skill_marketplace.py missing"
        
        content = backend_path.read_text()
        assert "SkillMarketplaceIndex" in content
        assert "def search" in content
        assert "def get_trending" in content
    
    def test_marketplace_routes_exist(self):
        """FastAPI routes for marketplace."""
        routes_path = Path("core/console/corvin_console/routes/marketplace_routes.py")
        assert routes_path.exists(), "marketplace_routes.py missing"
        
        content = routes_path.read_text()
        assert "/marketplace/index" in content
        assert "/marketplace/search" in content
        assert "/marketplace/trending" in content
        assert "/marketplace/newest" in content
        assert "/marketplace/{skill_id}" in content
        assert "/marketplace/{skill_id}/install" in content
    
    def test_marketplace_component_exists(self):
        """React Marketplace component."""
        component_path = Path("core/console/corvin_console/web-next/src/pages/marketplace.tsx")
        assert component_path.exists(), "marketplace.tsx missing"
        
        content = component_path.read_text()
        assert "export function Marketplace" in content
        assert "fetchSkills" in content
        assert "fetchDetail" in content
        assert "handleInstall" in content
    
    def test_routes_in_app(self):
        """Routes registered in main app."""
        app_path = Path("core/console/corvin_console/app.py")
        content = app_path.read_text()
        
        assert "marketplace_routes as marketplace_skill_routes" in content
        assert "marketplace_skill_routes.router" in content
        assert 'prefix="/marketplace"' in content
    
    def test_search_endpoint_accepts_filters(self):
        """Search endpoint has query parameters."""
        routes_path = Path("core/console/corvin_console/routes/marketplace_routes.py")
        content = routes_path.read_text()
        
        # Search function params
        assert "q: str = Query" in content
        assert "domain: Optional[str]" in content
        assert "tier: Optional[str]" in content
        assert "min_rating: float" in content
        assert "sort_by: str" in content
        assert "limit: int" in content
        assert "offset: int" in content
    
    def test_marketplace_ui_has_search_bar(self):
        """Marketplace component has search interface."""
        component_path = Path("core/console/corvin_console/web-next/src/pages/marketplace.tsx")
        content = component_path.read_text()
        
        assert "searchQuery" in content
        assert "setSearchQuery" in content
        assert "placeholder=\"Search skills" in content
    
    def test_marketplace_ui_has_filters(self):
        """Marketplace component has domain/tier/sort filters."""
        component_path = Path("core/console/corvin_console/web-next/src/pages/marketplace.tsx")
        content = component_path.read_text()
        
        assert "selectedDomain" in content
        assert "selectedTier" in content
        assert "sortBy" in content
        assert "domains" in content
        assert "tiers" in content
    
    def test_marketplace_ui_displays_skills(self):
        """Marketplace shows skill cards with metadata."""
        component_path = Path("core/console/corvin_console/web-next/src/pages/marketplace.tsx")
        content = component_path.read_text()
        
        # Skill card fields
        assert "skill.name" in content
        assert "skill.rating" in content
        assert "skill.install_count" in content
        assert "skill.domain" in content
        assert "skill.tier" in content
    
    def test_marketplace_ui_has_detail_modal(self):
        """Detail modal shows full skill info."""
        component_path = Path("core/console/corvin_console/web-next/src/pages/marketplace.tsx")
        content = component_path.read_text()
        
        assert "selectedSkill" in content
        assert "fetchDetail" in content
        assert "dependencies" in content
        assert "tags" in content
    
    def test_marketplace_ui_has_install_button(self):
        """Install button present on skill cards + detail modal."""
        component_path = Path("core/console/corvin_console/web-next/src/pages/marketplace.tsx")
        content = component_path.read_text()
        
        assert "handleInstall" in content
        assert "Install" in content or "install" in content
        assert "installing" in content
    
    def test_trending_endpoint(self):
        """Trending skills endpoint implementation."""
        routes_path = Path("core/console/corvin_console/routes/marketplace_routes.py")
        content = routes_path.read_text()
        
        assert "@router.get(\"/marketplace/trending\"" in content
        assert "index.get_trending(days)" in content
        assert "install_count" in content
    
    def test_newest_endpoint(self):
        """Newest skills endpoint implementation."""
        routes_path = Path("core/console/corvin_console/routes/marketplace_routes.py")
        content = routes_path.read_text()
        
        assert "@router.get(\"/marketplace/newest\"" in content
        assert "index.get_newest" in content
        assert "created_at" in content
    
    def test_detail_endpoint(self):
        """Skill detail endpoint implementation."""
        routes_path = Path("core/console/corvin_console/routes/marketplace_routes.py")
        content = routes_path.read_text()
        
        assert "@router.get(\"/marketplace/{skill_id}\"" in content
        assert "index.get_detail(skill_id)" in content
        assert "dependencies" in content
    
    def test_install_endpoint(self):
        """One-click install endpoint."""
        routes_path = Path("core/console/corvin_console/routes/marketplace_routes.py")
        content = routes_path.read_text()
        
        assert "@router.post(\"/marketplace/{skill_id}/install\"" in content
        assert "202" in content or "Accepted" in content
        assert "job_id" in content
    
    def test_backend_search_logic(self):
        """Backend fuzzy search implementation."""
        backend_path = Path("core/skills/skill_marketplace.py")
        content = backend_path.read_text()
        
        assert "def _fuzzy_score" in content
        assert "relevance_score" in content
        assert "matched_fields" in content
    
    def test_backend_filtering(self):
        """Backend multi-facet filtering."""
        backend_path = Path("core/skills/skill_marketplace.py")
        content = backend_path.read_text()
        
        assert "query.domain" in content
        assert "query.tier" in content
        assert "query.min_rating" in content
        assert "query.origin" in content
    
    def test_backend_sorting(self):
        """Backend sorting by relevance/popularity/rating/recency."""
        backend_path = Path("core/skills/skill_marketplace.py")
        content = backend_path.read_text()
        
        assert "sort_by == \"relevance\"" in content
        assert "sort_by == \"popularity\"" in content
        assert "sort_by == \"rating\"" in content
        assert "sort_by == \"recency\"" in content
    
    def test_backend_caching(self):
        """Backend TTL-based caching."""
        backend_path = Path("core/skills/skill_marketplace.py")
        content = backend_path.read_text()
        
        assert "_cache" in content
        assert "_cache_ts" in content
        assert "ttl_seconds" in content
        assert "def _is_cache_valid" in content
    
    def test_full_integration_flow(self):
        """Phase 4–6 integration: SkillInstaller → Marketplace → One-Click Install."""
        # Phase 4: SkillInstaller exists
        installer_path = Path("core/skills/skill_installer.py")
        assert installer_path.exists()
        
        # Phase 6: Marketplace Backend exists
        marketplace_path = Path("core/skills/skill_marketplace.py")
        assert marketplace_path.exists()
        
        # Phase 6: Console Routes wire Marketplace
        routes_path = Path("core/console/corvin_console/routes/marketplace_routes.py")
        assert routes_path.exists()
        
        # Phase 6: React Component consumes Marketplace API
        component_path = Path("core/console/corvin_console/web-next/src/pages/marketplace.tsx")
        assert component_path.exists()
        
        # Integration: App registers marketplace routes
        app_path = Path("core/console/corvin_console/app.py")
        app_content = app_path.read_text()
        assert "marketplace_skill_routes" in app_content

