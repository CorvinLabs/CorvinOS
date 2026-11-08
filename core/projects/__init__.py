"""CorvinOS projects (ADR-2315): a directory the user owns, indexed per tenant.

* ``manifest``  - ``project.yaml`` (the truth) : schema, validation, atomic write.
* ``registry``  - ``<tenant_home>/projects/registry.json`` (an index, rebuildable from the directories)
                  and the path rules for the two calls that take a path from a client.
* ``fragments`` - the declarative fragment/template engine: plan (no writes), apply (idempotent),
                  repair; closure checks of catalogues, templates and presets.

Pure standard library + PyYAML; no console import (the console's service layer calls this package).
"""
