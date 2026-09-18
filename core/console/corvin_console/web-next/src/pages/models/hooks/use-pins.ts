/** The turn pins — what actually serves an OS turn and a worker turn.
 *
 *  Read from `GET /settings/engine` (`spec.engine_models[default_engine]`,
 *  ADR-0759), the same route the Routing tab writes to. Since ADR-0885 step
 *  2b that route resolves the pins through the runtime's own resolver
 *  (`engine_models.get_tenant_engine_model`), the function the cost optimizer
 *  reports `cost_os_model_pin` / `acs_worker_model_pin` through — so the two
 *  cannot disagree. The dev-only assert below is a tripwire for that parity;
 *  the server-side test is the real guard. */
import { useQuery } from "@tanstack/react-query";
import { getOsEngineSetting, type OsEngineSetting } from "@/lib/api/engines";
import type { DashboardStatus } from "../components/cost-charts";

export const ENGINE_SETTING_KEY = ["os-engine-setting"] as const;

export interface Pins {
  default_engine: string;
  os_model: string | null;
  worker_model: string | null;
  provider: string | null;
}

export function pinsOf(setting: OsEngineSetting | undefined): Pins | undefined {
  if (!setting) return undefined;
  const engine = setting.default_engine ?? "claude_code";
  const cfg = setting.engine_models?.[engine];
  return {
    default_engine: engine,
    os_model: cfg?.os_model ?? null,
    worker_model: cfg?.worker_model ?? null,
    provider: cfg?.provider ?? null,
  };
}

export function useOsEngineSetting() {
  return useQuery({
    queryKey: ENGINE_SETTING_KEY,
    queryFn: ({ signal }) => getOsEngineSetting(signal),
    staleTime: 30_000,
  });
}

export function usePins(status?: DashboardStatus) {
  const q = useOsEngineSetting();
  const pins = pinsOf(q.data);
  if (import.meta.env.DEV && pins && status) {
    // Parity tripwire (not rendered): header pins vs the cost optimizer's pins.
    console.assert(
      (status.cost_os_model_pin ?? null) === pins.os_model &&
        (status.acs_worker_model_pin ?? null) === pins.worker_model,
      "pin parity: /settings/engine and cost status disagree",
    );
  }
  return { pins, setting: q.data, loading: q.isLoading, error: q.error };
}
