/**
 * Models console — ONE panel for routing, usage & cost, learning and the
 * catalogue (ADR-0885). Replaces /app/engine-config, /app/model-cost-optimizer
 * and /app/model-selection.
 *
 * Tab ↔ URL: the tab is `?tab=` (deep-linkable; browser back works because a
 * tab switch is a PUSH). A missing or unknown tab is rewritten to `routing`
 * with REPLACE. `?preselect=<model id>&turn=os|worker` is the Catalog → Routing
 * hand-off: consumed once by the Routing tab, then cleared with REPLACE.
 *
 * Radix unmounts inactive tab content, which would discard a half-edited
 * Routing form on a tab switch — so the Routing content is force-mounted and
 * merely hidden while another tab is active.
 */
import { useCallback, useEffect } from "react";
import { useSearchParams } from "react-router-dom";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ModelsHeader } from "./header";
import { DEFAULT_TAB, TAB_IDS, TAB_LABEL, isTabId, type TabId } from "./tabs";
import { RoutingTab } from "./tabs/routing";
import { UsageCostTab } from "./tabs/usage-cost";
import { LearningTab } from "./tabs/learning";
import { CatalogTab } from "./tabs/catalog";

export function ModelsPage() {
  const [params, setParams] = useSearchParams();
  const raw = params.get("tab");
  const active: TabId = isTabId(raw) ? raw : DEFAULT_TAB;

  // Missing / bogus tab → canonical URL, without a history entry.
  useEffect(() => {
    if (raw !== active) {
      const next = new URLSearchParams(params);
      next.set("tab", active);
      setParams(next, { replace: true });
    }
  }, [raw, active, params, setParams]);

  const goTo = useCallback(
    (tab: TabId, extra?: Record<string, string>) => {
      const next = new URLSearchParams(params);
      next.set("tab", tab);
      if (extra) for (const [k, v] of Object.entries(extra)) next.set(k, v);
      setParams(next); // PUSH — back returns to the previous tab
    },
    [params, setParams],
  );

  const clearParams = useCallback(
    (keys: string[]) => {
      const next = new URLSearchParams(params);
      for (const k of keys) next.delete(k);
      setParams(next, { replace: true });
    },
    [params, setParams],
  );

  return (
    <div className="max-w-7xl mx-auto p-6 space-y-6" data-testid="models-console">
      <ModelsHeader onGoTo={(tab) => goTo(tab)} />

      <Tabs value={active} onValueChange={(v) => { if (isTabId(v)) goTo(v); }}>
        <TabsList>
          {TAB_IDS.map((id) => (
            <TabsTrigger key={id} value={id}>{TAB_LABEL[id]}</TabsTrigger>
          ))}
        </TabsList>

        {/* forceMount: keeps a half-edited pin form alive across tab switches. */}
        <TabsContent value="routing" forceMount hidden={active !== "routing"} className="mt-6">
          <RoutingTab
            active={active === "routing"}
            preselect={params.get("preselect")}
            preselectTurn={params.get("turn")}
            onPreselectConsumed={() => clearParams(["preselect", "turn"])}
          />
        </TabsContent>
        <TabsContent value="usage-cost" className="mt-6">
          <UsageCostTab />
        </TabsContent>
        <TabsContent value="learning" className="mt-6">
          <LearningTab />
        </TabsContent>
        <TabsContent value="catalog" className="mt-6">
          <CatalogTab onUseFor={(id, turn) => goTo("routing", { preselect: id, turn })} />
        </TabsContent>
      </Tabs>

      <p className="text-xs text-muted-foreground pt-4 border-t">
        Every save on this page is an audited event in the tenant's audit chain.
      </p>
    </div>
  );
}

export default ModelsPage;
