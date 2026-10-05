/**
 * Friendship-token generation + import — sidebar section (ADR-2216).
 * Extracted from pages/chat-groups.tsx FriendshipCard for reuse inside the
 * unified chat sidebar (ChatContextSidebar.tsx).
 */
import * as React from "react";
import { KeyRound, Copy, Check, Loader2, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  createFriendshipTokenDirect, importFriendshipToken,
  type FriendshipTokenResult,
} from "@/lib/api/pending-actions";

function fmtTime(ts: number): string {
  return new Date(ts * 1000).toLocaleString();
}

export function TokenResultCard({ result }: { result: FriendshipTokenResult }) {
  const [copied, setCopied] = React.useState(false);
  return (
    <div className="rounded border border-border/60 bg-muted/30 p-3 space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium">
          {result.label ? `Token for "${result.label}"` : "New friendship token"}
        </span>
        <Button size="sm" variant="outline" className="h-6 px-2 text-[10px] gap-1"
          onClick={() => { navigator.clipboard.writeText(result.token); setCopied(true); setTimeout(() => setCopied(false), 1500); }}>
          {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />} Copy
        </Button>
      </div>
      <code className="block text-[10px] break-all bg-background rounded p-2 border border-border/40">
        {result.token}
      </code>
      <p className="text-[11px] text-muted-foreground">
        Send this code outside the console to the person you want to connect with —
        they paste it under "Accept token".
        {result.expires ? ` Valid until ${fmtTime(result.expires)}.` : " Does not expire."}
      </p>
    </div>
  );
}

export function TokensSection({ csrf, open, onOpenChange }: {
  csrf: string;
  /** Controlled expand state (a parent may open it). Omit for standalone use. */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
}) {
  const [internalExpanded, setInternalExpanded] = React.useState(false);
  const expanded = open ?? internalExpanded;
  const setExpanded = React.useCallback((v: boolean | ((prev: boolean) => boolean)) => {
    const next = typeof v === "function" ? v(expanded) : v;
    onOpenChange?.(next);
    setInternalExpanded(next);
  }, [expanded, onOpenChange]);
  const [label, setLabel] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState("");
  const [result, setResult] = React.useState<FriendshipTokenResult | null>(null);

  const [pasted, setPasted] = React.useState("");
  const [importBusy, setImportBusy] = React.useState(false);
  const [importError, setImportError] = React.useState("");
  const [importOk, setImportOk] = React.useState("");

  async function handleCreate() {
    setBusy(true); setError("");
    try {
      const res = await createFriendshipTokenDirect({ label: label.trim() || undefined }, csrf);
      setResult(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleImport() {
    setImportBusy(true); setImportError(""); setImportOk("");
    try {
      const res = await importFriendshipToken(pasted.trim(), csrf);
      setImportOk(`Connected — state: ${res.state}${res.label ? ` ("${res.label}")` : ""}.`);
      setPasted("");
    } catch (err) {
      setImportError(err instanceof Error ? err.message : String(err));
    } finally {
      setImportBusy(false);
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
          <KeyRound className="h-3.5 w-3.5" /> TOKENS
        </span>
        <span className="text-[10px]">{expanded ? "−" : "+"}</span>
      </button>
      {expanded && (
        <div className="space-y-3 px-3 pb-3">
          <div className="space-y-1.5">
            <div className="flex gap-1.5">
              <Input placeholder="Label (optional)" value={label}
                onChange={(e) => setLabel(e.target.value)} className="h-7 text-xs" />
              <Button size="sm" className="h-7 gap-1 shrink-0" disabled={busy} onClick={handleCreate}>
                {busy ? <Loader2 className="h-3 w-3 animate-spin" /> : <Plus className="h-3 w-3" />}
              </Button>
            </div>
            {error && <p className="text-[11px] text-destructive">{error}</p>}
            {result && <TokenResultCard result={result} />}
          </div>
          <div className="space-y-1.5 border-t border-border/30 pt-2">
            <p className="text-[11px] font-medium text-muted-foreground">Accept token</p>
            <div className="flex gap-1.5">
              <Input placeholder="corvin-a2a:ft1:…" value={pasted}
                onChange={(e) => setPasted(e.target.value)} className="h-7 text-[11px] font-mono" />
              <Button size="sm" variant="outline" className="h-7 shrink-0" disabled={importBusy || !pasted.trim()}
                onClick={handleImport}>
                {importBusy ? <Loader2 className="h-3 w-3 animate-spin" /> : "OK"}
              </Button>
            </div>
            {importError && <p className="text-[11px] text-destructive">{importError}</p>}
            {importOk && <p className="text-[11px] text-emerald-600 dark:text-emerald-400">{importOk}</p>}
          </div>
        </div>
      )}
    </div>
  );
}
