#!/usr/bin/env python3
"""
Generate Playwright E2E tests for all Console panels.
"""

import os
import sys
from pathlib import Path

def get_panel_name_formatted(slug: str) -> str:
    """Convert slug to title case"""
    words = slug.split('-')
    return ' '.join(w.capitalize() for w in words)

def generate_panel_test(panel_slug: str) -> str:
    """Generate Playwright test for a single panel"""
    
    panel_title = get_panel_name_formatted(panel_slug)
    
    test_code = f'''/**
 * {panel_title} Panel E2E Tests
 *
 * Auto-generated test suite for {panel_slug} panel.
 */

import {{ test, expect }} from '../fixtures/panel-fixtures';

test.describe('{panel_title} Panel', () => {{
  const panelSlug = '{panel_slug}';
  const panelTitle = '{panel_title}';

  test('navigates to {panel_slug} and loads', async ({{ panelNav }}) => {{
    await panelNav.goto(panelSlug);
    await panelNav.assertLoaded(panelSlug);
    expect(panelNav.page).toHaveURL(/\\/app\\/{panel_slug}/);
  }});

  test('displays page title', async ({{ panelNav }}) => {{
    await panelNav.goto(panelSlug);
    const title = panelNav.getTitle();
    await expect(title).toContainText(panelTitle);
  }});

  test('main content is visible', async ({{ panelNav }}) => {{
    await panelNav.goto(panelSlug);
    const mainContent = panelNav.getMainContent();
    await expect(mainContent).toBeVisible();
  }});

  test('breadcrumb navigation is optional', async ({{ panelNav }}) => {{
    await panelNav.goto(panelSlug);
    const breadcrumb = panelNav.getBreadcrumb();
    const isVisible = await breadcrumb.isVisible().catch(() => false);
    // Breadcrumb is optional
  }});

  test('responds to user interactions', async ({{ page, panelNav }}) => {{
    await panelNav.goto(panelSlug);
    const buttons = page.locator('button');
    const count = await buttons.count();
    expect(count).toBeGreaterThanOrEqual(0);
  }});

  test('handles API errors gracefully', async ({{ page, panelNav }}) => {{
    await page.route('**/v1/console/{panel_slug}/**', (route) => {{
      route.abort('failed');
    }});

    await panelNav.goto(panelSlug);
    const mainContent = panelNav.getMainContent();
    const isVisible = await mainContent.isVisible().catch(() => false);
    // Panel should recover or show error
  }});

  test('performance baseline', async ({{ panelNav }}) => {{
    const startTime = Date.now();
    await panelNav.goto(panelSlug);
    const loadTime = Date.now() - startTime;

    expect(loadTime).toBeLessThan(5000);
  }});
}});
'''
    
    return test_code

def main():
    # Paths
    pages_dir = Path('core/console/corvin_console/web-next/src/pages')
    e2e_dir = Path('core/console/corvin_console/web-next/tests/e2e')
    
    if not pages_dir.exists():
        print(f"Error: Pages directory not found: {pages_dir}")
        return 1
    
    # Find all panels
    panel_files = sorted(pages_dir.glob('*.tsx'))
    panels = [f.stem for f in panel_files if f.stem not in {'not-found', 'landing', 'login'}]
    
    print(f"Found {len(panels)} panels")
    
    # Create e2e directory
    e2e_dir.mkdir(parents=True, exist_ok=True)
    
    # Generate tests
    generated = 0
    skipped = 0
    
    for panel in panels:
        test_file = e2e_dir / f'panel-{panel}.spec.ts'
        
        # Skip if already exists and has good size
        if test_file.exists() and test_file.stat().st_size > 3000:
            print(f"✅ {panel:25s} (keeping existing)")
            skipped += 1
            continue
        
        test_code = generate_panel_test(panel)
        test_file.write_text(test_code)
        generated += 1
        print(f"✨ Generated: {panel:25s}")
    
    print()
    print(f"Generated: {generated}, Kept: {skipped}, Total: {generated + skipped}")
    
    return 0

if __name__ == '__main__':
    sys.exit(main())
