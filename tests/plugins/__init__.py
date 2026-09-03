"""CorvinOS Plugin Test Suite.

Comprehensive testing for 18 development plugins across:
- Unit tests (initialization, core functions, error handling)
- E2E integration tests (full lifecycle, audit chain integration)
- Adversarial tests (hostile inputs, race conditions, thread safety)

Test structure:
- tests/plugins/unit/         → Plugin unit tests (3-4 tests per plugin)
- tests/plugins/integration/  → E2E + lifecycle tests (1-2 tests per plugin)
- tests/plugins/adversarial/  → Hostile inputs, races, boundaries
"""
