/**
 * Unified chat command-center admin sidebar (ADR-2216 frontend consolidation).
 *
 * Composes PendingConfirmations + MembersSection + TokensSection into the
 * left-hand admin column so a group chat is a single command-center view
 * instead of two separate pages (the old /app/chat-groups route).
 *
 * Collapsible — ChevronLeft/Right toggle, state persisted via
 * useAdminSidebarState (localStorage, not a URL param).
 */
import * as React from "react";
import { ChevronLeft, ChevronRight, ShieldCheck } from "lucide-react";
import { cn } from "@/lib/utils";
import { PendingConfirmations } from "./PendingConfirmations";
import { MembersSection } from "./MembersSection";
import { TokensSection } from "./TokensSection";
import type { ChatGroup } from "@/lib/api/chat-groups";

export function AdminSidebar({
  group, csrf, selfId, collapsed, onToggle, onGroupChanged,
}: {
  group: ChatGroup;
  csrf: string;
  selfId: string;
  collapsed: boolean;
  onToggle: () => void;
  onGroupChanged: () => void;
}) {
  if (collapsed) {
    return (
      <div className="flex w-10 shrink-0 flex-col items-center border-r border-border bg-card/60 py-2">
        <button
          onClick={onToggle}
          title="Admin-Sidebar einblenden"
          className="rounded p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground"
        >
          <ChevronRight className="h-4 w-4" />
        </button>
        <ShieldCheck className="mt-3 h-4 w-4 text-muted-foreground/50" aria-hidden />
      </div>
    );
  }

  return (
    <aside className="flex w-56 shrink-0 flex-col overflow-y-auto border-r border-border bg-card/60">
      <div className="flex items-center justify-between border-b border-border/40 px-3 py-2">
        <span className="flex items-center gap-1.5 text-xs font-semibold text-muted-foreground">
          <ShieldCheck className="h-3.5 w-3.5" /> ADMIN
        </span>
        <button
          onClick={onToggle}
          title="Admin-Sidebar ausblenden"
          className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
        >
          <ChevronLeft className="h-3.5 w-3.5" />
        </button>
      </div>

      <PendingConfirmations csrf={csrf} onChanged={onGroupChanged} />
      <MembersSection group={group} csrf={csrf} selfId={selfId} onChanged={onGroupChanged} />
      <TokensSection csrf={csrf} />

      <div className={cn("border-t border-border/40 px-3 py-2 text-[10px] leading-relaxed text-muted-foreground")}>
        {group.title} · {group.participants.length} Teilnehmer
      </div>
    </aside>
  );
}
