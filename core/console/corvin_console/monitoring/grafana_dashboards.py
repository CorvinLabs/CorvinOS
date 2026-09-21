"""
Grafana Dashboard Definitions for CorvinOS Console

This module exports 3 complete Grafana dashboards as JSON:
1. Model Routing Overview (routing distribution, cost, confidence, token accuracy)
2. SLO Monitoring (latency, error rate, circuit breaker, SLO status)
3. Learning Loop Health (feedback signals, config updates, convergence)

Each dashboard is JSON-importable into Grafana (Settings → Import).
"""

import json
from typing import Dict, Any, List


def create_model_routing_dashboard() -> Dict[str, Any]:
    """
    Dashboard 1: Model Routing Overview

    Charts:
    - Routing distribution by model (pie/donut)
    - Cost per model (stacked bar)
    - Confidence scores trending (line)
    - Token estimation accuracy (scatter/heatmap simulation)
    - Stat tiles: tasks routed, cost today, avg confidence, SLO status
    """
    return {
        "annotations": {
            "list": [
                {
                    "builtIn": 1,
                    "datasource": "-- Prometheus --",
                    "enable": True,
                    "hide": True,
                    "iconColor": "rgba(0, 211, 255, 1)",
                    "name": "Annotations & Alerts",
                    "type": "dashboard",
                }
            ]
        },
        "description": "Model routing distribution, cost, confidence, and token accuracy",
        "editable": True,
        "gnetId": None,
        "graphTooltip": 1,
        "id": None,
        "links": [],
        "panels": [
            # Stat: Total tasks routed (24h)
            {
                "datasource": "Prometheus",
                "fieldConfig": {
                    "defaults": {
                        "color": {"mode": "palette-classic"},
                        "mappings": [],
                        "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": None}]},
                    },
                    "overrides": [],
                },
                "gridPos": {"h": 4, "w": 6, "x": 0, "y": 0},
                "id": 1,
                "options": {
                    "graphMode": "none",
                    "orientation": "auto",
                    "reduceOptions": {
                        "calcs": ["lastNotNull"],
                        "fields": "",
                        "values": False,
                    },
                    "text": {},
                    "textMode": "auto",
                },
                "pluginVersion": "9.0.0",
                "targets": [
                    {
                        "expr": "increase(corvinos_console_routing_decisions_total[24h])",
                        "refId": "A",
                    }
                ],
                "title": "Tasks Routed (24h)",
                "type": "stat",
            },
            # Stat: Cost today vs yesterday
            {
                "datasource": "Prometheus",
                "fieldConfig": {
                    "defaults": {
                        "color": {"mode": "thresholds"},
                        "mappings": [],
                        "thresholds": {
                            "mode": "absolute",
                            "steps": [
                                {"color": "green", "value": None},
                                {"color": "yellow", "value": 80},
                                {"color": "red", "value": 100},
                            ],
                        },
                        "unit": "currencyUSD",
                    },
                    "overrides": [],
                },
                "gridPos": {"h": 4, "w": 6, "x": 6, "y": 0},
                "id": 2,
                "options": {
                    "graphMode": "none",
                    "orientation": "auto",
                    "reduceOptions": {
                        "calcs": ["lastNotNull"],
                        "fields": "",
                        "values": False,
                    },
                    "text": {},
                    "textMode": "auto",
                },
                "pluginVersion": "9.0.0",
                "targets": [
                    {
                        "expr": "increase(corvinos_console_cost_total[24h])",
                        "refId": "A",
                    }
                ],
                "title": "Cost Today",
                "type": "stat",
            },
            # Stat: Average confidence score
            {
                "datasource": "Prometheus",
                "fieldConfig": {
                    "defaults": {
                        "color": {"mode": "palette-classic"},
                        "mappings": [],
                        "max": 1.0,
                        "min": 0.0,
                        "thresholds": {
                            "mode": "absolute",
                            "steps": [
                                {"color": "red", "value": None},
                                {"color": "yellow", "value": 0.7},
                                {"color": "green", "value": 0.85},
                            ],
                        },
                        "unit": "percentunit",
                    },
                    "overrides": [],
                },
                "gridPos": {"h": 4, "w": 6, "x": 12, "y": 0},
                "id": 3,
                "options": {
                    "graphMode": "area",
                    "orientation": "auto",
                    "reduceOptions": {
                        "calcs": ["mean"],
                        "fields": "",
                        "values": False,
                    },
                    "text": {},
                    "textMode": "auto",
                },
                "pluginVersion": "9.0.0",
                "targets": [
                    {
                        "expr": "avg(corvinos_console_routing_confidence_score)",
                        "refId": "A",
                    }
                ],
                "title": "Avg Confidence Score",
                "type": "stat",
            },
            # Stat: SLO Status
            {
                "datasource": "Prometheus",
                "fieldConfig": {
                    "defaults": {
                        "color": {"mode": "thresholds"},
                        "mappings": [
                            {"options": {"0": {"color": "green", "text": "OPEN"}}, "type": "value"},
                            {"options": {"1": {"color": "red", "text": "CLOSED"}}, "type": "value"},
                        ],
                        "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": None}]},
                    },
                    "overrides": [],
                },
                "gridPos": {"h": 4, "w": 6, "x": 18, "y": 0},
                "id": 4,
                "options": {
                    "graphMode": "none",
                    "orientation": "auto",
                    "reduceOptions": {
                        "calcs": ["lastNotNull"],
                        "fields": "",
                        "values": False,
                    },
                    "text": {},
                    "textMode": "auto",
                },
                "pluginVersion": "9.0.0",
                "targets": [
                    {
                        "expr": "avg(corvinos_console_circuit_breaker_state)",
                        "refId": "A",
                    }
                ],
                "title": "Circuit Breaker",
                "type": "stat",
            },
            # Chart: Routing distribution (pie)
            {
                "datasource": "Prometheus",
                "fieldConfig": {
                    "defaults": {"color": {"mode": "palette-classic"}},
                    "overrides": [],
                },
                "gridPos": {"h": 8, "w": 12, "x": 0, "y": 4},
                "id": 5,
                "options": {
                    "reduceOptions": {
                        "calcs": ["lastNotNull"],
                        "fields": "",
                        "values": False,
                    },
                    "pieType": "donut",
                    "tooltip": {"mode": "single"},
                    "displayLabels": [],
                    "legend": {"displayMode": "table", "placement": "bottom"},
                },
                "pluginVersion": "9.0.0",
                "targets": [
                    {
                        "expr": "sum(increase(corvinos_console_routing_decisions_total[5m])) by (model_selected)",
                        "format": "table",
                        "instant": True,
                        "refId": "A",
                    }
                ],
                "title": "Routing Distribution by Model (last 5m)",
                "type": "piechart",
            },
            # Chart: Cost per model (stacked bar)
            {
                "datasource": "Prometheus",
                "fieldConfig": {
                    "defaults": {"color": {"mode": "palette-classic"}},
                    "overrides": [],
                },
                "gridPos": {"h": 8, "w": 12, "x": 12, "y": 4},
                "id": 6,
                "options": {
                    "legend": {"calcs": [], "displayMode": "list", "placement": "bottom"},
                    "tooltip": {"mode": "multi"},
                },
                "pluginVersion": "9.0.0",
                "targets": [
                    {
                        "expr": "sum(increase(corvinos_console_cost_total[24h])) by (model_selected)",
                        "refId": "A",
                    }
                ],
                "title": "Cost per Model (24h)",
                "type": "barchart",
            },
            # Chart: Confidence scores trending (line)
            {
                "datasource": "Prometheus",
                "fieldConfig": {
                    "defaults": {"color": {"mode": "palette-classic"}},
                    "overrides": [],
                },
                "gridPos": {"h": 8, "w": 12, "x": 0, "y": 12},
                "id": 7,
                "options": {
                    "legend": {"calcs": [], "displayMode": "list", "placement": "bottom"},
                    "tooltip": {"mode": "multi"},
                },
                "pluginVersion": "9.0.0",
                "targets": [
                    {
                        "expr": "avg(corvinos_console_routing_confidence_score) by (complexity_tier)",
                        "refId": "A",
                    }
                ],
                "title": "Confidence Score Trend by Tier",
                "type": "timeseries",
            },
            # Chart: Token estimation accuracy (using rate)
            {
                "datasource": "Prometheus",
                "fieldConfig": {
                    "defaults": {"color": {"mode": "palette-classic"}},
                    "overrides": [],
                },
                "gridPos": {"h": 8, "w": 12, "x": 12, "y": 12},
                "id": 8,
                "options": {
                    "legend": {"calcs": ["mean"], "displayMode": "list", "placement": "bottom"},
                    "tooltip": {"mode": "multi"},
                },
                "pluginVersion": "9.0.0",
                "targets": [
                    {
                        "expr": "histogram_quantile(0.5, rate(corvinos_console_token_estimation_error_bucket[5m]))",
                        "legendFormat": "Median Error %",
                        "refId": "A",
                    }
                ],
                "title": "Token Estimation Accuracy (Median Error %)",
                "type": "timeseries",
            },
        ],
        "refresh": "10s",
        "schemaVersion": 36,
        "style": "dark",
        "tags": ["corvinOS", "routing", "models"],
        "templating": {"list": []},
        "time": {"from": "now-24h", "to": "now"},
        "timepicker": {},
        "timezone": "browser",
        "title": "Model Routing Overview",
        "uid": "routing-overview",
        "version": 0,
    }


