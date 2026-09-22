"""Phase 9: User Feedback Loop System

Comprehensive feedback collection, triage, and quick-fix infrastructure.

Components:
  - phase9_feedback_portal: Main feedback collection system
  - feedback_models: Data models for feedback (bug, feature, survey)
  - feedback_triage: Triage & prioritization logic
  - feedback_notifier: Email notifications to operators
"""

__all__ = [
    "FeedbackPortal",
    "FeedbackType",
    "FeedbackSeverity",
    "TriageEngine",
    "FeedbackNotifier",
]
