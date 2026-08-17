"""corvin_core — the OS kernel modules, extracted out of corvin_console so the
core/bridge never imports the Console (ADR-0352). Physically under core/console/
only to reuse the existing `core/console`→wheel-root source mapping (no new
packaging rule = no Windows-boot-class risk); it is a TOP-LEVEL package
(`corvin_core`), NOT part of the Console. When the Console is extracted as a
separate distribution, corvin_core moves to its own top-level source.
"""
