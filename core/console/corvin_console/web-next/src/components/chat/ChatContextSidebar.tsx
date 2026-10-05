/**
 * The chat's right-hand sidebar: one switchable panel for every conversation
 * the main area can show.
 *
 *   Chats  — operator ↔ CorvinOS sessions
 *   Peers  — group chats, connected A2A agents, friendship tokens
 *   A2A    — confirmations a chat turn staged + recent agent traffic
 *
 * Picking an entry navigates; the main area renders whatever the URL names
 * (/app/chat/<sid>, /app/chat/group/<id>, /app/chat/peer/<id>).
 */
import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Activity, Globe2, Loader2, MessageSquare, Plus, Radar, Settings2, Users } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { PREF_KEYS, usePersistedString } from "@/lib/preferences";
import { createGroup, listGroups } from "@/lib/api/chat-groups";
import { getA2AFeed, listDiscoveryPeers, type A2AFeedMessage } from "@/lib/api/a2a";
import { listPendingSends, listPendingTokenRequests } from "@/lib/api/pending-actions";
import { PendingConfirmations } from "./PendingConfirmations";
import { TokensSection } from "./TokensSection";
import { PeerManagementDialog } from "./PeerManagementDialog";
import { plural } from "./GroupConversation";
import { presenceView } from "@/lib/a2a-presence";

export type SidebarMode = "chats" | "peers" | "a2a";
const MODES: SidebarMode[] = ["chats", "peers", "a2a"];

const PENDING_REFETCH_MS = 10_000;

export function usePendingCount(): number {
  const sends = useQuery({
    queryKey: ["pending", "sends"],
    queryFn: ({ signal }) => listPendingSends(signal),
    refetchInterval: PENDING_REFETCH_MS,
  });
  const tokens = useQuery({
    queryKey: ["pending", "tokens"],
    queryFn: ({ signal }) => listPendingTokenRequests(signal),
    refetchInterval: PENDING_REFETCH_MS,
  });
  return (sends.data?.length ?? 0) + (tokens.data?.length ?? 0);
}

function SectionTitle({ icon: Icon, children, action }: {
  icon: React.ComponentType<{ className?: string }>;
  children: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-between px-3 pb-1 pt-3">
      <span className="flex items-center gap-1.5 text-xs font-semibold text-muted-foreground">
        <Icon className="h-3.5 w-3.5" /> {children}
      </span>
      {action}
    </div>
  );
}

