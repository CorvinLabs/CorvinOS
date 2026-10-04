/**
 * Group member management — sidebar section (ADR-2216).
 * Extracted from pages/chat-groups.tsx GroupDetail's participant chips +
 * AddParticipantDialog, restyled for the sidebar (dense list instead of chips).
 */
import * as React from "react";
import {
  Users, UserPlus, Trash2, Bot, User as UserIcon, Globe2,
  Loader2, AlertTriangle,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogTrigger,
} from "@/components/ui/dialog";
import { Select, SelectItem } from "@/components/ui/select";
import {
  addParticipant, removeParticipant,
  type ChatGroup, type ParticipantKind,
} from "@/lib/api/chat-groups";

const KIND_ICON: Record<ParticipantKind, React.ComponentType<{ className?: string }>> = {
  human: UserIcon, agent: Bot, a2a_peer: Globe2,
};
const KIND_LABEL: Record<ParticipantKind, string> = {
  human: "human", agent: "agent", a2a_peer: "A2A peer",
};

function AddParticipantDialog({
  groupId, csrf, onAdded,
}: { groupId: string; csrf: string; onAdded: () => void }) {
  const [open, setOpen] = React.useState(false);
  const [kind, setKind] = React.useState<ParticipantKind>("human");
  const [participantId, setParticipantId] = React.useState("");
  const [displayName, setDisplayName] = React.useState("");
  const [peerEndpointId, setPeerEndpointId] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState("");

  async function handleAdd() {
    setBusy(true); setError("");
    try {
      await addParticipant(groupId, {
        participant_id: participantId.trim(),
        kind,
        display_name: displayName.trim() || undefined,
        peer_endpoint_id: kind === "a2a_peer" ? peerEndpointId.trim() : undefined,
      }, csrf);
      setOpen(false);
      setParticipantId(""); setDisplayName(""); setPeerEndpointId("");
      onAdded();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm" variant="outline" className="h-6 w-6 p-0" title="Add member" aria-label="Add member">
          <UserPlus className="h-3 w-3" />
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader><DialogTitle>Add member</DialogTitle></DialogHeader>
        <div className="space-y-3">
          <Select value={kind} onChange={(e) => setKind(e.target.value as ParticipantKind)}>
            <SelectItem value="human">Human (internal)</SelectItem>
            <SelectItem value="agent">Agent (internal)</SelectItem>
            <SelectItem value="a2a_peer">A2A peer (external agent — needs an active friendship)</SelectItem>
          </Select>
          <Input placeholder={kind === "a2a_peer" ? "Peer endpoint ID" : "Member ID"}
            value={participantId} onChange={(e) => setParticipantId(e.target.value)} />
          {kind === "a2a_peer" && (
            <Input placeholder="Peer endpoint ID (from Peers)" value={peerEndpointId}
              onChange={(e) => setPeerEndpointId(e.target.value)} />
          )}
          <Input placeholder="Display name (optional)" value={displayName}
            onChange={(e) => setDisplayName(e.target.value)} />
          {error && (
            <p className="text-xs text-destructive flex items-start gap-1">
              <AlertTriangle className="h-3 w-3 mt-0.5 shrink-0" /> {error}
            </p>
          )}
        </div>
        <DialogFooter>
          <Button disabled={busy || !participantId.trim() || (kind === "a2a_peer" && !peerEndpointId.trim())}
            onClick={handleAdd}>
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : "Add"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function MembersSection({
  group, csrf, selfId, onChanged,
}: {
  group: ChatGroup;
  csrf: string;
  selfId: string;
  onChanged: () => void;
}) {
  const [expanded, setExpanded] = React.useState(true);
  const [removeError, setRemoveError] = React.useState("");

  async function handleRemove(participantId: string) {
    setRemoveError("");
    try {
      await removeParticipant(group.group_id, participantId, csrf);
      onChanged();
    } catch (err) {
      setRemoveError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <div className="border-t border-border/40">
      <button
        className="flex w-full items-center justify-between px-3 py-2 text-xs font-semibold text-muted-foreground hover:text-foreground"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
      >
        <span className="flex items-center gap-1.5">
          <Users className="h-3.5 w-3.5" /> MEMBERS ({group.participants.length})
        </span>
        <span className="text-[10px]">{expanded ? "−" : "+"}</span>
      </button>
      {expanded && (
        <div className="space-y-1 px-3 pb-3">
          {group.participants.map((p) => {
            const Icon = KIND_ICON[p.kind];
            return (
              <div key={p.participant_id}
                className="group flex items-center justify-between gap-1.5 rounded px-1.5 py-1 text-xs hover:bg-muted/60">
                <span className="flex min-w-0 items-center gap-1.5">
                  <Icon className="h-3 w-3 shrink-0 text-muted-foreground" />
                  <span className="truncate font-medium">{p.display_name || p.participant_id}</span>
                  <span className="shrink-0 text-[9px] text-muted-foreground">({KIND_LABEL[p.kind]})</span>
                </span>
                {p.participant_id !== selfId && (
                  <button
                    className="shrink-0 opacity-0 text-muted-foreground hover:text-destructive group-hover:opacity-100"
                    title="Remove" aria-label="Remove member"
                    onClick={() => handleRemove(p.participant_id)}
                  >
                    <Trash2 className="h-3 w-3" />
                  </button>
                )}
              </div>
            );
          })}
          {removeError && <p className="text-[11px] text-destructive">{removeError}</p>}
          <div className="pt-1">
            <AddParticipantDialog groupId={group.group_id} csrf={csrf} onAdded={onChanged} />
          </div>
        </div>
      )}
    </div>
  );
}