def create_slo_monitoring_dashboard() -> Dict[str, Any]:
    """
    Dashboard 2: SLO Monitoring

    Charts:
    - P99 latency (line, threshold 500ms red line)
    - Error rate % (line, threshold 0.1% red line)
    - Circuit breaker status (gauge)
    - Alert annotations (red shaded regions on SLO violations)
    """
    return {
        "annotations": {
            "list": [
                {
                    "builtIn": 1,
                    "datasource": "-- Prometheus --",
                    "enable": True,
                    "hide": True,
                    "iconColor": "rgba(0, 211, 255, 1)",
                    "name": "Annotations & Alerts",
                    "type": "dashboard",
                },
                {
                    "datasource": "Prometheus",
                    "enable": True,
                    "expr": "corvinos_console_circuit_breaker_state > 0",
                    "iconColor": "red",
                    "name": "Circuit Breaker Triggered",
                    "tagKeys": "",
                    "textFormat": "Circuit Breaker Open",
                },
            ]
        },
        "description": "SLO monitoring: latency, error rate, circuit breaker status",
        "editable": True,
        "gnetId": None,
        "graphTooltip": 1,
        "id": None,
        "links": [],
        "panels": [
            # Chart: P99 Latency with threshold
            {
                "datasource": "Prometheus",
                "fieldConfig": {
                    "defaults": {"color": {"mode": "palette-classic"}},
                    "overrides": [],
                },
                "gridPos": {"h": 8, "w": 12, "x": 0, "y": 0},
                "id": 1,
                "options": {
                    "legend": {"calcs": ["mean", "max"], "displayMode": "table", "placement": "right"},
                    "tooltip": {"mode": "multi"},
                },
                "pluginVersion": "9.0.0",
                "targets": [
                    {
                        "expr": "corvinos_console_slo_latency_p99_ms",
                        "legendFormat": "P99 Latency (ms)",
                        "refId": "A",
                    }
                ],
                "thresholds": {
                    "mode": "absolute",
                    "steps": [
                        {"color": "green", "value": None},
                        {"color": "yellow", "value": 400},
                        {"color": "red", "value": 500},
                    ],
                },
                "title": "P99 Latency (threshold: 500ms)",
                "type": "timeseries",
            },
            # Chart: Error rate % with threshold
            {
                "datasource": "Prometheus",
                "fieldConfig": {
                    "defaults": {"color": {"mode": "palette-classic"}},
                    "overrides": [],
                },
                "gridPos": {"h": 8, "w": 12, "x": 12, "y": 0},
                "id": 2,
                "options": {
                    "legend": {"calcs": ["mean", "max"], "displayMode": "table", "placement": "right"},
                    "tooltip": {"mode": "multi"},
                },
                "pluginVersion": "9.0.0",
                "targets": [
                    {
                        "expr": "corvinos_console_slo_error_rate_pct",
                        "legendFormat": "Error Rate %",
                        "refId": "A",
                    }
                ],
                "thresholds": {
                    "mode": "absolute",
                    "steps": [
                        {"color": "green", "value": None},
                        {"color": "yellow", "value": 0.05},
                        {"color": "red", "value": 0.1},
                    ],
                },
                "title": "Error Rate % (threshold: 0.1%)",
                "type": "timeseries",
            },
            # Gauge: Circuit breaker status
            {
                "datasource": "Prometheus",
                "fieldConfig": {
                    "defaults": {
                        "color": {"mode": "thresholds"},
                        "mappings": [],
                        "max": 1,
                        "min": 0,
                        "thresholds": {
                            "mode": "absolute",
                            "steps": [
                                {"color": "green", "value": None},
                                {"color": "red", "value": 0.5},
                            ],
                        },
                    },
                    "overrides": [],
                },
                "gridPos": {"h": 8, "w": 6, "x": 0, "y": 8},
                "id": 3,
                "options": {
                    "orientation": "auto",
                    "showThresholdLabels": False,
                    "showThresholdMarkers": True,
                },
                "pluginVersion": "9.0.0",
                "targets": [
                    {
                        "expr": "max(corvinos_console_circuit_breaker_state)",
                        "refId": "A",
                    }
                ],
                "title": "Circuit Breaker Status (0=OK, 1=TRIGGERED)",
                "type": "gauge",
            },
            # Stat: SLO Compliance
            {
                "datasource": "Prometheus",
                "fieldConfig": {
                    "defaults": {
                        "color": {"mode": "thresholds"},
                        "mappings": [],
                        "thresholds": {
                            "mode": "absolute",
                            "steps": [
                                {"color": "red", "value": None},
                                {"color": "yellow", "value": 50},
                                {"color": "green", "value": 95},
                            ],
                        },
                        "unit": "percent",
                    },
                    "overrides": [],
                },
                "gridPos": {"h": 8, "w": 6, "x": 6, "y": 8},
                "id": 4,
                "options": {
                    "graphMode": "area",
                    "orientation": "auto",
                    "reduceOptions": {
                        "calcs": ["lastNotNull"],
                        "fields": "",
                        "values": False,
                    },
                    "text": {},
                    "textMode": "auto",
                },
                "pluginVersion": "9.0.0",
                "targets": [
                    {
                        "expr": "100 * (1 - (corvinos_console_slo_error_rate_pct / 0.1))",
                        "refId": "A",
                    }
                ],
                "title": "SLO Compliance %",
                "type": "stat",
            },
            # Request rate (hidden until SLOs are met)
            {
                "datasource": "Prometheus",
                "fieldConfig": {
                    "defaults": {"color": {"mode": "palette-classic"}},
                    "overrides": [],
                },
                "gridPos": {"h": 8, "w": 12, "x": 12, "y": 8},
                "id": 5,
                "options": {
                    "legend": {"calcs": ["sum"], "displayMode": "list", "placement": "bottom"},
                    "tooltip": {"mode": "multi"},
                },
                "pluginVersion": "9.0.0",
                "targets": [
                    {
                        "expr": "sum(rate(corvinos_console_requests_total[5m])) by (status)",
                        "legendFormat": "{{status}}",
                        "refId": "A",
                    }
                ],
                "title": "Request Rate by Status (5m)",
                "type": "timeseries",
            },
        ],
        "refresh": "5s",
        "schemaVersion": 36,
        "style": "dark",
        "tags": ["corvinOS", "slo", "monitoring"],
        "templating": {"list": []},
        "time": {"from": "now-7d", "to": "now"},
        "timepicker": {},
        "timezone": "browser",
        "title": "SLO Monitoring",
        "uid": "slo-monitoring",
        "version": 0,
    }


