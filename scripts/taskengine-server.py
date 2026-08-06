#!/usr/bin/env python3
"""TaskEngine server launcher (standalone).

Workaround: Python's stdlib 'operator' module shadows 'operator.task_analysis' namespace.
Use this script instead of 'python -m operator.task_analysis.server'.
"""

import sys
import os

# Ensure the operator subpackage is findable
# by importing it first, before anything tries operator.task_analysis
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

# Force operator submodule to be registered as a package
import operator as stdlib_operator
_task_analysis_path = os.path.join(project_root, "operator", "task_analysis")
if not hasattr(stdlib_operator, 'task_analysis'):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "operator.task_analysis",
        os.path.join(_task_analysis_path, "__init__.py"),
        submodule_search_locations=[_task_analysis_path],
    )
    task_analysis_pkg = importlib.util.module_from_spec(spec)
    sys.modules['operator.task_analysis'] = task_analysis_pkg
    spec.loader.exec_module(task_analysis_pkg)

# NOW we can import from operator.task_analysis
from operator.task_analysis.server import main

if __name__ == "__main__":
    main()
