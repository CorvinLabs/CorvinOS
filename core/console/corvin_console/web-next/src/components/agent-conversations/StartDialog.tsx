/** "New conversation" dialog — the form that used to occupy the left column. */
import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Plus, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";
import { getA2AEndpoints } from "@/lib/api/a2a";
import {
  listLocalAgents, listPeerAgents, refreshPeerCatalog, startConversation,
} from "@/lib/api/federation";
import { DEFAULT_SETTINGS } from "@/lib/agent-conversation-view";
import { SettingsPanel } from "./SettingsPanel";

const selectCls = "h-9 w-full rounded-md border bg-background px-2 text-sm";

export function StartDialog({ csrf, onStarted }: { csrf: string; onStarted: (id: string) => void }) {
  const qc = useQueryClient();
  const [open, setOpen] = React.useState(false);
  const local = useQuery({ queryKey: ["federation", "agents"], queryFn: listLocalAgents, enabled: open });
  const peers = useQuery({ queryKey: ["federation", "peer-agents"], queryFn: listPeerAgents, enabled: open });
  const endpoints = useQuery({ queryKey: ["a2a", "endpoints"], queryFn: () => getA2AEndpoints(), enabled: open });
  const [localId, setLocalId] = React.useState("");
  const [peerKey, setPeerKey] = React.useState("");
  const [opener, setOpener] = React.useState("");
  const [maxTurns, setMaxTurns] = React.useState(6);
  const [first, setFirst] = React.useState<"local" | "peer">("local");
  const [settings, setSettings] = React.useState(DEFAULT_SETTINGS);

  const localAgents = (local.data?.agents ?? []).filter((a) => a.engine_type === "claude_code");
  const peerAgents = peers.data?.agents ?? [];
  const peer = peerAgents.find((a) => `${a.endpoint_id}\u0000${a.agent_id}` === peerKey);

  const refresh = useMutation({
    mutationFn: async () => {
      const eps = (endpoints.data?.endpoints ?? []).filter((e) => e.enabled);
      await Promise.allSettled(eps.map((e) => refreshPeerCatalog(e.endpoint_id, csrf)));
    },
    onSettled: () => qc.invalidateQueries({ queryKey: ["federation", "peer-agents"] }),
  });

  const start = useMutation({
    mutationFn: () => {
      const [endpoint_id, peer_agent_id] = peerKey.split("\u0000");
      return startConversation({ local_agent_id: localId, endpoint_id, peer_agent_id,
                                 opener: opener.trim(), max_turns: maxTurns, first_speaker: first,
                                 settings }, csrf);
    },
    onSuccess: (r) => {
      setOpener("");
      setOpen(false);
      qc.invalidateQueries({ queryKey: ["federation", "conversations"] });
      onStarted(r.conversation_id);
    },
  });
  const ready = localId && peerKey && opener.trim() && !start.isPending;

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm" variant="accent" data-testid="new-conversation">
          <Plus className="mr-1 h-4 w-4" /> New
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[90vh] max-w-xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>New conversation</DialogTitle>
          <DialogDescription>
            One of your agents talks to an agent on a paired installation. You can join at any time.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <label className="block space-y-1 text-xs text-muted-foreground">
            <span>Your agent</span>
            <select className={selectCls} value={localId} onChange={(e) => setLocalId(e.target.value)} aria-label="Your agent">
              <option value="">Select…</option>
              {localAgents.map((a) => <option key={a.agent_id} value={a.agent_id}>{a.agent_id} · {a.model}</option>)}
            </select>
            {local.isSuccess && localAgents.length === 0 && (
              <span>No Claude Code agent is registered on this installation.</span>
            )}
          </label>
          <label className="block space-y-1 text-xs text-muted-foreground">
            <span className="flex items-center justify-between">
              Peer agent
              <button type="button" className="inline-flex items-center gap-1 hover:text-foreground"
                      onClick={() => refresh.mutate()} disabled={refresh.isPending}>
                <RefreshCw className={cn("h-3 w-3", refresh.isPending && "animate-spin")} /> Refresh peers
              </button>
            </span>
            <select className={selectCls} value={peerKey} onChange={(e) => setPeerKey(e.target.value)} aria-label="Peer agent">
              <option value="">Select…</option>
              {peerAgents.map((a) => (
                <option key={a.address} value={`${a.endpoint_id}\u0000${a.agent_id}`}>
                  {a.agent_id} · {a.endpoint_id} · {a.model}
                </option>
              ))}
            </select>
            {peers.isSuccess && peerAgents.length === 0 && <span>No peer agents known yet — refresh the paired peers.</span>}
          </label>
          <label className="block space-y-1 text-xs text-muted-foreground">
            <span>Topic</span>
            <Textarea value={opener} onChange={(e) => setOpener(e.target.value)} rows={3} maxLength={2000}
                      placeholder="What should the two agents discuss?" aria-label="Topic" />
          </label>
          <div className="grid grid-cols-2 gap-3">
            <label className="block space-y-1 text-xs text-muted-foreground">
              <span>Turns (max 12)</span>
              <input type="number" min={1} max={12} value={maxTurns} className={selectCls}
                     onChange={(e) => setMaxTurns(Math.min(12, Math.max(1, Number(e.target.value) || 1)))} />
            </label>
            <label className="block space-y-1 text-xs text-muted-foreground">
              <span>Speaks first</span>
              <select className={selectCls} value={first} onChange={(e) => setFirst(e.target.value as "local" | "peer")}>
                <option value="local">Your agent</option>
                <option value="peer">Peer agent</option>
              </select>
            </label>
          </div>
          <div className="rounded-lg border p-3">
            <div className="mb-3 text-xs font-medium">Settings</div>
            <SettingsPanel value={settings} onChange={setSettings}
                           names={{ local: localId || undefined, peer: peer?.agent_id }} />
          </div>
          {start.isError && <div className="text-xs text-red-600">{(start.error as Error).message}</div>}
        </div>
        <DialogFooter>
          <Button disabled={!ready} onClick={() => start.mutate()} data-testid="start-conversation">
            {start.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
            Start conversation
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
