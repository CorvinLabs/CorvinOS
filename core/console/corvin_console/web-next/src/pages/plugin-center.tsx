/**
 * Plugin Center — the single console surface for everything that extends CorvinOS.
 *
 * CONSOLIDATION (roadmap de-dup of the "plugin triple"): three sidebar entries
 * with near-synonymous names — "Extensions", "MCP Plugins", "Plugins" — used to
 * point at three genuinely DISTINCT subsystems, which sent operators to the wrong
 * page constantly. Each subsystem keeps its own backend, data model, CLI and page
 * component; only the NAVIGATION is unified here, as three tabs under one entry:
 *
 *   • Plugins         — the per-tenant plugin registry / lifecycle (ADR-0233,
 *                        route /plugins, CLI: plugin registry). Enable/disable,
 *                        settings schema, consent, PII risk, self-healing.
 *   • MCP Tools       — installed MCP tool servers (ADR-0096, route /mcp-plugins,
 *                        CLI: corvin-mcp). Per-scope activation, locality, secrets.
 *   • Layer Extensions — the Layer Extension API (ADR-0142, route /extensions,
 *                        CLI: corvin-layer). Deny-wins security layers + hooks.
 *
 * Nothing is dropped: each tab renders the original, unchanged page component. The
 * standalone routes (/app/extensions, /app/mcp-plugins, /app/plugins) stay mounted
 * for deep-link stability but are removed from the sidebar (see NAV_EXEMPT in
 * tests/unit/panel-nav-wiring.test.ts).
 *
 * Radix Tabs unmounts inactive tab content, so only the active tab's data queries
 * fire — the three subsystems are never all polled at once.
 */
import { useSearchParams } from "react-router-dom";
import { Blocks, Package, Puzzle, ShoppingCart } from "lucide-react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ExtensionsPage } from "@/pages/extensions";
import McpPluginsPage from "@/pages/mcp-plugins";
import { PluginsPage } from "@/pages/plugins";
import { MarketplaceTab } from "@/components/MarketplaceTab";

const TABS = ["plugins", "marketplace", "mcp", "extensions"] as const;
type TabId = (typeof TABS)[number];

function isTabId(v: string | null): v is TabId {
  return v !== null && (TABS as readonly string[]).includes(v);
}

export function PluginCenterPage() {
  const [params, setParams] = useSearchParams();
  const raw = params.get("tab");
  const active: TabId = isTabId(raw) ? raw : "plugins";

  const onChange = (value: string) => {
    const next = new URLSearchParams(params);
    next.set("tab", value);
    setParams(next, { replace: true });
  };

  return (
    <div className="p-6">
      <div className="mb-4">
        <h1 className="text-2xl font-bold flex items-center gap-2">
          <Puzzle className="h-6 w-6" />
          Plugins &amp; Extensions
        </h1>
        <p className="text-sm text-muted-foreground mt-0.5">
          Everything that extends CorvinOS — the plugin registry, MCP tool servers,
          and security layer extensions — in one place.
        </p>
      </div>

      <Tabs value={active} onValueChange={onChange}>
        <TabsList>
          <TabsTrigger value="plugins" data-testid="plugin-center-tab-plugins">
            <Puzzle className="h-4 w-4" /> Plugins
          </TabsTrigger>
          <TabsTrigger value="marketplace" data-testid="plugin-center-tab-marketplace">
            <ShoppingCart className="h-4 w-4" /> Marketplace
          </TabsTrigger>
          <TabsTrigger value="mcp" data-testid="plugin-center-tab-mcp">
            <Package className="h-4 w-4" /> MCP Tools
          </TabsTrigger>
          <TabsTrigger value="extensions" data-testid="plugin-center-tab-extensions">
            <Blocks className="h-4 w-4" /> Layer Extensions
          </TabsTrigger>
        </TabsList>

        {/* Each tab renders its original page component unchanged. Radix unmounts
            inactive content, so only the active subsystem's queries run. */}
        <TabsContent value="plugins">
          <PluginsPage />
        </TabsContent>
        <TabsContent value="marketplace">
          <MarketplaceTab />
        </TabsContent>
        <TabsContent value="mcp">
          <McpPluginsPage />
        </TabsContent>
        <TabsContent value="extensions">
          <ExtensionsPage />
        </TabsContent>
      </Tabs>
    </div>
  );
}

export default PluginCenterPage;
