import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import {
  AudioLines,
  ChevronRight,
  Hash,
  Lock,
  Mail,
  MessageCircle,
  MessageSquare,
  Send,
  Shield,
  Users,
  Zap,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { getMembersDetail, listMembers, type MemberUidRecord } from "@/lib/api";
import { cn } from "@/lib/utils";

// ── Channel icons ─────────────────────────────────────────────────────────

const CHANNEL_ICON: Record<string, React.ComponentType<{ className?: string }>> = {
  discord: Zap,
  telegram: Send,
  whatsapp: MessageCircle,
  slack: Hash,
  email: Mail,
  signal: Lock,
  teams: MessageSquare,
  web: MessageSquare,
};

const CHANNEL_COLOR: Record<string, string> = {
  discord: "text-indigo-500 bg-indigo-500/10",
  telegram: "text-sky-500 bg-sky-500/10",
  whatsapp: "text-emerald-500 bg-emerald-500/10",
  slack: "text-amber-500 bg-amber-500/10",
  email: "text-slate-500 bg-slate-500/10",
  signal: "text-teal-500 bg-teal-500/10",
  teams: "text-violet-500 bg-violet-500/10",
  web: "text-accent bg-accent/10",
};

// ── Role helpers ──────────────────────────────────────────────────────────

function extractRole(uid: MemberUidRecord): string {
  if (!uid.role) return "unknown";
  const r = uid.role as Record<string, unknown>;
  return (r.bundle as string) ?? (r.role as string) ?? "member";
}

const ROLE_STYLE: Record<string, string> = {
  owner: "border-purple-500/40 bg-purple-500/10 text-purple-700 dark:text-purple-400",
  admin: "border-blue-500/40 bg-blue-500/10 text-blue-700 dark:text-blue-400",
  member: "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
  observer: "border-slate-500/40 bg-slate-500/10 text-slate-600 dark:text-slate-400",
  unknown: "border-border text-muted-foreground",
};

function RoleBadge({ role }: { role: string }) {
  return (
    <Badge className={cn("text-[10px]", ROLE_STYLE[role] ?? ROLE_STYLE.unknown)}>
      {role}
    </Badge>
  );
}

// ── Quota bar ─────────────────────────────────────────────────────────────

function QuotaBar({ quota }: { quota: Record<string, unknown> | null }) {
  if (!quota) return <span className="text-xs text-muted-foreground/50">—</span>;
  const used = (quota.messages_today as number) ?? 0;
  const limit =
    (quota.messages_per_day as number) ??
    (quota.limit as number) ??
    (quota.messages_per_hour as number) ??
    100;
  const pct = Math.min(100, (used / limit) * 100);
  const color =
    pct > 85 ? "bg-destructive" : pct > 60 ? "bg-amber-500" : "bg-emerald-500";
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-20 overflow-hidden rounded-full bg-muted">
        <div className={cn("h-full rounded-full transition-all", color)} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-[10px] text-muted-foreground tabular-nums">
        {used}/{limit}
      </span>
    </div>
  );
}

// ── Consent chip ──────────────────────────────────────────────────────────

function ConsentChip({ consent }: { consent: Record<string, unknown> | null }) {
  if (!consent) return <span className="text-xs text-muted-foreground/50">—</span>;
  const granted = (consent.granted as boolean) ?? false;
  const ttl = consent.ttl as string | undefined;
  return (
    <span
      className={cn(
        "rounded-full px-2 py-0.5 text-[10px] font-medium",
        granted
          ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
          : "bg-muted text-muted-foreground",
      )}
    >
      {granted ? (ttl ? `active until ${ttl}` : "active") : "denied"}
    </span>
  );
}

// ── Disclosure chip ───────────────────────────────────────────────────────

function DisclosureChip({ disclosure }: { disclosure: Record<string, unknown> | null }) {
  if (!disclosure) {
    return (
      <span className="rounded-full bg-amber-500/10 px-2 py-0.5 text-[10px] font-medium text-amber-600 dark:text-amber-400">
        pending
      </span>
    );
  }
  return (
    <span className="rounded-full bg-emerald-500/10 px-2 py-0.5 text-[10px] font-medium text-emerald-600 dark:text-emerald-400">
      sent
    </span>
  );
}

// ── Member row ────────────────────────────────────────────────────────────

function MemberRow({ uid }: { uid: MemberUidRecord }) {
  const role = extractRole(uid);
  const initials = uid.uid.slice(0, 2).toUpperCase();

  return (
    <div className="flex items-center gap-3 rounded-lg px-3 py-2.5 hover:bg-muted/40 transition-colors">
      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-accent/15 text-xs font-semibold text-accent">
        {initials}
      </div>
      <div className="min-w-0 flex-1">
        <div className="truncate font-mono text-sm">{uid.uid}</div>
      </div>
      <RoleBadge role={role} />
      <div className="hidden sm:block">
        <QuotaBar quota={uid.quota} />
      </div>
      <div className="hidden md:block">
        <ConsentChip consent={uid.consent} />
      </div>
      <div className="hidden lg:block">
        <DisclosureChip disclosure={uid.disclosure} />
      </div>
    </div>
  );
}

// ── Chat detail panel ─────────────────────────────────────────────────────