function GroupsSection({ csrf, activeGroupId }: { csrf: string; activeGroupId?: string }) {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const groups = useQuery({
    queryKey: ["chat-groups"],
    queryFn: ({ signal }) => listGroups(signal),
    refetchInterval: 15_000,
  });
  const [creating, setCreating] = React.useState(false);
  const [title, setTitle] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState("");

  async function handleCreate() {
    if (!title.trim()) return;
    setBusy(true); setError("");
    try {
      const g = await createGroup(title.trim(), csrf);
      setTitle(""); setCreating(false);
      await qc.invalidateQueries({ queryKey: ["chat-groups"] });
      navigate(`/app/chat/group/${encodeURIComponent(g.group_id)}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <SectionTitle icon={Users} action={
        <Button size="sm" variant="ghost" className="h-6 w-6 p-0" aria-label="New group"
          title="New group" onClick={() => setCreating((v) => !v)}>
          <Plus className="h-3.5 w-3.5" />
        </Button>
      }>GROUPS</SectionTitle>
      {creating && (
        <div className="space-y-1 px-3 pb-2">
          <div className="flex gap-1.5">
            <Input autoFocus placeholder="Group name" value={title} className="h-7 text-xs"
              aria-label="Group name"
              onChange={(e) => setTitle(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") handleCreate(); if (e.key === "Escape") setCreating(false); }} />
            <Button size="sm" className="h-7 shrink-0" disabled={busy || !title.trim()} onClick={handleCreate}>
              {busy ? <Loader2 className="h-3 w-3 animate-spin" /> : "Create"}
            </Button>
          </div>
          {error && <p className="text-[11px] text-destructive">{error}</p>}
        </div>
      )}
      <div className="space-y-0.5 px-2">
        {groups.isLoading && <Loader2 className="mx-auto h-4 w-4 animate-spin" />}
        {groups.data?.length === 0 && (
          <p className="px-2 py-1 text-[11px] text-muted-foreground">No groups yet.</p>
        )}
        {groups.data?.map((g) => {
          const peers = g.participants.filter((p) => p.kind === "a2a_peer").length;
          return (
            <button key={g.group_id}
              onClick={() => navigate(`/app/chat/group/${encodeURIComponent(g.group_id)}`)}
              className={cn(
                "w-full rounded-md px-2 py-1.5 text-left text-sm hover:bg-muted/60",
                activeGroupId === g.group_id && "bg-muted",
              )}>
              <div className="truncate font-medium">{g.title}</div>
              <div className="text-[10px] text-muted-foreground">
                {plural(g.participants.length, "member")}{peers ? ` · ${peers} via A2A` : ""}
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function PeersSection({ activePeerId }: { activePeerId?: string }) {
  const navigate = useNavigate();
  const feed = useQuery({
    queryKey: ["a2a", "peers", "with-former"],
    queryFn: ({ signal }) => getA2AFeed({ limit: 1, include_former: true }, signal),
    select: (r) => r.peers,
    refetchInterval: 30_000,
  });
  return (
    <div>
      <SectionTitle icon={Globe2} action={
        <PeerManagementDialog trigger={
          <Button size="sm" variant="ghost" className="h-6 w-6 p-0" aria-label="Manage peer permissions"
            title="Manage peer permissions, invite codes & connections">
            <Settings2 className="h-3.5 w-3.5" />
          </Button>
        } />
      }>CONNECTED AGENTS</SectionTitle>
      <div className="space-y-0.5 px-2">
        {feed.isLoading && <Loader2 className="mx-auto h-4 w-4 animate-spin" />}
        {feed.error && (
          <p className="px-2 py-1 text-[11px] text-muted-foreground">
            {feed.error instanceof Error ? feed.error.message : "A2A unavailable"}
          </p>
        )}
        {feed.data?.length === 0 && (
          <p className="px-2 py-1 text-[11px] text-muted-foreground">
            No connected agents. Create a token below and send it to the other side.
          </p>
        )}
        {feed.data?.map((p) => {
          const pv = presenceView(p);
          const direction = p.can_send && p.can_receive ? "two-way" : p.can_send ? "send only" : p.can_receive ? "receive only" : null;
          return (
            <button key={p.peer_id}
              onClick={() => navigate(`/app/chat/peer/${encodeURIComponent(p.peer_id)}`)}
              data-testid="peer-row" data-presence={pv.presence}
              title={pv.title}
              className={cn(
                "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-muted/60",
                activePeerId === p.peer_id && "bg-muted",
                pv.presence === "removed" && "opacity-60",
              )}>
              <span aria-hidden data-testid="presence-dot" className={cn("h-2 w-2 shrink-0 rounded-full", pv.dotClass)} />
              <span className="min-w-0 flex-1">
                <span className="block truncate font-medium">{p.label || p.peer_id}</span>
                <span className="block text-[10px] text-muted-foreground">
                  {pv.label}{direction && pv.presence !== "disabled" && pv.presence !== "removed" ? ` · ${direction}` : ""}
                </span>
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

/**
 * Discovered-but-unpaired peers (ADR: Discovery folded into the chat sidebar
 * — the standalone /app/discovery page is retired, this is now its only UI).
 * "Add" doesn't pair directly (listDiscoveryPeers has no pairing endpoint,
 * discovery is LAN/mDNS visibility only) — it opens the Tokens section,
 * which is the actual pairing mechanism (generate a friendship token, send
 * it to the peer out-of-band).
 */
function DiscoverySection({ connectedPeerIds, onAdd }: { connectedPeerIds: Set<string>; onAdd: () => void }) {
  const discovery = useQuery({
    queryKey: ["a2a", "discovery", "peers"],
    queryFn: ({ signal }) => listDiscoveryPeers(signal),
    refetchInterval: 30_000,
  });
  const unpaired = (discovery.data?.peers ?? []).filter((p) => !connectedPeerIds.has(p.peer_id));
  if (discovery.isLoading || unpaired.length === 0) return null;
  return (
    <div>
      <SectionTitle icon={Radar}>DISCOVERED</SectionTitle>
      <div className="space-y-0.5 px-2">
        {unpaired.map((p) => (
          <div key={p.peer_id}
            className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm">
            <span aria-hidden className={cn("h-2 w-2 shrink-0 rounded-full",
              p.status === "online" ? "bg-sky-500" : "bg-muted-foreground/40")} />
            <span className="min-w-0 flex-1">
              <span className="block truncate font-medium">{p.name}</span>
              <span className="block text-[10px] text-muted-foreground">{p.status}{p.region ? ` · ${p.region}` : ""}</span>
            </span>
            <Button size="sm" variant="ghost" className="h-6 px-2 text-[10px] gap-1 shrink-0" onClick={onAdd}>
              <Plus className="h-3 w-3" /> Add
            </Button>
          </div>
        ))}
      </div>
    </div>
  );
}

function ActivityRow({ m }: { m: A2AFeedMessage }) {
  const navigate = useNavigate();
  const failed = Boolean(m.error) || ["rejected", "timeout", "error"].includes(m.status);
  const preview = (m.text || (m.kind === "response" ? "reply" : "")).slice(0, 80);
  return (
    <button onClick={() => navigate(`/app/chat/peer/${encodeURIComponent(m.peer_id)}`)}
      className="w-full rounded-md px-2 py-1.5 text-left hover:bg-muted/60">
      <div className="flex items-center gap-1.5 text-[10px] text-muted-foreground">
        <span className={cn(failed ? "text-destructive" : m.direction === "in" ? "text-sky-600 dark:text-sky-400" : "")}>
          {m.direction === "in" ? "←" : "→"} {m.peer_label || m.peer_id}
        </span>
        <span className="ml-auto">{new Date(m.ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
      </div>
      <div className="truncate text-xs">{preview || <span className="text-muted-foreground">{m.status}</span>}</div>
    </button>
  );
}

function ActivitySection() {
  const feed = useQuery({
    queryKey: ["a2a", "feed", "recent"],
    queryFn: ({ signal }) => getA2AFeed({ limit: 40 }, signal),
    refetchInterval: 8_000,
  });
  const recent = [...(feed.data?.messages ?? [])].reverse();
  return (
    <div>
      <SectionTitle icon={Activity}>RECENT AGENT TRAFFIC</SectionTitle>
      <div className="space-y-0.5 px-2">
        {feed.isLoading && <Loader2 className="mx-auto h-4 w-4 animate-spin" />}
        {feed.error && (
          <p className="px-2 py-1 text-[11px] text-muted-foreground">
            {feed.error instanceof Error ? feed.error.message : "A2A unavailable"}
          </p>
        )}
        {feed.data && recent.length === 0 && (
          <p className="px-2 py-1 text-[11px] text-muted-foreground">No agent traffic yet.</p>
        )}
        {recent.map((m) => <ActivityRow key={m.id} m={m} />)}
      </div>
    </div>
  );
}

const MODE_META: Record<SidebarMode, { label: string; icon: React.ComponentType<{ className?: string }> }> = {
  chats: { label: "Chats", icon: MessageSquare },
  peers: { label: "Peers", icon: Users },
  a2a: { label: "A2A", icon: Globe2 },
};

export function ChatContextSidebar({
  csrf, sessionsPanel, sessionsAction, activeGroupId, activePeerId, initialMode,
}: {
  csrf: string;
  sessionsPanel: React.ReactNode;
  sessionsAction: React.ReactNode;
  activeGroupId?: string;
  activePeerId?: string;
  /** Mode implied by the URL (e.g. a group is open → Peers). */
  initialMode?: SidebarMode;
}) {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [stored, setStored] = usePersistedString(PREF_KEYS.chatSidebarMode, "chats");
  const mode: SidebarMode = (MODES as string[]).includes(stored) ? (stored as SidebarMode) : "chats";
  const pending = usePendingCount();
  const [tokensOpen, setTokensOpen] = React.useState(false);
  const connectedFeed = useQuery({
    queryKey: ["a2a", "peers"],
    queryFn: ({ signal }) => getA2AFeed({ limit: 1 }, signal),
    select: (r) => new Set(r.peers.map((p) => p.peer_id)),
    enabled: mode === "peers",
  });

  // Auto-navigate when clicking a tab if a group/peer is active
  const handleTabClick = React.useCallback((m: SidebarMode) => {
    setStored(m);
    if (m === "peers") {
      if (activeGroupId) {
        navigate(`/app/chat/group/${encodeURIComponent(activeGroupId)}`);
      } else if (activePeerId) {
        navigate(`/app/chat/peer/${encodeURIComponent(activePeerId)}`);
      }
    }
  }, [setStored, navigate, activeGroupId, activePeerId]);

  // Opening a group or agent while the Chats list is showing switches to the
  // panel it belongs to; from Peers or A2A the operator stays where they are.
  React.useEffect(() => {
    if (initialMode && mode === "chats") setStored(initialMode);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialMode, activeGroupId, activePeerId]);

  React.useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (!e.altKey || e.ctrlKey || e.metaKey) return;
      const idx = ["1", "2", "3"].indexOf(e.key);
      if (idx < 0) return;
      e.preventDefault();
      const m = MODES[idx];
      handleTabClick(m);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [handleTabClick]);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div role="tablist" aria-label="Sidebar" className="flex border-b border-border">
        {MODES.map((m, i) => {
          const Icon = MODE_META[m].icon;
          const selected = m === mode;
          return (
            <button key={m} role="tab" aria-selected={selected} title={`${MODE_META[m].label} (Alt+${i + 1})`}
              onClick={() => handleTabClick(m)}
              className={cn(
                "relative flex flex-1 items-center justify-center gap-1.5 py-2.5 text-xs font-medium transition-colors",
                selected ? "border-b-2 border-accent text-foreground" : "text-muted-foreground hover:text-foreground",
              )}>
              <Icon className="h-3.5 w-3.5" /> {MODE_META[m].label}
              {m === "a2a" && pending > 0 && (
                <span data-testid="pending-badge"
                  className="rounded-full bg-amber-500 px-1.5 text-[10px] font-semibold leading-4 text-white">
                  {pending}
                </span>
              )}
            </button>
          );
        })}
      </div>

      {mode === "chats" && (
        <>
          <div className="flex items-center justify-between gap-2 px-4 py-2">
            <span className="font-serif text-lg">Chats</span>
            {sessionsAction}
          </div>
          <div className="flex-1 overflow-y-auto px-2 pb-2">{sessionsPanel}</div>
        </>
      )}

      {mode === "peers" && (
        <div className="flex-1 overflow-y-auto pb-2" role="tabpanel" aria-label="Peers">
          <GroupsSection csrf={csrf} activeGroupId={activeGroupId} />
          <PeersSection activePeerId={activePeerId} />
          <DiscoverySection connectedPeerIds={connectedFeed.data ?? new Set()} onAdd={() => setTokensOpen(true)} />
          <div className="mt-3">
            <TokensSection csrf={csrf} open={tokensOpen} onOpenChange={setTokensOpen} />
          </div>
        </div>
      )}

      {mode === "a2a" && (
        <div className="flex-1 overflow-y-auto pb-2" role="tabpanel" aria-label="A2A">
          <PendingConfirmations csrf={csrf}
            onChanged={() => qc.invalidateQueries({ queryKey: ["a2a"] })} />
          {pending === 0 && (
            <p className="px-3 pt-3 text-[11px] text-muted-foreground">
              Nothing waiting for confirmation. When a chat turn prepares an agent message or a
              friendship token, it appears here and is only sent after you confirm.
            </p>
          )}
          <ActivitySection />
        </div>
      )}
    </div>
  );
}
