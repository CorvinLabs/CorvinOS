/**
 * Marketplace — ONE panel for browsing the plugin index, managing installed
 * plugins, skill packages and MCP tools (ADR-0892). Replaces
 * /app/marketplace-hub (a synthetic index that fetched a 404), the manifest's
 * /app/plugin-center (a deleted component: the 404 page) and /app/packages.
 *
 * Tab ↔ URL exactly as in the Models console (ADR-0885): `?tab=` is the tab,
 * a switch is a PUSH, a missing or unknown tab is rewritten to `browse` with
 * REPLACE. Every action on every tab calls a route that changes this install
 * and audits it — there is no mock button on this page.
 */
import { useCallback, useEffect } from "react";
import { useSearchParams } from "react-router-dom";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { MarketplaceHeader } from "./header";
import { DEFAULT_TAB, TAB_IDS, TAB_LABEL, isTabId, type TabId } from "./tabs";
import { BrowseTab } from "./tabs/browse";
import { InstalledTab } from "./tabs/installed";
import { PackagesTab } from "./tabs/packages";
import { ToolsTab } from "./tabs/tools";

export function MarketplacePage() {
  const [params, setParams] = useSearchParams();
  const raw = params.get("tab");
  const active: TabId = isTabId(raw) ? raw : DEFAULT_TAB;

  useEffect(() => {
    if (raw !== active) {
      const next = new URLSearchParams(params);
      next.set("tab", active);
      setParams(next, { replace: true });
    }
  }, [raw, active, params, setParams]);

  const goTo = useCallback(
    (tab: TabId) => {
      const next = new URLSearchParams(params);
      next.set("tab", tab);
      setParams(next); // PUSH — back returns to the previous tab
    },
    [params, setParams],
  );

  return (
    <div className="max-w-7xl mx-auto p-6 space-y-6" data-testid="marketplace-console">
      <MarketplaceHeader onGoTo={goTo} />

      <Tabs value={active} onValueChange={(v) => { if (isTabId(v)) goTo(v); }}>
        <TabsList>
          {TAB_IDS.map((id) => (
            <TabsTrigger key={id} value={id}>{TAB_LABEL[id]}</TabsTrigger>
          ))}
        </TabsList>

        <TabsContent value="browse" className="mt-6"><BrowseTab onGoTo={goTo} /></TabsContent>
        <TabsContent value="installed" className="mt-6"><InstalledTab onGoTo={goTo} /></TabsContent>
        <TabsContent value="packages" className="mt-6"><PackagesTab /></TabsContent>
        <TabsContent value="tools" className="mt-6"><ToolsTab /></TabsContent>
      </Tabs>
    </div>
  );
}

export default MarketplacePage;
