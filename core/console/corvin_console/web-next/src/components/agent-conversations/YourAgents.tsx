/**
 * "Your agents" — register this installation's Claude Code agent and choose whether paired peers may use it.
 *
 * Why this exists: `/ask @mine`, `/ask @peer` and `/talk` need a registered agent here, and a peer needs one the
 * operator SHARED. The console used to be able to LIST local agents and nothing else, so a freshly installed
 * instance answered "this peer offers no federable agent — the peer must mark one" with no way to do so short of a
 * hand-written API call. Registering is one click; sharing is a separate, explicit switch (never a side effect).
 * Backend: routes/federation_routes.py `/default-agent`, `PATCH /agents/{id}`.
 */
import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, Loader2, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { listLocalAgents, registerDefaultAgent, setAgentFederable, type LocalAgent } from "@/lib/api/federation";

/** Only this engine's agents can be offered to peers (the wire side lists no other). */
export const SHAREABLE_ENGINE = "claude_code";

export function YourAgents({ csrf }: { csrf: string }) {
  const qc = useQueryClient();
  const agents = useQuery({ queryKey: ["federation", "agents"], queryFn: listLocalAgents });
  const [error, setError] = React.useState("");
  const done = () => { setError(""); void qc.invalidateQueries({ queryKey: ["federation", "agents"] }); };
  const fail = (e: unknown) => setError(e instanceof Error ? e.message : "The change could not be saved.");
  const register = useMutation({ mutationFn: () => registerDefaultAgent(csrf), onSuccess: done, onError: fail });
  const share = useMutation({
    mutationFn: (v: { id: string; on: boolean }) => setAgentFederable(v.id, v.on, csrf),
    onSuccess: done, onError: fail,
  });
  const list: LocalAgent[] = agents.data?.agents ?? [];

  return (
    <section aria-label="Your agents" data-testid="your-agents" className="rounded-md border p-3 text-sm">
      <div className="flex items-center gap-2">
        <Bot className="h-4 w-4" aria-hidden />
        <h2 className="font-medium">Your agents</h2>
      </div>
      <p className="mt-1 text-xs text-muted-foreground">
        Agents the slash commands and conversations can use. A paired peer can run tasks on an agent only after you
        turn on sharing for it.
      </p>

      {agents.isError && (
        <p role="alert" className="mt-2 text-xs text-destructive">Could not load your agents.</p>
      )}

      {agents.isSuccess && list.length === 0 && (
        <div className="mt-2 space-y-2" data-testid="your-agents-empty">
          <p className="text-xs">No agent is registered yet, so <code>/ask</code> and <code>/talk</code> have nobody to use.</p>
          <Button size="sm" onClick={() => register.mutate()} disabled={register.isPending}
                  data-testid="register-default-agent">
            {register.isPending ? <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" aria-hidden />
                                : <Plus className="mr-1 h-3.5 w-3.5" aria-hidden />}
            Register Claude Code agent
          </Button>
        </div>
      )}

      {list.length > 0 && (
        <ul className="mt-2 space-y-2" data-testid="your-agents-list">
          {list.map((a) => {
            const shareable = a.engine_type === SHAREABLE_ENGINE;
            const pending = share.isPending && share.variables?.id === a.agent_id;
            return (
              <li key={a.agent_id} data-testid="your-agent" data-agent={a.agent_id}
                  className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded border px-2 py-1.5">
                <div className="min-w-0 flex-1">
                  <div className="break-all font-medium">{a.agent_id}</div>
                  <div className="break-words text-xs text-muted-foreground">
                    {a.engine_type} · {a.model}
                  </div>
                </div>
                <label className="flex shrink-0 items-center gap-2 text-xs">
                  <span className={shareable ? "" : "text-muted-foreground"}>
                    {shareable ? "Share with paired peers" : "Not shareable"}
                  </span>
                  <Switch
                    checked={a.federable && shareable}
                    disabled={!shareable || pending}
                    onCheckedChange={(on) => share.mutate({ id: a.agent_id, on })}
                    aria-label={`Share ${a.agent_id} with paired peers`}
                    title={shareable ? "Paired peers can run tasks on this agent while this is on"
                                     : "Only Claude Code agents can be shared with peers"}
                    data-testid="share-agent-switch"
                  />
                </label>
              </li>
            );
          })}
        </ul>
      )}

      {error && <p role="alert" className="mt-2 text-xs text-destructive" data-testid="your-agents-error">{error}</p>}
    </section>
  );
}
