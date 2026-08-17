"""Autonomous Chat Observatory (ACO) — ADR-0174.

Layer 1: Observable Chat (chat_debug.jsonl) — implemented in chat_runtime.py
Layer 2: Replay Engine        — replay.py
Layer 3: Anomaly Detection    — anomaly_detector.py
Layer 4: Autonomous Diagnosis — diagnosis.py
Layer 5: Self-Repair Loop     — repair_actions.py, actuating, ADR-0178
Layer 6: Self-Improving Maintenance Loop — maintenance_loop.py + maintainer_capability.py, ADR-0178
"""
