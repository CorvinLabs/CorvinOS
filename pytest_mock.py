"""
Minimal pytest mock — provides decorators & fixtures without pytest installed.
This allows test files to be imported and executed without a pytest dependency.
"""

import sys
from functools import wraps
from typing import Any, Callable, Optional

# ============================================================================
# PYTEST MARKERS
# ============================================================================

class Mark:
    """Mock pytest marker"""
    def __init__(self, name: str, *args, **kwargs):
        self.name = name
        self.args = args
        self.kwargs = kwargs

    def __call__(self, func):
        """Mark a function"""
        if not hasattr(func, "pytestmark"):
            func.pytestmark = []
        func.pytestmark.append(self)
        return func

class MarkDecorator:
    """Mock pytest.mark namespace"""

    def __getattr__(self, name: str) -> Mark:
        """Return a marker for any name"""
        return Mark(name)

mark = MarkDecorator()

# ============================================================================
# PYTEST FIXTURES
# ============================================================================

def fixture(scope: str = "function"):
    """Decorator for fixture functions"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)
        wrapper._is_fixture = True
        wrapper._scope = scope
        return wrapper
    return decorator

# ============================================================================
# PYTEST ASSERTIONS
# ============================================================================

class ExceptionInfo:
    """Mock ExceptionInfo"""
    def __init__(self, exc_type, exc_value, traceback):
        self.type = exc_type
        self.value = exc_value
        self.traceback = traceback

def raises(expected_exception, *args, **kwargs):
    """Mock pytest.raises context manager"""
    class RaisesContext:
        def __init__(self, exc):
            self.expected_exception = exc
            self.exc_info = None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            if exc_type is None:
                raise AssertionError(f"Expected {self.expected_exception.__name__} but no exception was raised")
            if not issubclass(exc_type, self.expected_exception):
                return False
            self.exc_info = ExceptionInfo(exc_type, exc_val, exc_tb)
            return True

    return RaisesContext(expected_exception)

# ============================================================================
# PYTEST FUNCTIONS
# ============================================================================

def skip(reason: str = ""):
    """Skip a test"""
    raise SkipTest(reason)

def xfail(reason: str = ""):
    """Mark test as expected to fail"""
    raise XFail(reason)

class SkipTest(Exception):
    """Exception for skipped tests"""
    pass

class XFail(Exception):
    """Exception for expected failures"""
    pass

# ============================================================================
# PYTEST COMPARISONS
# ============================================================================

class Approx:
    """Mock pytest.approx for floating point comparisons"""
    def __init__(self, expected, rel=1e-6, abs=1e-12):
        self.expected = expected
        self.rel = rel
        self.abs = abs

    def __eq__(self, actual):
        if isinstance(self.expected, (list, tuple)):
            return all(self._compare(e, a) for e, a in zip(self.expected, actual))
        return self._compare(self.expected, actual)

    def __repr__(self):
        return f"{self.expected} ± {self.abs}"

    def _compare(self, expected, actual):
        tolerance = max(self.rel * abs(expected), self.abs)
        return abs(expected - actual) <= tolerance

approx = Approx

# ============================================================================
# MODULE SETUP
# ============================================================================

# Install as pytest module
sys.modules['pytest'] = sys.modules[__name__]

# Export public API
__all__ = [
    'mark',
    'fixture',
    'raises',
    'skip',
    'xfail',
    'approx',
    'SkipTest',
    'XFail',
]