function ChatDetailPanel({ chatKey }: { chatKey: string }) {
  const q = useQuery({
    queryKey: ["members-detail", chatKey],
    queryFn: ({ signal }) => getMembersDetail(chatKey, signal),
    staleTime: 30_000,
  });

  return (
    <div className="flex flex-1 flex-col gap-4 overflow-y-auto">
      {q.isLoading && (
        <div className="space-y-2 p-2">
          {[1, 2, 3].map((i) => <Skeleton key={i} className="h-12 w-full rounded-lg" />)}
        </div>
      )}

      {q.data && (
        <>
          <div className="flex items-center justify-between border-b border-border pb-3">
            <div>
              <h3 className="font-medium">{q.data.chat}</h3>
              <p className="text-xs text-muted-foreground font-mono">{q.data.chat_key}</p>
            </div>
            <Badge variant="outline" className="text-xs">
              {q.data.uid_count} users
            </Badge>
          </div>

          {q.data.uids.length === 0 ? (
            <div className="flex flex-col items-center gap-3 py-10 text-center">
              <Users className="h-8 w-8 text-muted-foreground/30" />
              <p className="text-sm text-muted-foreground">No members in this chat.</p>
            </div>
          ) : (
            <>
              {/* Column headers */}
              <div className="flex items-center gap-3 px-3 text-[10px] font-medium uppercase tracking-widest text-muted-foreground/60">
                <div className="w-8 shrink-0" />
                <div className="flex-1">User ID</div>
                <div className="w-16 text-right">Role</div>
                <div className="hidden sm:block w-28">Quota</div>
                <div className="hidden md:block w-20">Consent</div>
                <div className="hidden lg:block w-20">Disclosure</div>
              </div>

              <div className="space-y-0.5">
                {q.data.uids.map((uid) => (
                  <MemberRow key={uid.uid} uid={uid} />
                ))}
              </div>

              <p className="text-xs text-muted-foreground/50 px-3 pt-2">
                Changes are made via bridge commands (e.g. <code className="font-mono">/grant</code>, <code className="font-mono">/revoke</code>).
              </p>
            </>
          )}
        </>
      )}

      {q.isError && (
        <p className="text-sm text-destructive px-3">Error loading members.</p>
      )}
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────

export function PeoplePage() {
  const chatsQ = useQuery({
    queryKey: ["members"],
    queryFn: ({ signal }) => listMembers(signal),
    staleTime: 30_000,
    refetchInterval: 60_000,
  });

  const [selected, setSelected] = React.useState<string | null>(null);

  const chats = chatsQ.data?.chats ?? [];

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <div className="flex items-end justify-between gap-4">
        <div>
          <h1 className="font-serif text-3xl font-light tracking-tight">People</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Overview of all users, roles, quotas, and GDPR consents.
          </p>
        </div>
        <Badge variant="outline" className="text-xs">
          {chatsQ.data?.count ?? "—"} Chat{chatsQ.data?.count !== 1 ? "s" : ""}
        </Badge>
      </div>

      <div className="grid gap-4 lg:grid-cols-[260px_1fr]">
        {/* Chat list */}
        <div className="flex flex-col gap-1 rounded-xl border border-border/60 bg-card/40 p-3">
          {chatsQ.isLoading && (
            <div className="space-y-2 p-2">
              {[1, 2, 3].map((i) => <Skeleton key={i} className="h-12 w-full rounded-lg" />)}
            </div>
          )}

          {chats.length === 0 && !chatsQ.isLoading && (
            <div className="flex flex-col items-center gap-3 py-12 text-center">
              <Users className="h-10 w-10 text-muted-foreground/20" />
              <div>
                <p className="text-sm font-medium text-muted-foreground">No chats</p>
                <p className="text-xs text-muted-foreground/60 mt-0.5">
                  Once users write via a bridge, they will appear here.
                </p>
              </div>
            </div>
          )}

          {chats.map((chat) => {
            const Icon = CHANNEL_ICON[chat.channel] ?? AudioLines;
            const colorCls = CHANNEL_COLOR[chat.channel] ?? "text-accent bg-accent/10";
            const isSelected = selected === chat.chat_key;
            return (
              <button
                key={chat.chat_key}
                onClick={() => setSelected(chat.chat_key)}
                className={cn(
                  "flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left transition-colors",
                  isSelected ? "bg-accent/15 ring-1 ring-accent/30" : "hover:bg-muted/60",
                )}
              >
                <div className={cn("flex h-8 w-8 shrink-0 items-center justify-center rounded-lg", colorCls)}>
                  <Icon className="h-4 w-4" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium">{chat.chat}</div>
                  <div className="text-[11px] text-muted-foreground">{chat.members} users · {chat.channel}</div>
                </div>
                <ChevronRight className={cn(
                  "h-4 w-4 shrink-0 text-muted-foreground/40 transition-transform",
                  isSelected && "rotate-90 text-accent",
                )} />
              </button>
            );
          })}
        </div>

        {/* Detail panel */}
        <div className="flex min-h-[400px] rounded-xl border border-border/60 bg-card/40 p-5">
          {selected ? (
            <ChatDetailPanel chatKey={selected} />
          ) : (
            <div className="flex flex-1 flex-col items-center justify-center gap-3 text-sm text-muted-foreground">
              <Shield className="h-10 w-10 opacity-20" />
              <p>Select a chat to view members.</p>
              {chats.length > 0 && (
                <p className="text-xs opacity-60">{chats.length} chat{chats.length !== 1 ? "s" : ""}</p>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
