"""Fixtures for the plugin integration tests.

These modules were moved here from tests/e2e/plugin_verification/ without
their conftest; the fixtures they request (isolated_plugin_env,
load_order_tracker, ...) live in that package's conftest and are re-exported.
"""
from tests.e2e.plugin_verification.conftest import *  # noqa: F401,F403
