"""A2A transport stack (Layer 38) — modules moved here from corvin_operator/bridges/shared/ (ADR-2258).

What stays in bridges/shared (standalone-deployable single files, ADR-2258 option A): the relay, sender,
receiver, client CLI, friendship, attachments, manifest, http_server and everything that anchors on its own
``__file__``. Moved modules keep their old bare name through an alias shim in bridges/shared that puts THIS
module into ``sys.modules`` under the old name: one object, so monkeypatches and module state are shared.
"""
