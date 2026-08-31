import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronRight, Network, Sparkles, Users, Workflow } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ReauthDialog } from "@/components/reauth-dialog";
import { Link } from "react-router-dom";
import { useAuth } from "@/lib/auth";
import { listPersonas, listChatSettings, patchChatSettings } from "@/lib/api";

export function CoworkPage() {
  const { session } = useAuth();
  const qc = useQueryClient();

  const personas = useQuery({
    queryKey: ["personas", "list"],
    queryFn: ({ signal }) => listPersonas(signal),
  });

  const chatSettings = useQuery({
    queryKey: ["chat-settings"],
    queryFn: ({ signal }) => listChatSettings(signal),
  });

  const allPersonaNames = React.useMemo(() => {
    const names = personas.data?.personas.map((p) => p.name) ?? [];
    return ["(default — auto-routing)", ...names];
  }, [personas.data]);

  const [pendingPin, setPendingPin] = React.useState<{
    channel: string;
    chatKey: string;
    persona: string | null;
  } | null>(null);
  const [reauthOpen, setReauthOpen] = React.useState(false);
  const [saveError, setSaveError] = React.useState<string | null>(null);

  const doPin = async () => {
    if (!pendingPin || !session) return;
    setSaveError(null);
    try {
      await patchChatSettings(
        pendingPin.channel,
        pendingPin.chatKey,
        { persona: pendingPin.persona },
        session.csrf_token,
      );
      qc.invalidateQueries({ queryKey: ["chat-settings"] });
      setPendingPin(null);
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : String(e));
    }
  };

  const handlePersonaChange = (channel: string, chatKey: string, value: string) => {
    const persona = value === "(default — auto-routing)" ? null : value;
    setPendingPin({ channel, chatKey, persona });
    setReauthOpen(true);
  };

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <header>
        <h1 className="font-serif text-3xl font-light tracking-tight">Auto-routing</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Configure how Corvin automatically selects a persona for each conversation.
        </p>
      </header>

      {/* Routing modes — info only, env-driven */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <Workflow className="h-4 w-4 text-accent" />
            <CardTitle className="text-base">Routing modes</CardTitle>
          </div>
          <CardDescription>
            Active mode set via <span className="font-mono">ADAPTER_ROUTING_MODE</span> environment
            variable. Per-chat persona pinning below overrides routing for individual chats.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-2 md:grid-cols-3">
          <ModeCard label="off" description="No auto-routing. Default persona used as-is." />
          <ModeCard label="heuristic" description="Keyword patterns, 0 ms, no API. Production default." />
          <ModeCard label="auto" description="Anthropic SDK + heuristic fallback. Needs ANTHROPIC_API_KEY." />
        </CardContent>
      </Card>

      {/* Per-chat persona pinning — editable */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <Users className="h-4 w-4 text-accent" />
            <CardTitle className="text-base">Per-chat persona pinning</CardTitle>
          </div>
          <CardDescription>
            Pin a specific persona to any chat. Overrides auto-routing for that chat.
            Requires owner re-authentication to change.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {chatSettings.isLoading && <Skeleton className="h-24 w-full" />}
          {chatSettings.data && chatSettings.data.chats.length === 0 && (
            <p className="text-sm text-muted-foreground">
              No chats known yet. Send a message from any bridge to register a chat.
            </p>
          )}
          {chatSettings.data && chatSettings.data.chats.length > 0 && (
            <div className="space-y-2">
              {chatSettings.data.chats.map((chat) => (
                <div
                  key={`${chat.channel}:${chat.chat_key}`}
                  className="flex items-center justify-between gap-3 rounded-md border border-border px-3 py-2.5"
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <Badge variant="outline" className="font-mono text-[10px] shrink-0">
                        {chat.channel}
                      </Badge>
                      <span className="font-mono text-xs truncate text-muted-foreground">
                        {chat.chat_key}
                      </span>
                    </div>
                    {chat.persona && (
                      <div className="mt-0.5 text-[11px] text-accent">
                        Pinned: <span className="font-mono">{chat.persona}</span>
                      </div>
                    )}
                  </div>
                  <select
                    className="shrink-0 rounded-md border border-border bg-background px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-accent"
                    value={chat.persona ?? "(default — auto-routing)"}
                    onChange={(e) => handlePersonaChange(chat.channel, chat.chat_key, e.target.value)}
                  >
                    {allPersonaNames.map((n) => (
                      <option key={n} value={n}>{n}</option>
                    ))}
                  </select>
                </div>
              ))}
            </div>
          )}
          {saveError && (
            <p className="mt-2 text-xs text-destructive">{saveError}</p>
          )}
        </CardContent>
      </Card>

      {/* Personas in rotation */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-accent" />
            <CardTitle className="text-base">
              Personas in rotation ({(personas.data?.personas.length ?? 0)})
            </CardTitle>
          </div>
          <CardDescription>
            Personas with <span className="font-mono">routing_anchors</span> participate in auto-routing.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {personas.isLoading && <Skeleton className="h-32 w-full" />}
          {personas.data && (
            <div className="grid grid-cols-2 gap-1 sm:grid-cols-3">
              {personas.data.personas.map((p) => (
                <Link
                  key={p.path}
                  to={`/app/personas/${p.name}`}
                  className="flex items-center justify-between rounded-md px-2 py-1.5 text-sm hover:bg-muted/40 transition-colors"
                >
                  <span className="flex items-center gap-2 min-w-0">
                    <Network className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                    <span className="truncate capitalize">{p.name.replace(/-/g, " ")}</span>
                  </span>
                  <Badge variant={p.source === "user" ? "ok" : "outline"} className="text-[10px] shrink-0 ml-1">
                    {p.source}
                  </Badge>
                </Link>
              ))}
            </div>
          )}
          <div className="mt-3 flex gap-2">
            <Button asChild variant="outline" size="sm">
              <Link to="/app/personas">
                Manage personas <ChevronRight className="h-3 w-3" />
              </Link>
            </Button>
          </div>
        </CardContent>
      </Card>

      <ReauthDialog
        open={reauthOpen}
        onOpenChange={(v) => { if (!v) { setReauthOpen(false); setPendingPin(null); } else setReauthOpen(v); }}
        title={pendingPin
          ? `Pin persona to ${pendingPin.channel}:${pendingPin.chatKey}`
          : "Confirm"}
        description={`Changing persona pinning requires owner re-authentication.`}
        onConfirm={doPin}
      />
    </div>
  );
}

function ModeCard({ label, description }: { label: string; description: string }) {
  return (
    <div className="rounded-md border border-border/60 bg-card/60 p-3">
      <div className="mb-1 font-mono text-xs text-accent">{label}</div>
      <p className="text-xs leading-relaxed text-muted-foreground">{description}</p>
    </div>
  );
}
