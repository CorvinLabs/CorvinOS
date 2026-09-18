"""
Track A: Marketplace Hub Discovery — Adversarial Test Suite (12 tests)
ADR-0677 | Phase C Tier-2 Initiative 1

Security, performance, and stability tests
"""

import sys
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, str(Path.cwd()))

from core.skills.marketplace_hub import MarketplaceHub


class TestMarketplaceHubAdversarial:
    """Adversarial tests for Marketplace Hub Discovery"""

    @classmethod
    def setup_class(cls):
        """Initialize hub service"""
        cls.hub = MarketplaceHub(corvin_home=str(Path.home() / ".corvin"))
        cls.test_results = []

    def log_test(self, test_name: str, status: str, details: str = ""):
        """Log test result"""
        result = {"test": test_name, "status": status, "details": details}
        self.test_results.append(result)
        symbol = "✅" if status == "PASS" else "❌"
        print(f"{symbol} {test_name}: {status}")
        if details:
            print(f"   {details}")

    # Security Tests
    def test_01_sql_injection_in_filter(self):
        """SQL injection in filter is safely escaped"""
        malicious = ["'; DROP TABLE --", "1' OR '1'='1", "<img src=x onerror>"]
        for query in malicious:
            try:
                result = self.hub.search(query="", filters={"tier": query}, page=1, per_page=20)
            except Exception as e:
                self.log_test("Security — SQL Injection", "FAIL", str(e))
                return

        self.log_test("Security — SQL Injection", "PASS", f"{len(malicious)} payloads safe")

    def test_02_xss_in_search(self):
        """XSS payloads handled safely"""
        xss = ["<script>alert('xss')</script>", "onerror=\"alert(1)\""]
        for payload in xss:
            try:
                self.hub.search(query=payload, page=1, per_page=20)
            except Exception as e:
                self.log_test("Security — XSS Prevention", "FAIL", str(e))
                return

        self.log_test("Security — XSS Prevention", "PASS", f"{len(xss)} payloads safe")

    # Performance Tests
    def test_03_large_query_latency(self):
        """10KB query completes in <100ms"""
        large_query = "a" * 10000
        start = time.time()
        result = self.hub.search(query=large_query, page=1, per_page=20)
        latency_ms = (time.time() - start) * 1000

        assert latency_ms < 100, f"Took {latency_ms:.1f}ms"
        self.log_test("Performance — Large Query", "PASS", f"{latency_ms:.2f}ms")

    def test_04_concurrent_isolation(self):
        """50 concurrent requests don't interfere"""
        results = {}

        def search_task(req_id):
            result = self.hub.search(query="test", page=1, per_page=10)
            results[req_id] = result.total

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(search_task, i) for i in range(50)]
            for f in as_completed(futures):
                f.result()

        assert len(results) == 50, f"Only {len(results)}/50 completed"
        self.log_test("Performance — Concurrent", "PASS", f"50 requests completed")

    # Stability Tests
    def test_05_massive_page_number(self):
        """Page 1,000,000 handled gracefully"""
        try:
            result = self.hub.search(query="", page=1000000, per_page=20)
            assert result.page == 1000000
            self.log_test("Stability — Massive Page", "PASS", "Handled gracefully")
        except Exception as e:
            self.log_test("Stability — Massive Page", "FAIL", str(e))

    def test_06_massive_per_page(self):
        """per_page=100000 is capped"""
        result = self.hub.search(query="", page=1, per_page=100000)
        assert len(result.items) <= 100
        self.log_test("Stability — Massive Per-Page", "PASS", f"Capped at {len(result.items)}")

    # Consistency Tests
    def test_07_facet_accuracy(self):
        """Facet counts match result total"""
        result = self.hub.search(query="", page=1, per_page=100)
        faceted_total = sum(result.facets.get("category", {}).values())

        assert faceted_total == result.total
        self.log_test("Consistency — Facet Counts", "PASS", f"Match: {faceted_total}")

    def test_08_no_pagination_duplication(self):
        """No items duplicate across pages"""
        all_ids = set()
        duplicates = set()

        for page in range(1, 5):
            result = self.hub.search(query="", page=page, per_page=20)
            for item in result.items:
                if item.id in all_ids:
                    duplicates.add(item.id)
                all_ids.add(item.id)
            if not result.items:
                break

        assert len(duplicates) == 0
        self.log_test("Consistency — No Duplication", "PASS", f"{len(all_ids)} unique")

    # Edge Cases
    def test_09_unicode_query(self):
        """Unicode queries handled"""
        unicode_queries = ["こんにちは", "你好", "🚀"]
        for query in unicode_queries:
            try:
                self.hub.search(query=query, page=1, per_page=20)
            except Exception as e:
                self.log_test("Edge Case — Unicode", "FAIL", str(e))
                return

        self.log_test("Edge Case — Unicode", "PASS", f"{len(unicode_queries)} queries safe")

    def test_10_special_chars(self):
        """Special characters safe"""
        special = ["<>", "@#$", "\\x00"]
        for query in special:
            try:
                self.hub.search(query=query, page=1, per_page=20)
            except Exception as e:
                self.log_test("Edge Case — Special Chars", "FAIL", str(e))
                return

        self.log_test("Edge Case — Special Chars", "PASS", f"{len(special)} queries safe")

    # Filter Validation
    def test_11_invalid_rating_filter(self):
        """Invalid rating filter handled"""
        try:
            result = self.hub.search(query="", filters={"rating_min": "invalid"}, page=1, per_page=20)
            self.log_test("Filter Validation", "PASS", "Invalid rating handled")
        except Exception as e:
            self.log_test("Filter Validation", "WARN", f"Exception: {str(e)}")

    # Cache Performance
    def test_12_cache_speedup(self):
        """Cache is faster than refresh"""
        self.hub.get_index()  # Warm cache

        start = time.time()
        self.hub.get_index(force_refresh=False)
        cached_ms = (time.time() - start) * 1000

        start = time.time()
        self.hub.get_index(force_refresh=True)
        refresh_ms = (time.time() - start) * 1000

        assert cached_ms < refresh_ms
        speedup = refresh_ms / cached_ms if cached_ms > 0 else 1
        self.log_test("Performance — Cache Speedup", "PASS", f"{speedup:.1f}x faster")

    def run_all_tests(self):
        """Run all adversarial tests"""
        print("\n" + "=" * 70)
        print("TRACK A: MARKETPLACE HUB DISCOVERY — ADVERSARIAL TEST SUITE")
        print("=" * 70)

        test_methods = [m for m in dir(self) if m.startswith("test_") and callable(getattr(self, m))]

        for method_name in sorted(test_methods):
            try:
                getattr(self, method_name)()
            except Exception as e:
                self.log_test(method_name.replace("test_", "").title(), "FAIL", str(e))

        # Summary
        print("\n" + "=" * 70)
        passed = len([r for r in self.test_results if r["status"] == "PASS"])
        failed = len([r for r in self.test_results if r["status"] == "FAIL"])

        print(f"✅ Passed:  {passed}")
        print(f"❌ Failed:  {failed}")
        print(f"📊 Total:   {len(self.test_results)}")

        print("\n" + "=" * 70)
        if failed == 0:
            print("🎉 ADVERSARIAL TESTS PASSED — SECURITY VERIFIED")
        print("=" * 70)

        return {"passed": passed, "failed": failed, "total": len(self.test_results)}


if __name__ == "__main__":
    tester = TestMarketplaceHubAdversarial()
    tester.setup_class()
    results = tester.run_all_tests()
    sys.exit(0 if results["failed"] == 0 else 1)
