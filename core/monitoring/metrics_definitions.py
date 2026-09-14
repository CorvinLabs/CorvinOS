"""Metrics Definitions — Standard CorvinOS metrics"""

METRICS = {
    # Skill execution
    "skill_execution_count": {"type": "counter", "help": "Total skill executions"},
    "skill_latency_ms": {"type": "histogram", "help": "Skill execution latency"},
    "skill_error_rate": {"type": "gauge", "help": "% skill errors"},

    # Learning
    "learning_confidence_mean": {"type": "gauge", "help": "Mean skill confidence"},
    "learning_convergence_detected": {"type": "counter", "help": "Skills converged"},
    "learning_feedback_latency_s": {"type": "histogram", "help": "Feedback latency"},

    # System
    "api_latency_ms": {"type": "histogram", "help": "API response latency"},
    "cache_hit_rate": {"type": "gauge", "help": "Cache efficiency"},
    "audit_chain_valid": {"type": "gauge", "help": "Audit chain integrity"},
}
