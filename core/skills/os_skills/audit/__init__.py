"""Audit Trail + Compliance Module for DataHub Creator Learning Loop.

This module provides:
- AuditTrail: immutable, hash-chained event log
- ComplianceReporter: GDPR-compliant exports with PII redaction
- PrometheusMetrics: production monitoring

All events are tenant-scoped and audit-first (write to chain before persistence).
"""
