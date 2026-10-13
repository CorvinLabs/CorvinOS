"""A2A transport stack (Layer 38) — moved here from corvin_operator/bridges/shared/ (ADR-2258).

Old bare-name imports (``import a2a_task_state``) keep working through alias shims left in
``corvin_operator/bridges/shared/``: the shim puts THIS module into ``sys.modules`` under the old
name, so both names are one object (monkeypatches and module state are shared).
"""
