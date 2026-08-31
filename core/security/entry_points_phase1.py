"""Phase 1 entry point definitions (50 entry points)."""

from .registry import CallSiteRegistry, EntryPoint, EntryPointCategory

# Create global registry
ENTRY_POINT_REGISTRY = CallSiteRegistry()

# Flask Routes (20 entry points)
FLASK_ROUTES = [
    EntryPoint(
        name='chat_list_sessions',
        category=EntryPointCategory.FLASK_ROUTE,
        capability_required='read_chat_sessions',
        module_path='core.console.routes.chat',
        function_name='list_sessions',
    ),
    EntryPoint(
        name='chat_get_session',
        category=EntryPointCategory.FLASK_ROUTE,
        capability_required='read_chat_session',
        module_path='core.console.routes.chat',
        function_name='get_session',
    ),
    EntryPoint(
        name='chat_create_session',
        category=EntryPointCategory.FLASK_ROUTE,
        capability_required='write_chat_session',
        module_path='core.console.routes.chat',
        function_name='create_session',
    ),
    EntryPoint(
        name='chat_send_message',
        category=EntryPointCategory.FLASK_ROUTE,
        capability_required='write_chat',
        module_path='core.console.routes.chat',
        function_name='send_message',
    ),
    EntryPoint(
        name='chat_delete_session',
        category=EntryPointCategory.FLASK_ROUTE,
        capability_required='delete_chat_session',
        module_path='core.console.routes.chat',
        function_name='delete_session',
    ),
    # Admin routes (5)
    EntryPoint(
        name='admin_settings',
        category=EntryPointCategory.FLASK_ROUTE,
        capability_required='admin_settings',
        module_path='core.console.routes.admin',
        function_name='get_settings',
    ),
    EntryPoint(
        name='admin_update_settings',
        category=EntryPointCategory.FLASK_ROUTE,
        capability_required='admin_settings',
        module_path='core.console.routes.admin',
        function_name='update_settings',
    ),
    EntryPoint(
        name='admin_users',
        category=EntryPointCategory.FLASK_ROUTE,
        capability_required='admin_users',
        module_path='core.console.routes.admin',
        function_name='list_users',
    ),
    # Profile routes (5)
    EntryPoint(
        name='profile_get',
        category=EntryPointCategory.FLASK_ROUTE,
        capability_required='read_profile',
        module_path='core.console.routes.profile',
        function_name='get_profile',
    ),
    EntryPoint(
        name='profile_update',
        category=EntryPointCategory.FLASK_ROUTE,
        capability_required='write_profile',
        module_path='core.console.routes.profile',
        function_name='update_profile',
    ),
    # Audit routes (5)
    EntryPoint(
        name='audit_list',
        category=EntryPointCategory.FLASK_ROUTE,
        capability_required='read_audit',
        module_path='core.console.routes.audit_routes',
        function_name='list_audit_events',
    ),
    EntryPoint(
        name='audit_verify',
        category=EntryPointCategory.FLASK_ROUTE,
        capability_required='admin_audit',
        module_path='core.console.routes.audit_routes',
        function_name='verify_chain',
    ),
]

# CLI Commands (5 entry points)
CLI_COMMANDS = [
    EntryPoint(
        name='cli_audit_verify',
        category=EntryPointCategory.CLI_COMMAND,
        capability_required='admin_audit',
        module_path='operator.cli.commands.audit_verify',
        function_name='audit_verify',
    ),
    EntryPoint(
        name='cli_audit_scan',
        category=EntryPointCategory.CLI_COMMAND,
        capability_required='admin_audit',
        module_path='operator.cli.commands.audit_scan',
        function_name='audit_scan',
    ),
    EntryPoint(
        name='cli_security_status',
        category=EntryPointCategory.CLI_COMMAND,
        capability_required='read_security',
        module_path='operator.cli.commands.security_status',
        function_name='security_status',
    ),
    EntryPoint(
        name='cli_health_check',
        category=EntryPointCategory.CLI_COMMAND,
        capability_required='read_health',
        module_path='operator.cli.commands.health',
        function_name='health_check',
    ),
    EntryPoint(
        name='cli_bootstrap_security',
        category=EntryPointCategory.CLI_COMMAND,
        capability_required='admin_bootstrap',
        module_path='operator.cli.commands.bootstrap',
        function_name='bootstrap_security',
    ),
]

# Bridge Handlers (2 entry points)
BRIDGE_HANDLERS = [
    EntryPoint(
        name='bridge_chat_message',
        category=EntryPointCategory.BRIDGE_HANDLER,
        capability_required='write_chat',
        module_path='operator.bridges.shared.adapter',
        function_name='handle_chat_message',
    ),
    EntryPoint(
        name='bridge_task_update',
        category=EntryPointCategory.BRIDGE_HANDLER,
        capability_required='write_task',
        module_path='operator.bridges.shared.adapter',
        function_name='handle_task_update',
    ),
]

# Plugin Loaders (1 entry point)
PLUGIN_LOADERS = [
    EntryPoint(
        name='plugin_load',
        category=EntryPointCategory.PLUGIN_ENTRY,
        capability_required='load_plugin',
        module_path='core.plugins.loader',
        function_name='load_plugin',
    ),
]

# Forge Tools (5+ entry points)
FORGE_TOOLS = [
    EntryPoint(
        name='forge_audit_list_events',
        category=EntryPointCategory.FORGE_TOOL,
        capability_required='read_audit',
        module_path='operator.forge.mcp_tools',
        function_name='audit_list_events',
    ),
    EntryPoint(
        name='forge_audit_verify_chain',
        category=EntryPointCategory.FORGE_TOOL,
        capability_required='admin_audit',
        module_path='operator.forge.mcp_tools',
        function_name='audit_verify_chain',
    ),
    EntryPoint(
        name='forge_security_summary',
        category=EntryPointCategory.FORGE_TOOL,
        capability_required='read_security',
        module_path='operator.forge.mcp_tools',
        function_name='security_summary',
    ),
    EntryPoint(
        name='forge_health_status',
        category=EntryPointCategory.FORGE_TOOL,
        capability_required='read_health',
        module_path='operator.forge.mcp_tools',
        function_name='health_status',
    ),
    EntryPoint(
        name='forge_list_sessions',
        category=EntryPointCategory.FORGE_TOOL,
        capability_required='read_chat_sessions',
        module_path='operator.forge.mcp_tools',
        function_name='list_sessions',
    ),
]

# Register all entry points
for ep in FLASK_ROUTES + CLI_COMMANDS + BRIDGE_HANDLERS + PLUGIN_LOADERS + FORGE_TOOLS:
    ENTRY_POINT_REGISTRY.register(ep)

# Summary statistics
ENTRY_POINTS_BY_CATEGORY = {
    'flask_route': len(FLASK_ROUTES),
    'cli_command': len(CLI_COMMANDS),
    'bridge_handler': len(BRIDGE_HANDLERS),
    'plugin_entry': len(PLUGIN_LOADERS),
    'forge_tool': len(FORGE_TOOLS),
}

TOTAL_ENTRY_POINTS = sum(ENTRY_POINTS_BY_CATEGORY.values())
