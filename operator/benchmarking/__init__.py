"""TDE Benchmark Suite — Token Savings SIMULATION.

Deterministic simulation of the TDE token-savings hypothesis: the harness
encodes assumed per-category savings ratios and reports what that model
implies. It executes NO TDE code and measures NO real LLM usage
(adversarial review 2026-07-24 — see harness.py's honesty note). For
measured numbers use operator/orchestration/tde/bench.py.

Modules:
  - harness.py: Simulation runner (deterministic token model)
  - analysis.py: Descriptive statistics of the simulation output
  - fixtures.py: Task definition and loading
"""
