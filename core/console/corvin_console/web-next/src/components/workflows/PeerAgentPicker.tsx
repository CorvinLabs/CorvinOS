/**
 * Peer picker of the `peer_agent` workflow node: choose one agent of a paired peer installation.
 * Reads the paired peers and their fresh catalogs; shows an empty state when none exists and a
 * "catalog stale" row (with a refresh) for a peer whose catalog is older than its freshness window.
 */
import * as React from "react";
import { Loader2 } from "lucide-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import { listFederationPeers, listPeerAgents, refreshPeerCatalog } from "@/lib/api/federation";
import { choiceValue, currentIsOffered, peerChoices, splitChoice, stalePeers } from "@/lib/peer-agent-choices";

export function PeerAgentPicker({
  endpointId,
  agentId,
  csrf,
  onChange,
}: {
  endpointId: string;
  agentId: string;
  csrf: string;
  onChange: (endpointId: string, agentId: string) => void;
}) {
  const qc = useQueryClient();
  const peers = useQuery({ queryKey: ["federation", "peers"], queryFn: () => listFederationPeers() });
  const agents = useQuery({ queryKey: ["federation", "peer-agents"], queryFn: () => listPeerAgents() });
  const [refreshing, setRefreshing] = React.useState<string | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  if (peers.isLoading || agents.isLoading) {
    return (
      <div data-testid="peer-agent-picker" className="text-xs text-muted-foreground">
        <Loader2 className="mr-1 inline h-3 w-3 animate-spin" /> Loading paired peers…
      </div>
    );
  }
  const peerList = peers.data?.peers ?? [];
  const choices = peerChoices(agents.data?.agents ?? []);
  const stale = stalePeers(peerList);
  const offered = currentIsOffered(choices, endpointId, agentId);
  const current = endpointId && agentId ? choiceValue(endpointId, agentId) : "";

  const refresh = async (id: string) => {
    setRefreshing(id);
    setError(null);
    try {
      await refreshPeerCatalog(id, csrf);
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["federation", "peers"] }),
        qc.invalidateQueries({ queryKey: ["federation", "peer-agents"] }),
      ]);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRefreshing(null);
    }
  };

  return (
    <div className="space-y-2" data-testid="peer-agent-picker">
      <div className="text-[10px] font-medium uppercase tracking-widest text-muted-foreground">
        peer agent <span className="text-destructive">*</span>
      </div>
      {peerList.length === 0 && choices.length === 0 ? (
        <p
          className="rounded-md border border-border bg-muted/30 px-2 py-1.5 text-xs text-muted-foreground"
          data-testid="peer-agent-empty"
        >
          No peer is paired with this installation yet, so there is no agent to delegate to. Pair a peer
          and refresh its catalog first.
        </p>
      ) : (
        <Select
          aria-label="peer agent"
          data-testid="peer-agent-select"
          className="h-8 text-xs"
          value={current}
          onChange={(e) => {
            const picked = splitChoice(e.target.value);
            if (picked) onChange(picked.endpoint_id, picked.agent_id);
          }}
        >
          {!current && <option value="">Select a peer agent…</option>}
          {!offered && (
            <option value={current}>{`${agentId} @ ${endpointId} (not in a fresh catalog)`}</option>
          )}
          {choices.map((c) => (
            <option key={c.value} value={c.value}>{c.label}</option>
          ))}
        </Select>
      )}
      {!offered && (
        <p className="text-[11px] text-amber-600 dark:text-amber-400" data-testid="peer-agent-not-offered">
          The chosen agent is not offered by a fresh peer catalog. The node can still be saved, but a run
          fails if the peer is unpaired.
        </p>
      )}
      {stale.map((p) => (
        <div key={p.endpoint_id} className="flex items-center gap-2 text-[11px]" data-testid={`peer-stale-${p.endpoint_id}`}>
          <span className="text-muted-foreground">
            Catalog of <code className="font-mono">{p.endpoint_id}</code> is stale.
          </span>
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-6 text-[11px]"
            disabled={refreshing !== null}
            onClick={() => void refresh(p.endpoint_id)}
          >
            {refreshing === p.endpoint_id && <Loader2 className="h-3 w-3 animate-spin" />}
            Refresh
          </Button>
        </div>
      ))}
      {error && <p className="text-[11px] text-destructive" data-testid="peer-agent-error">{error}</p>}
    </div>
  );
}
