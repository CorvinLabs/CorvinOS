/**
 * Agent conversations — the console panel of the Marketplace plugin `agent_conversations`.
 * One of this installation's agents talks to an agent on a paired peer installation, and the
 * operator takes part (interjections, pause, settings) exactly like in the peer chat.
 * Backend: routes/federation_routes.py "/conversations"; moderator: core/federation/conversation.py.
 * Selection lives in the URL (`?c=<id>`) so Back/Forward and reloads keep the open conversation.
 */
import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { MessagesSquare, Search } from "lucide-react";
import { useSearchParams } from "react-router-dom";
import { useAuth } from "@/lib/auth";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { listConversations } from "@/lib/api/federation";
import { ConversationThread, StatusBadge } from "@/components/agent-conversations/ConversationThread";
import { StartDialog } from "@/components/agent-conversations/StartDialog";
import { YourAgents } from "@/components/agent-conversations/YourAgents";

const LIST_POLL_MS = 5000;

export function AgentConversationsPage() {
  const { session } = useAuth();
  const csrf = session?.csrf_token ?? "";
  const [params, setParams] = useSearchParams();
  const selected = params.get("c");
  const select = (id: string | null) =>
    setParams((p) => { const n = new URLSearchParams(p); if (id) n.set("c", id); else n.delete("c"); return n; });
  const [q, setQ] = React.useState("");
  const list = useQuery({ queryKey: ["federation", "conversations"], queryFn: listConversations,
                          refetchInterval: LIST_POLL_MS });
  const items = (list.data?.conversations ?? []).filter((s) => !s.ask).filter((s) => {
    const hay = `${s.local?.agent_id} ${s.peer?.agent_id} ${s.topic}`.toLowerCase();
    return hay.includes(q.trim().toLowerCase());
  });

  return (
    <div className="grid h-[calc(100vh-4rem)] grid-cols-1 gap-4 p-4 lg:grid-cols-[20rem_1fr]">
      <div className="flex min-h-0 flex-col gap-3">
        <div className="flex items-center gap-2">
          <MessagesSquare className="h-5 w-5" />
          <h1 className="text-lg font-semibold">Agent conversations</h1>
          <span className="ml-auto"><StartDialog csrf={csrf} onStarted={select} /></span>
        </div>
        <YourAgents csrf={csrf} />
        <label className="relative block">
          <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search conversations"
                 aria-label="Search conversations"
                 className="h-9 w-full rounded-md border bg-background pl-8 pr-2 text-sm" />
        </label>
        <div className="min-h-0 flex-1 space-y-1 overflow-y-auto" data-testid="conversation-list">
          {items.map((s) => (
            <button key={s.conversation_id} type="button" onClick={() => select(s.conversation_id)}
                    data-testid="conversation-item"
                    className={cn("w-full rounded-md border px-3 py-2 text-left text-sm hover:bg-muted",
                                  selected === s.conversation_id && "bg-muted")}>
              <div className="flex items-center gap-2">
                <span className="truncate font-medium">{s.local?.agent_id} ↔ {s.peer?.agent_id}</span>
                <span className="ml-auto"><StatusBadge status={s.status} paused={s.paused} /></span>
              </div>
              <div className="truncate text-xs text-muted-foreground">{s.topic}</div>
            </button>
          ))}
          {list.isSuccess && items.length === 0 && (
            <div className="text-xs text-muted-foreground">
              {q ? "Nothing matches." : "No conversations yet — start one with New."}
            </div>
          )}
        </div>
      </div>
      <Card className="min-h-0 overflow-hidden">
        {selected
          ? <ConversationThread key={selected} id={selected} onDeleted={() => select(null)} />
          : <div className="p-6 text-sm text-muted-foreground">Select or start a conversation.</div>}
      </Card>
    </div>
  );
}
