"""
Test suite for Prometheus alert rules (Component 3)

6 tests covering:
- YAML validity
- Alert rule structure
- Alert naming conventions
- Severity levels
- Expressions and thresholds
"""

import pytest
import yaml
from pathlib import Path


# Load the alert rules YAML
ALERTS_FILE = Path(__file__).parent.parent.parent / "core/console/corvin_console/monitoring/prometheus_alerts.yaml"


class TestPrometheusAlerts:
    """Test Prometheus alert rules."""

    @pytest.fixture(scope="class")
    def alert_rules(self):
        """Load alert rules from YAML file."""
        with open(ALERTS_FILE, "r") as f:
            content = f.read()
            # Remove comments for YAML parsing
            lines = [line for line in content.split("\n") if not line.strip().startswith("#")]
            yaml_content = "\n".join(lines)
            rules = yaml.safe_load(yaml_content)
        return rules

    def test_alert_yaml_valid(self):
        """Test that alert rules YAML is valid."""
        with open(ALERTS_FILE, "r") as f:
            # Should parse without errors
            content = f.read()
            lines = [line for line in content.split("\n") if not line.strip().startswith("#")]
            yaml_content = "\n".join(lines)
            rules = yaml.safe_load(yaml_content)

            assert rules is not None
            assert isinstance(rules, dict)

    def test_alert_groups_exist(self, alert_rules):
        """Test that alert groups are defined."""
        assert "groups" in alert_rules
        groups = alert_rules["groups"]

        assert len(groups) > 0
        assert len(groups) >= 8  # At least 8 groups expected

    def test_alert_group_structure(self, alert_rules):
        """Test each alert group has correct structure."""
        groups = alert_rules["groups"]

        for group in groups:
            assert "name" in group
            assert "interval" in group
            assert "rules" in group
            assert len(group["rules"]) > 0

    def test_alert_rules_have_required_fields(self, alert_rules):
        """Test each alert rule has required fields."""
        groups = alert_rules["groups"]

        for group in groups:
            for rule in group["rules"]:
                # Check for required alert fields
                if "alert" in rule:  # It's an alert rule
                    assert "expr" in rule, f"Alert {rule.get('alert')} missing expr"
                    assert "for" in rule, f"Alert {rule.get('alert')} missing for"
                    assert "labels" in rule, f"Alert {rule.get('alert')} missing labels"
                    assert "annotations" in rule, f"Alert {rule.get('alert')} missing annotations"

                    # Check annotation structure
                    annotations = rule["annotations"]
                    assert "summary" in annotations
                    assert "description" in annotations

    def test_alert_severity_levels(self, alert_rules):
        """Test alerts have proper severity levels."""
        severity_levels = {"critical", "high", "warning", "info"}
        groups = alert_rules["groups"]

        found_severities = set()

        for group in groups:
            for rule in group["rules"]:
                if "alert" in rule:
                    labels = rule.get("labels", {})
                    severity = labels.get("severity")

                    # Severity should be one of the standard levels
                    if severity:
                        assert severity in severity_levels, \
                            f"Alert {rule.get('alert')} has invalid severity: {severity}"
                        found_severities.add(severity)

        # Should have multiple severity levels
        assert len(found_severities) >= 2

    def test_critical_alerts_exist(self, alert_rules):
        """Test that critical alerts are defined."""
        groups = alert_rules["groups"]
        critical_alerts = []

        for group in groups:
            for rule in group["rules"]:
                if "alert" in rule:
                    labels = rule.get("labels", {})
                    if labels.get("severity") == "critical":
                        critical_alerts.append(rule.get("alert"))

        # Should have multiple critical alerts
        assert len(critical_alerts) >= 4, f"Expected >=4 critical alerts, got {len(critical_alerts)}"

        # Expected critical alerts
        expected_critical = [
            "CorvinOSSLOLatencyViolation",
            "CorvinOSSLOErrorRateViolation",
            "CorvinOSAuditChainFailure",
        ]

        for expected in expected_critical:
            assert expected in critical_alerts, f"Missing critical alert: {expected}"

    def test_alert_expressions_valid_prometheus(self, alert_rules):
        """Test alert expressions look like valid Prometheus queries."""
        groups = alert_rules["groups"]

        for group in groups:
            for rule in group["rules"]:
                if "alert" in rule:
                    expr = rule.get("expr", "")

                    # Expression should not be empty
                    assert len(expr.strip()) > 0, \
                        f"Alert {rule.get('alert')} has empty expression"

                    # Should contain metric names or operators
                    valid_indicators = [
                        "corvinos_console_",  # CorvinOS metric prefix
                        ">", "<", "==", "!=",  # Comparison operators
                        "rate(", "increase(", "sum(", "avg(",  # Functions
                    ]

                    has_valid_indicator = any(
                        indicator in expr for indicator in valid_indicators
                    )
                    assert has_valid_indicator, \
                        f"Alert {rule.get('alert')} expression doesn't look like Prometheus: {expr[:100]}"

    def test_alert_thresholds_documented(self, alert_rules):
        """Test alert thresholds are in descriptions."""
        groups = alert_rules["groups"]

        threshold_count = 0

        for group in groups:
            for rule in group["rules"]:
                if "alert" in rule:
                    description = rule.get("annotations", {}).get("description", "")

                    # Critical alerts should mention thresholds
                    severity = rule.get("labels", {}).get("severity")
                    if severity == "critical":
                        # Should mention threshold or expected value
                        has_threshold = any(
                            keyword in description.lower()
                            for keyword in ["threshold", "expected", "ms)", "%)", "exceeds"]
                        )

                        if has_threshold:
                            threshold_count += 1

        # Should have multiple alerts with documented thresholds
        assert threshold_count >= 3


class TestAlertNaming:
    """Test alert naming conventions."""

    def test_alert_names_follow_convention(self, alert_rules):
        """Test alert names follow naming convention."""
        groups = alert_rules["groups"]

        for group in groups:
            for rule in group["rules"]:
                if "alert" in rule:
                    alert_name = rule["alert"]

                    # Should start with CorvinOS
                    assert alert_name.startswith("CorvinOS"), \
                        f"Alert name should start with CorvinOS: {alert_name}"

                    # Should be PascalCase
                    assert alert_name[0].isupper(), \
                        f"Alert name should be PascalCase: {alert_name}"

    def test_alert_components_tagged(self, alert_rules):
        """Test alerts have component tags."""
        groups = alert_rules["groups"]

        components = set()

        for group in groups:
            for rule in group["rules"]:
                if "alert" in rule:
                    labels = rule.get("labels", {})
                    component = labels.get("component")

                    # Should have a component tag
                    assert component is not None, \
                        f"Alert {rule.get('alert')} missing component label"

                    components.add(component)

        # Should have multiple components
        expected_components = [
            "marketplace",
            "audit_chain",
            "regex_engine",
            "model_api",
            "cost_control",
            "intelligent_router",
            "learning_loop",
        ]

        for expected in expected_components:
            assert expected in components, f"Missing component in alerts: {expected}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
