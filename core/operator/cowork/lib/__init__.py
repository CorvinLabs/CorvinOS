# cowork — Multi-Persona-Layer for Claude Code.
# Public API:
#   resolver.load(name)                  -> dict | None
#   resolver.resolve(name, overrides)    -> dict
#   resolver.list_available()            -> list[dict]
#   resolver.merge_into_args(profile, …) -> list[str]   # MCP/add_dirs flag-builder
