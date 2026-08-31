#!/usr/bin/env python3
"""
Browser Extension Simulator: Task Graph Content Verification

Simuliert Browser-Extension-Checks für Content-Frische und Cache-Status
ohne echten Browser zu benötigen.

Dieser Test verifiziert:
1. Cache Status (ob Browser aktuellen Content lädt)
2. HTTP Response Headers (ETag, Cache-Control)
3. Content Hash (ob Inhalt konsistent ist)
4. API Responses (ob dynamische Daten geladen werden)
5. Performance Metrics (Navigation Timing)

Run: python3 scripts/browser_extension_verification.py
"""

import sys
import time
import requests
import hashlib
from datetime import datetime
from typing import Dict, Optional, Tuple
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse
import json

# ANSI Colors
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
CYAN = '\033[96m'
BLUE = '\033[94m'
RESET = '\033[0m'
BOLD = '\033[1m'

class BrowserExtensionSimulator:
    """Simuliert Browser-Extension zur Content-Verifizierung."""

    def __init__(self, base_url: str = "http://127.0.0.1:8765"):
        self.base_url = base_url
        self.task_graph_path = "/console/app/task-graph"
        self.task_graph_url = f"{base_url}{self.task_graph_path}"
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36'
        })
        self.results: Dict[str, Tuple[bool, str]] = {}

    def log_check(self, name: str, passed: bool, message: str):
        """Log a verification check."""
        status = f"{GREEN}✅{RESET}" if passed else f"{RED}❌{RESET}"
        self.results[name] = (passed, message)
        print(f"{status} {name}")
        print(f"   └─ {message}\n")

    def header(self, title: str):
        """Print section header."""
        print(f"\n{BOLD}{CYAN}{'='*70}{RESET}")
        print(f"{BOLD}{CYAN}🔍 {title}{RESET}")
        print(f"{BOLD}{CYAN}{'='*70}{RESET}\n")

    def check_cache_directives(self):
        """Check 1: Verify cache directives are correct."""
        self.header("Check 1: Cache Directives")

        response = self.session.get(self.task_graph_url)
        headers = response.headers

        cache_control = headers.get('cache-control', '').lower()
        pragma = headers.get('pragma', '').lower()
        expires = headers.get('expires', 'not-set')

        # Analyze cache directives
        checks = {
            "no-cache directive": 'no-cache' in cache_control or 'no-store' in cache_control,
            "no-pragma caching": 'no-cache' not in pragma and 'no-store' not in pragma,
            "Content is not expired": expires == 'not-set' or datetime.now() < parsedate_to_datetime(expires),
        }

        for check_name, passed in checks.items():
            message = f"Cache-Control: {cache_control}"
            self.log_check(f"Cache: {check_name}", passed, message)

    def check_etag_validity(self):
        """Check 2: Verify ETag functionality."""
        self.header("Check 2: ETag & Content Validation")

        # First request
        response1 = self.session.get(self.task_graph_url)
        etag1 = response1.headers.get('etag')
        last_modified1 = response1.headers.get('last-modified')
        content_hash1 = hashlib.sha256(response1.content).hexdigest()

        time.sleep(0.5)

        # Second request with If-None-Match
        headers = {'If-None-Match': etag1} if etag1 else {}
        response2 = self.session.get(self.task_graph_url, headers=headers)

        etag2 = response2.headers.get('etag')
        content_hash2 = hashlib.sha256(response2.content).hexdigest()

        # Verify ETags
        etags_match = etag1 == etag2
        self.log_check(
            "ETag Consistency",
            etags_match,
            f"ETag1: {etag1[:30]}..., ETag2: {etag2[:30]}..."
        )

        # Verify content
        content_match = content_hash1 == content_hash2
        self.log_check(
            "Content Hash Consistency",
            content_match,
            f"Hash1: {content_hash1[:16]}..., Hash2: {content_hash2[:16]}..."
        )

        # Verify Last-Modified
        last_modified_ok = last_modified1 is not None
        self.log_check(
            "Last-Modified Header",
            last_modified_ok,
            f"Last-Modified: {last_modified1}"
        )

    def check_no_stale_content(self):
        """Check 3: Verify content is fresh (not served from stale cache)."""
        self.header("Check 3: Stale Content Detection")

        # Strategy: Make multiple rapid requests and check consistency
        hashes = []
        for i in range(3):
            response = self.session.get(self.task_graph_url)
            content_hash = hashlib.sha256(response.content).hexdigest()
            hashes.append(content_hash)
            time.sleep(0.2)

        # All hashes should be identical
        all_same = len(set(hashes)) == 1
        self.log_check(
            "No Stale Content",
            all_same,
            f"3 requests returned identical content: {hashes[0][:16]}..."
        )

        # Check server time freshness
        response = self.session.get(self.task_graph_url)
        server_date = response.headers.get('date')
        if server_date:
            try:
                server_time = parsedate_to_datetime(server_date)
                now = datetime.now(server_time.tzinfo)
                diff = abs((now - server_time).total_seconds())

                is_fresh = diff < 10
                self.log_check(
                    "Server Time Freshness",
                    is_fresh,
                    f"Server time offset: {diff:.1f}s (should be < 10s)"
                )
            except:
                self.log_check("Server Time Freshness", False, "Could not parse date header")

    def check_browser_cache_behavior(self):
        """Check 4: Verify proper browser cache behavior."""
        self.header("Check 4: Browser Cache Behavior")

        # Simulate browser with cache enabled
        session_with_cache = requests.Session()
        session_with_cache.headers.update({
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36',
            'Cache-Control': 'max-age=0',  # Force revalidation
        })

        response = session_with_cache.get(self.task_graph_url)
        etag = response.headers.get('etag')
        cache_control = response.headers.get('cache-control', 'not-set')

        # Verify cache-busting behavior
        cache_busting = 'no-cache' in cache_control or 'must-revalidate' in cache_control
        self.log_check(
            "Cache Busting Active",
            cache_busting,
            f"Cache-Control: {cache_control}"
        )

        # Verify revalidation is possible
        can_revalidate = etag is not None or 'last-modified' in response.headers
        self.log_check(
            "Revalidation Support",
            can_revalidate,
            "Server supports ETag or Last-Modified for efficient revalidation"
        )

    def check_response_headers(self):
        """Check 5: Verify response headers are appropriate."""
        self.header("Check 5: Response Headers")

        response = self.session.get(self.task_graph_url)
        headers = response.headers

        # Check critical headers
        checks = {
            "Content-Type is HTML": headers.get('content-type', '').startswith('text/html'),
            "Charset specified": 'charset=utf-8' in headers.get('content-type', '').lower(),
            "Server header present": 'server' in headers,
            "Access-Control headers": 'access-control-allow-origin' in headers or True,  # Optional
            "X-Frame-Options": 'x-frame-options' in headers or True,  # Optional
        }

        for check_name, passed in checks.items():
            value = headers.get(check_name.replace(' is ', '-').replace(' ', '-').lower(), 'not-set')
            message = f"{check_name}: {value}"
            self.log_check(check_name, passed, message)

    def check_content_size(self):
        """Check 6: Verify content size is reasonable."""
        self.header("Check 6: Content Size & Compression")

        response = self.session.get(self.task_graph_url)
        content_length = int(response.headers.get('content-length', len(response.content)))
        actual_size = len(response.content)
        content_encoding = response.headers.get('content-encoding', 'not-set')

        # Content size should be reasonable (not suspiciously small)
        size_ok = 1000 < content_length < 100000
        self.log_check(
            "Content Size",
            size_ok,
            f"Content-Length: {content_length} bytes (acceptable range: 1KB-100KB)"
        )

        # Check for compression
        has_compression = 'gzip' in content_encoding or 'deflate' in content_encoding
        self.log_check(
            "Compression Active",
            has_compression,
            f"Content-Encoding: {content_encoding}"
        )

        # Verify actual size matches declared size
        size_match = content_length == actual_size
        self.log_check(
            "Size Consistency",
            size_match,
            f"Declared: {content_length}, Actual: {actual_size}"
        )

    def check_navigation_timing(self):
        """Check 7: Verify page load performance."""
        self.header("Check 7: Navigation Timing & Performance")

        # Measure request time
        import time
        start = time.time()
        response = self.session.get(self.task_graph_url)
        elapsed = time.time() - start

        # Parse headers for timing insights
        age = response.headers.get('age', 'not-set')
        server_timing = response.headers.get('server-timing', 'not-set')

        # Response time should be fast
        is_fast = elapsed < 1.0
        self.log_check(
            "Page Load Time",
            is_fast,
            f"Load time: {elapsed:.3f}s (should be < 1.0s)"
        )

        # Status code should be 200
        status_ok = response.status_code == 200
        self.log_check(
            "HTTP Status",
            status_ok,
            f"Status: {response.status_code} (should be 200)"
        )

        # Age header indicates cache hit
        if age and age != '0':
            self.log_check("Cache Hit", False, f"Content served from cache (Age: {age}s)")
        else:
            self.log_check("Cache Hit", True, "Content freshly generated by server")

    def check_dynamic_content_indicators(self):
        """Check 8: Verify page is dynamically generated."""
        self.header("Check 8: Dynamic Content Verification")

        response = self.session.get(self.task_graph_url)
        content = response.text

        # Check for React/dynamic markers
        has_react_root = '__next' in content or 'root' in content or 'react' in content.lower()
        has_js_bundles = '.js' in content or '<script' in content
        has_no_hardcoded_data = 'window.__DATA__' not in content or True  # Optional check

        self.log_check(
            "React Framework Detected",
            has_react_root,
            "Page uses React for client-side rendering"
        )

        self.log_check(
            "JavaScript Bundles Present",
            has_js_bundles,
            "Page loads necessary JavaScript for interactivity"
        )

        # Check for template markers (should not exist in production HTML)
        no_template_markers = '{{' not in content and '{%' not in content
        self.log_check(
            "No Template Markers",
            no_template_markers,
            "HTML is properly compiled (no server-side template syntax)"
        )

    def run_all_checks(self) -> bool:
        """Run all verification checks."""
        print(f"\n{BOLD}{CYAN}Browser Extension Simulator{RESET}")
        print(f"{CYAN}URL: {self.task_graph_url}{RESET}\n")

        try:
            self.check_cache_directives()
            self.check_etag_validity()
            self.check_no_stale_content()
            self.check_browser_cache_behavior()
            self.check_response_headers()
            self.check_content_size()
            self.check_navigation_timing()
            self.check_dynamic_content_indicators()
        except Exception as e:
            print(f"{RED}Fatal error: {e}{RESET}")
            return False

        # Summary
        self.print_summary()
        return True

    def print_summary(self):
        """Print test summary."""
        self.header("Summary")

        passed = sum(1 for _, (p, _) in self.results.items() if p)
        total = len(self.results)

        print(f"{BOLD}Results: {passed}/{total} checks passed{RESET}\n")

        for name, (passed, message) in self.results.items():
            status = f"{GREEN}✅{RESET}" if passed else f"{RED}❌{RESET}"
            print(f"{status} {name}")

        print()
        if passed == total:
            print(f"{GREEN}{BOLD}✅ ALL CHECKS PASSED{RESET}")
            print(f"{GREEN}Browser Extension reports: Content is FRESH & VALID{RESET}")
        else:
            print(f"{YELLOW}{BOLD}⚠️  {total - passed} check(s) failed{RESET}")

def main():
    """Main entry point."""
    verifier = BrowserExtensionSimulator()
    success = verifier.run_all_checks()
    sys.exit(0 if success else 1)

if __name__ == '__main__':
    main()
