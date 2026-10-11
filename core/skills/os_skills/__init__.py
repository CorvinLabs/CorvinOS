"""OS Skills (ADR-0532): the Skills that replace control-plane subsystems.

A regular package on purpose. As a namespace package (no ``__init__.py``) pytest's default import
mode put THIS directory on ``sys.path`` for every ``os_skills/<pkg>/tests`` package it collected, and
the ``audit`` subpackage here then shadowed the core audit writer (``import audit``) for the rest of
the run (review 2026-10-12, ACP-1). Keep this file; ``tests/skills/test_no_shadowing_syspath_inserts.py``
checks it.
"""
