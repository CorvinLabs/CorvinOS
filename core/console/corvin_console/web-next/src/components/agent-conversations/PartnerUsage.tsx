/**
 * What tasks delegated to ONE paired peer consumed — the peer's engine-reported tokens from its signed
 * answers, summed over the console's counting window, priced with this installation's own rates.
 *
 * Rules the copy and the numbers follow: the token counts are what the peer's engine REPORTED (attributed,
 * not verified); a model this installation has no rate for is shown as tokens only, never at a price the
 * peer supplied; the peer's catalogue figure is a separate, labelled hint that is never added to the cost.
 * Sums only — no averages, no percentages.
 * Backend: routes/federation_routes.py `GET /federation/usage`.
 */
import { useQuery } from "@tanstack/react-query";
import { Gauge } from "lucide-react";
import { getPartnerUsage, type PartnerUsage as Row } from "@/lib/api/federation";

const nf = new Intl.NumberFormat("en-US");
const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 4 });

export function PartnerUsage({ peerId }: { peerId: string }) {
  const q = useQuery({ queryKey: ["federation", "usage"], queryFn: getPartnerUsage });
  const row: Row | undefined = q.data?.partners.find((p) => p.endpoint_id === peerId);
  const win = q.data?.window;
  const period = win?.active && win.since_iso
    ? `Since ${new Date(win.since_iso).toLocaleString("en-US")}` : "All time";

  return (
    <section aria-label="Usage of delegated tasks" data-testid="partner-usage" className="rounded-md border p-3 text-sm">
      <div className="flex items-center gap-2">
        <Gauge className="h-4 w-4" aria-hidden />
        <h2 className="font-medium">Usage of tasks run by this peer</h2>
      </div>
      {win && <p className="mt-1 text-xs text-muted-foreground" data-testid="partner-usage-window">{period}</p>}

      {q.isError && <p role="alert" className="mt-2 text-xs text-destructive">Could not load usage.</p>}

      {q.isSuccess && !row && (
        <p className="mt-2 text-xs" data-testid="partner-usage-empty">No delegated tasks to this peer in this period.</p>
      )}

      {row && (
        <div className="mt-2 space-y-2" data-testid="partner-usage-row">
          <dl className="grid grid-cols-2 gap-x-3 gap-y-0.5 text-xs">
            <dt className="text-muted-foreground">Tasks</dt>
            <dd data-testid="partner-usage-tasks">{nf.format(row.tasks)} ({nf.format(row.tasks_with_usage)} with token data)</dd>
            <dt className="text-muted-foreground">Input tokens</dt><dd>{nf.format(row.input_tokens)}</dd>
            <dt className="text-muted-foreground">Output tokens</dt><dd>{nf.format(row.output_tokens)}</dd>
            <dt className="text-muted-foreground">Cache read tokens</dt><dd>{nf.format(row.cache_read_tokens)}</dd>
            <dt className="text-muted-foreground">Cache write tokens</dt><dd>{nf.format(row.cache_write_tokens)}</dd>
            <dt className="text-muted-foreground">Cost at your rates</dt>
            <dd data-testid="partner-usage-cost">
              {row.cost_usd === null ? "Not priced" : usd.format(row.cost_usd)}
              {row.unpriced_tasks > 0 && ` · ${nf.format(row.unpriced_tasks)} task(s) on a model without a rate here, tokens only`}
            </dd>
          </dl>
          <ul className="space-y-0.5 text-xs" data-testid="partner-usage-models">
            {row.models.map((m) => (
              <li key={m.model_id} className="break-words">
                <span className="font-medium">{m.model_id}</span>: {nf.format(m.tasks)} task(s),{" "}
                {nf.format(m.input_tokens + m.output_tokens + m.cache_read_tokens + m.cache_write_tokens)} tokens
                {m.cost_usd === null ? " (no rate here)" : ` · ${usd.format(m.cost_usd)}`}
              </li>
            ))}
          </ul>
          <p className="text-xs text-muted-foreground">
            Token counts are what the peer&apos;s engine reported in its signed answers; they are not verified.
          </p>
        </div>
      )}

      {row && row.declared_by_peer.length > 0 && (
        <p className="mt-2 text-xs text-muted-foreground" data-testid="partner-usage-declared">
          Declared by peer (not verified, not included above):{" "}
          {row.declared_by_peer.map((d) => `${d.agent_id} ${usd.format(d.cost_per_task_usd)} per task`).join(", ")}
        </p>
      )}
    </section>
  );
}
