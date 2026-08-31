# conftest.py — pytest configuration for operator/forge/tests/
#
# test_mcp.py is a standalone test driver (run as: python3 test_mcp.py).
# It uses a custom @with_client() decorator that wraps test functions so they
# have no parameters at call time, but functools.wraps copies the original
# signature (fn(client, root)), causing pytest to misidentify 'client' and
# 'root' as pytest fixture names and fail with "fixture not found".
#
# The correct way to run test_mcp.py is: python3 operator/forge/tests/test_mcp.py
# or via run-all-tests.sh, which invokes it that way.
collect_ignore = ["test_mcp.py"]