def create_learning_loop_dashboard() -> Dict[str, Any]:
    """
    Dashboard 3: Learning Loop Health

    Charts:
    - Feedback signals (bars: outcome/preference/confidence/metric)
    - Config updates per skill (line)
    - Convergence rate (trending toward 1.0)
    """
    return {
        "annotations": {
            "list": [
                {
                    "builtIn": 1,
                    "datasource": "-- Prometheus --",
                    "enable": True,
                    "hide": True,
                    "iconColor": "rgba(0, 211, 255, 1)",
                    "name": "Annotations & Alerts",
                    "type": "dashboard",
                }
            ]
        },
        "description": "Learning loop health: feedback signals, config updates, convergence",
        "editable": True,
        "gnetId": None,
        "graphTooltip": 1,
        "id": None,
        "links": [],
        "panels": [
            # Chart: Feedback signals (bar)
            {
                "datasource": "Prometheus",
                "fieldConfig": {
                    "defaults": {"color": {"mode": "palette-classic"}},
                    "overrides": [],
                },
                "gridPos": {"h": 8, "w": 12, "x": 0, "y": 0},
                "id": 1,
                "options": {
                    "legend": {"calcs": ["sum"], "displayMode": "list", "placement": "bottom"},
                    "tooltip": {"mode": "multi"},
                },
                "pluginVersion": "9.0.0",
                "targets": [
                    {
                        "expr": "sum(increase(corvinos_console_learning_events_received_total[24h])) by (event_type)",
                        "legendFormat": "{{event_type}}",
                        "refId": "A",
                    }
                ],
                "title": "Feedback Signals Received (24h)",
                "type": "barchart",
            },
            # Chart: Config updates per skill
            {
                "datasource": "Prometheus",
                "fieldConfig": {
                    "defaults": {"color": {"mode": "palette-classic"}},
                    "overrides": [],
                },
                "gridPos": {"h": 8, "w": 12, "x": 12, "y": 0},
                "id": 2,
                "options": {
                    "legend": {"calcs": ["sum"], "displayMode": "table", "placement": "right"},
                    "tooltip": {"mode": "multi"},
                },
                "pluginVersion": "9.0.0",
                "targets": [
                    {
                        "expr": "sum(rate(corvinos_console_learning_events_received_total[5m])) by (skill_id)",
                        "legendFormat": "{{skill_id}}",
                        "refId": "A",
                    }
                ],
                "title": "Learning Activity by Skill (5m rate)",
                "type": "timeseries",
            },
            # Stat: Total feedback events
            {
                "datasource": "Prometheus",
                "fieldConfig": {
                    "defaults": {
                        "color": {"mode": "palette-classic"},
                        "mappings": [],
                        "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": None}]},
                    },
                    "overrides": [],
                },
                "gridPos": {"h": 4, "w": 6, "x": 0, "y": 8},
                "id": 3,
                "options": {
                    "graphMode": "none",
                    "orientation": "auto",
                    "reduceOptions": {
                        "calcs": ["lastNotNull"],
                        "fields": "",
                        "values": False,
                    },
                    "text": {},
                    "textMode": "auto",
                },
                "pluginVersion": "9.0.0",
                "targets": [
                    {
                        "expr": "increase(corvinos_console_learning_events_received_total[24h])",
                        "refId": "A",
                    }
                ],
                "title": "Total Feedback Events (24h)",
                "type": "stat",
            },
            # Stat: Active skills
            {
                "datasource": "Prometheus",
                "fieldConfig": {
                    "defaults": {
                        "color": {"mode": "palette-classic"},
                        "mappings": [],
                        "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": None}]},
                    },
                    "overrides": [],
                },
                "gridPos": {"h": 4, "w": 6, "x": 6, "y": 8},
                "id": 4,
                "options": {
                    "graphMode": "none",
                    "orientation": "auto",
                    "reduceOptions": {
                        "calcs": ["countNonNull"],
                        "fields": "",
                        "values": False,
                    },
                    "text": {},
                    "textMode": "auto",
                },
                "pluginVersion": "9.0.0",
                "targets": [
                    {
                        "expr": "count(count(corvinos_console_learning_events_received_total) by (skill_id))",
                        "refId": "A",
                    }
                ],
                "title": "Active Skills",
                "type": "stat",
            },
            # Stat: Last update
            {
                "datasource": "Prometheus",
                "fieldConfig": {
                    "defaults": {
                        "color": {"mode": "palette-classic"},
                        "mappings": [],
                        "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": None}]},
                    },
                    "overrides": [],
                },
                "gridPos": {"h": 4, "w": 6, "x": 12, "y": 8},
                "id": 5,
                "options": {
                    "graphMode": "none",
                    "orientation": "auto",
                    "reduceOptions": {
                        "calcs": ["lastNotNull"],
                        "fields": "",
                        "values": False,
                    },
                    "text": {},
                    "textMode": "auto",
                },
                "pluginVersion": "9.0.0",
                "targets": [
                    {
                        "expr": "time() - max(corvinos_console_learning_events_received_total) / 1000",
                        "refId": "A",
                    }
                ],
                "title": "Seconds Since Last Feedback",
                "type": "stat",
            },
            # Chart: Event type distribution
            {
                "datasource": "Prometheus",
                "fieldConfig": {
                    "defaults": {"color": {"mode": "palette-classic"}},
                    "overrides": [],
                },
                "gridPos": {"h": 8, "w": 12, "x": 0, "y": 12},
                "id": 6,
                "options": {
                    "legend": {"displayMode": "table", "placement": "bottom"},
                    "tooltip": {"mode": "single"},
                },
                "pluginVersion": "9.0.0",
                "targets": [
                    {
                        "expr": "sum(increase(corvinos_console_learning_events_received_total[7d])) by (event_type)",
                        "format": "table",
                        "instant": True,
                        "refId": "A",
                    }
                ],
                "title": "Feedback Distribution (7d)",
                "type": "piechart",
            },
        ],
        "refresh": "30s",
        "schemaVersion": 36,
        "style": "dark",
        "tags": ["corvinOS", "learning", "optimization"],
        "templating": {"list": []},
        "time": {"from": "now-7d", "to": "now"},
        "timepicker": {},
        "timezone": "browser",
        "title": "Learning Loop Health",
        "uid": "learning-health",
        "version": 0,
    }


def export_dashboards_json() -> Dict[str, Any]:
    """
    Export all 3 dashboards as importable JSON.

    Returns:
        Dict with 'dashboards' key containing list of JSON objects
    """
    return {
        "dashboards": [
            create_model_routing_dashboard(),
            create_slo_monitoring_dashboard(),
            create_learning_loop_dashboard(),
        ]
    }


if __name__ == "__main__":
    # Export all dashboards
    dashboards = export_dashboards_json()

    # Save to file
    with open("/tmp/grafana_dashboards.json", "w") as f:
        json.dump(dashboards, f, indent=2)

    print("✅ Exported 3 Grafana dashboards to /tmp/grafana_dashboards.json")
    print(f"   - Model Routing Overview")
    print(f"   - SLO Monitoring")
    print(f"   - Learning Loop Health")
    print(f"\nImport in Grafana: Settings → Import → Upload JSON")
