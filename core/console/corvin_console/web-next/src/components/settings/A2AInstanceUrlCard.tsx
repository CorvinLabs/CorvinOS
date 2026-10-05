/**
 * A2A instance URL — the address this install's A2A receiver answers on
 * (/v1/a2a/receive), shown and editable so an operator can share it when
 * creating a friendship token. Moved out of agent-hub.tsx (Console
 * navigation refactor, Phase 3): this is instance configuration, not a
 * chat-sidebar concern — the Tokens section in the chat sidebar creates
 * tokens with no URL field at all, because the backend
 * (/remote-trigger/pair/friendship/create) already falls back to this
 * saved value (then to auto-detection) when none is supplied.
 */
import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, Globe2, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { useAuth } from "@/lib/auth";
import { getMyA2AUrl, setMyA2AUrl } from "@/lib/api/a2a";

export function A2AInstanceUrlCard() {
  const { session } = useAuth();
  const csrf = session?.csrf_token ?? "";
  const qc = useQueryClient();

  const myUrl = useQuery({
    queryKey: ["a2a", "my-url"],
    queryFn: ({ signal }) => getMyA2AUrl(signal),
    staleTime: 60_000,
  });

  const [editing, setEditing] = React.useState(false);
  const [urlInput, setUrlInput] = React.useState("");
  const [saving, setSaving] = React.useState(false);
  const [copied, setCopied] = React.useState(false);

  const url = myUrl.data?.url ?? null;
  const suggested = myUrl.data?.suggested ?? null;

  function isPrivateUrl(u: string | null): boolean {
    if (!u) return false;
    return /^https?:\/\/(localhost|127\.|::1|10\.|172\.(1[6-9]|2\d|3[01])\.|192\.168\.)/i.test(u);
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    try {
      await setMyA2AUrl(urlInput.trim(), csrf);
      void qc.invalidateQueries({ queryKey: ["a2a", "my-url"] });
      setEditing(false);
    } finally {
      setSaving(false);
    }
  }

  async function handleAcceptSuggested() {
    if (!suggested) return;
    setSaving(true);
    try {
      await setMyA2AUrl(suggested, csrf);
      void qc.invalidateQueries({ queryKey: ["a2a", "my-url"] });
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="rounded-md border border-border bg-muted/30 px-4 py-3">
      <div className="flex items-center gap-2 mb-1.5">
        <Globe2 className="h-3.5 w-3.5 text-muted-foreground" />
        <span className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
          My A2A URL
        </span>
      </div>

      {!editing ? (
        <div className="flex items-center gap-2 flex-wrap">
          {url ? (
            <>
              <code className="text-sm font-mono flex-1 truncate min-w-0">{url}</code>
              <Button size="sm" variant="ghost" className="h-7 px-2 gap-1 text-xs shrink-0"
                onClick={() => {
                  void navigator.clipboard.writeText(url).then(() => {
                    setCopied(true);
                    setTimeout(() => setCopied(false), 2000);
                  });
                }}>
                <Copy className="h-3 w-3" />
                {copied ? "Copied!" : "Copy"}
              </Button>
              <Button size="sm" variant="ghost" className="h-7 px-2 text-xs shrink-0"
                onClick={() => { setUrlInput(url); setEditing(true); }}>
                Edit
              </Button>
            </>
          ) : (
            <div className="flex-1 space-y-2">
              {suggested ? (
                <>
                  <div className={cn(
                    "flex items-center gap-2 rounded-md border px-3 py-2",
                    isPrivateUrl(suggested)
                      ? "border-amber-500/40 bg-amber-500/5"
                      : "border-accent/30 bg-accent/5"
                  )}>
                    <div className="flex-1 min-w-0">
                      <p className="text-[11px] text-muted-foreground mb-0.5">
                        Detected IP of this instance:
                      </p>
                      <code className="text-sm font-mono">{suggested}</code>
                      {isPrivateUrl(suggested) && (
                        <p className="text-[11px] text-amber-700 dark:text-amber-400 mt-1">
                          ⓘ Local network address — works for pairing with
                          other devices on this same Wi-Fi/network. For a
                          peer outside this network, use a VPN (e.g.
                          Tailscale) or a public domain instead.
                        </p>
                      )}
                    </div>
                    <Button size="sm" disabled={saving} className="h-7 px-3 text-xs shrink-0"
                      onClick={handleAcceptSuggested}>
                      {saving ? <Loader2 className="h-3 w-3 animate-spin" /> : "Use this URL"}
                    </Button>
                  </div>
                  <Button size="sm" variant="ghost" className="h-6 px-2 text-xs text-muted-foreground"
                    onClick={() => { setUrlInput(suggested); setEditing(true); }}>
                    Enter a different URL…
                  </Button>
                </>
              ) : (
                <Button size="sm" variant="outline" className="h-7 px-3 text-xs"
                  onClick={() => { setUrlInput(""); setEditing(true); }}>
                  Enter URL
                </Button>
              )}
            </div>
          )}
        </div>
      ) : (
        <form onSubmit={handleSave} className="flex gap-2 mt-1">
          <Input
            autoFocus
            placeholder="https://my-corvin.example.com"
            value={urlInput}
            onChange={e => setUrlInput(e.target.value)}
            className="h-8 text-sm flex-1"
            required
          />
          <Button type="submit" size="sm" disabled={saving} className="h-8 px-3 shrink-0">
            {saving ? <Loader2 className="h-3 w-3 animate-spin" /> : "Save"}
          </Button>
          <Button type="button" size="sm" variant="ghost" className="h-8 px-3 shrink-0"
            onClick={() => setEditing(false)}>
            ✕
          </Button>
        </form>
      )}

      <p className="mt-2 text-[11px] text-muted-foreground">
        The A2A receiver runs on the same server as this console
        (<code className="rounded bg-muted px-1">/v1/a2a/receive</code>).
        Share this URL — the peer enters it when importing the token.
      </p>
    </div>
  );
}
