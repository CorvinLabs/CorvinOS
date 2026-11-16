"""Personal knowledge base: one encrypted, owner-bound graph per person (ADR-2325, ADR-2326, ADR-2327).

The project knowledge base (Corvin-Knowledge) is shared and permanent. This package is the
opposite: private to one owner, encrypted at rest, erasable, and never projected onto the
shared task board. Submodules are imported directly; nothing heavy is exported here.

Specification: PLAN-0973 in Corvin-Knowledge. Personal decoder: CONCEPT-0134.
"""

__all__: list[str] = []
