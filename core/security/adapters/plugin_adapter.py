"""Plugin loader security gate (Finding #10)."""

import logging

logger = logging.getLogger(__name__)


class PluginSecurityGate:
    """Security gate for plugin loading."""

    def __init__(self, pipeline=None):
        self.pipeline = pipeline

    async def check_plugin_load(self, plugin_id: str, plugin_cls, tenant_config):
        """Check if plugin is allowed to load (Finding #10)."""
        # Check required capabilities
        if hasattr(plugin_cls, 'required_capabilities'):
            required_caps = plugin_cls.required_capabilities
            for cap in required_caps:
                if not hasattr(tenant_config, 'capabilities') or \
                   not getattr(tenant_config.capabilities, cap, False):
                    logger.warning(f"[PluginGate] {plugin_id} missing capability: {cap}")
                    return False, f"missing_capability: {cap}"

        # Validate plugin metadata
        if not hasattr(plugin_cls, 'plugin_id') or not plugin_cls.plugin_id:
            logger.warning(f"[PluginGate] {plugin_id} missing plugin_id")
            return False, "invalid_plugin_metadata"

        logger.debug(f"[PluginGate] Plugin {plugin_id} allowed to load")
        return True, ""
