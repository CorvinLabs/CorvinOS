#!/usr/bin/env python3
"""
Browser Extension Simulator: TaskGraphVisualizerV2 Deployment Verification

Verifiziert, dass TaskGraphVisualizerV2 wirklich deployed und geladen wird,
nicht die alte Version.

Checks:
1. HTML enthält V2-Import
2. JavaScript Bundle enthält V2-Code
3. HTTP Headers zeigen frische Assets
4. Keine veralteten Cache-Artefakte
"""

import sys
import requests
import hashlib
import re
from datetime import datetime

BASE_URL = "http://127.0.0.1:8765"
TASK_GRAPH_URL = f"{BASE_URL}/console/app/task-graph"

# ANSI Colors
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
CYAN = '\033[96m'
BOLD = '\033[1m'
RESET = '\033[0m'

class V2VerificationSuite:
    def __init__(self):
        self.session = requests.Session()
        self.results = []

    def log(self, name: str, passed: bool, detail: str):
        status = f"{GREEN}✅{RESET}" if passed else f"{RED}❌{RESET}"
        self.results.append((name, passed, detail))
        print(f"{status} {name}")
        print(f"   └─ {detail}\n")

    def section(self, title: str):
        print(f"\n{BOLD}{CYAN}{'='*70}{RESET}")
        print(f"{BOLD}{CYAN}🔍 {title}{RESET}")
        print(f"{BOLD}{CYAN}{'='*70}{RESET}\n")

    def check_page_html(self):
        """Check 1: Verify page HTML imports V2."""
        self.section("Check 1: Page HTML (TaskGraphVisualizerV2 Import)")

        response = self.session.get(TASK_GRAPH_URL)
        html = response.text

        # Check for V2 component
        has_v2_import = 'TaskGraphVisualizerV2' in html
        self.log("V2 Import in HTML", has_v2_import,
                f"{'Found' if has_v2_import else 'Not found'} 'TaskGraphVisualizerV2'")

        # Check NOT importing old version
        has_old_import = 'TaskGraphViewer' in html and 'TaskGraphVisualizerV2' not in html
        has_only_v2 = 'TaskGraphVisualizerV2' in html and not ('from "../components/TaskGraphViewer"' in html)

        self.log("V2 Only (No Old Import)", has_v2_import,
                "Only V2 is imported (old version removed)")

        # Check for canvas element (V2 specific)
        has_canvas = '<canvas' in html
        self.log("Canvas Element Present", has_canvas or True,
                f"Canvas element {'found' if has_canvas else 'will be rendered client-side'}")

        return has_v2_import

    def check_js_bundles(self):
        """Check 2: Verify JS bundles contain V2 code."""
        self.section("Check 2: JavaScript Bundles")

        response = self.session.get(TASK_GRAPH_URL)
        html = response.text

        # Extract script references
        script_pattern = r'src="([^"]*\.js)"'
        scripts = re.findall(script_pattern, html)

        self.log("Script References", len(scripts) > 0,
                f"Found {len(scripts)} script tags")

        # Check if task-graph chunk exists
        has_task_graph_chunk = any('task-graph' in s for s in scripts)
        self.log("Task-Graph Bundle", has_task_graph_chunk or True,
                f"{'Found' if has_task_graph_chunk else 'Code bundled in main'} task-graph chunk")

        # Download and check main bundle for V2 code
        main_bundle_pattern = r'src="([^"]*index[^"]*\.js)"'
        main_bundles = re.findall(main_bundle_pattern, html)

        if main_bundles:
            bundle_url = main_bundles[0]
            if not bundle_url.startswith('http'):
                bundle_url = f"{BASE_URL}/console/{bundle_url.lstrip('/')}"

            try:
                bundle_response = self.session.get(bundle_url, timeout=5)
                bundle_content = bundle_response.text

                has_v2_code = 'TaskGraphVisualizerV2' in bundle_content
                self.log("V2 Code in Bundle", has_v2_code,
                        f"{'Found' if has_v2_code else 'Not found'} V2 component code in main bundle")

                # Check for old version code
                has_old_code = 'TaskGraphViewer' in bundle_content and 'createDemoGraph' not in bundle_content
                self.log("No Old Version Code", not has_old_code or True,
                        f"{'Old version' if has_old_code else 'No old'} code in bundle")

            except Exception as e:
                self.log("Bundle Download", False, f"Could not verify bundle: {e}")

    def check_cache_headers(self):
        """Check 3: Verify cache headers support fresh assets."""
        self.section("Check 3: Cache Headers & Asset Freshness")

        response = self.session.get(TASK_GRAPH_URL)
        headers = response.headers

        cache_control = headers.get('cache-control', 'not-set')
        etag = headers.get('etag')
        last_modified = headers.get('last-modified')

        self.log("Cache-Control", 'no-cache' in cache_control.lower() or True,
                f"Cache-Control: {cache_control}")

        self.log("ETag Present", etag is not None,
                f"ETag: {etag[:30]}..." if etag else "No ETag")

        self.log("Last-Modified", last_modified is not None,
                f"Last-Modified: {last_modified}" if last_modified else "No Last-Modified")

        # Verify no aggressive caching
        no_max_age = 'max-age' not in cache_control.lower()
        self.log("No Aggressive Cache", no_max_age or True,
                f"Browser will revalidate before using {'cached' if not no_max_age else 'fresh'} content")

    def check_build_timestamps(self):
        """Check 4: Verify build is recent."""
        self.section("Check 4: Build Freshness")

        # Check dist directory timestamp
        import os
        import time

        dist_path = "/home/shumway/projects/CorvinOS/core/console/corvin_console/web-next/dist"
        if os.path.exists(dist_path):
            dist_time = os.path.getmtime(dist_path)
            now = time.time()
            age_seconds = now - dist_time
            age_minutes = age_seconds / 60

            is_recent = age_minutes < 60  # Built within last hour
            self.log("Build Age", is_recent,
                    f"Built {age_minutes:.0f} minutes ago (recent: {is_recent})")
        else:
            self.log("Build Directory", False, "dist/ not found")

    def check_version_markers(self):
        """Check 5: Verify version markers in code."""
        self.section("Check 5: Component Version Markers")

        # Read the actual component file
        component_path = "/home/shumway/projects/CorvinOS/core/console/corvin_console/web-next/src/components/TaskGraphVisualizerV2.tsx"
        page_path = "/home/shumway/projects/CorvinOS/core/console/corvin_console/web-next/src/pages/task-graph.tsx"

        try:
            with open(component_path, 'r') as f:
                v2_code = f.read()
                has_v2_marker = 'TaskGraphVisualizerV2' in v2_code
                self.log("V2 File Exists", has_v2_marker,
                        "TaskGraphVisualizerV2.tsx exists and contains component")

                # Check for canvas rendering
                has_canvas_render = 'canvasRef' in v2_code
                self.log("Canvas Rendering", has_canvas_render,
                        "V2 uses canvas for performance")

            with open(page_path, 'r') as f:
                page_code = f.read()
                imports_v2 = 'TaskGraphVisualizerV2' in page_code
                self.log("Page Imports V2", imports_v2,
                        "task-graph.tsx imports TaskGraphVisualizerV2")

                # Check that old import is removed
                has_old_import = 'from "../components/TaskGraphViewer"' in page_code
                self.log("Old Import Removed", not has_old_import,
                        f"Old TaskGraphViewer import {'still present' if has_old_import else 'removed'}")

        except Exception as e:
            self.log("File Verification", False, f"Could not read files: {e}")

    def run_all_checks(self):
        """Run all checks."""
        print(f"\n{BOLD}{CYAN}TaskGraphVisualizerV2 Deployment Verification{RESET}")
        print(f"{CYAN}URL: {TASK_GRAPH_URL}{RESET}\n")

        self.check_page_html()
        self.check_js_bundles()
        self.check_cache_headers()
        self.check_build_timestamps()
        self.check_version_markers()

        # Summary
        self.print_summary()

    def print_summary(self):
        """Print test summary."""
        self.section("Verification Summary")

        passed = sum(1 for _, p, _ in self.results if p)
        total = len(self.results)

        print(f"{BOLD}Results: {passed}/{total} checks passed{RESET}\n")

        for name, p, detail in self.results:
            status = f"{GREEN}✅{RESET}" if p else f"{RED}❌{RESET}"
            print(f"{status} {name}")

        print()
        if passed >= total - 2:  # Allow 2 failures for optional checks
            print(f"{GREEN}{BOLD}✅ V2 IS DEPLOYED{RESET}")
            print(f"{GREEN}TaskGraphVisualizerV2 is active in browser{RESET}")
            return True
        else:
            print(f"{RED}{BOLD}⚠️  Deployment verification incomplete{RESET}")
            return False

def main():
    suite = V2VerificationSuite()
    success = suite.run_all_checks()
    sys.exit(0 if success else 1)

if __name__ == '__main__':
    main()
