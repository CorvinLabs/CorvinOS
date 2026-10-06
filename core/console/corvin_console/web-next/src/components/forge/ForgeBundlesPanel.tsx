/**
 * Forge → Bundles: share Skills, Tools and Layer definitions between installs.
 *
 * Export reads each forge's own store; import only PROPOSES — every artifact
 * enters through its own forge (skills install, layers run Layer Forge's
 * gates, plugins wait for approval under Plugins, tools wait in the review
 * queue below). Nothing here can activate a tool without an explicit Accept.
 */
import { useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Download, Loader2, Upload, X } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useAuth } from "@/lib/auth";
import { ApiError } from "@/lib/api/client";
import {
  decideQuarantine, exportBundle, fetchExportable, fetchQuarantine, importBundle, validateBundle,
  type ArtifactOutcome, type ImportResult, type Selection, type ValidationResult,
} from "@/lib/api/forge-bundles";
import { UnverifiedOriginBadge } from "./UnverifiedOriginBadge";

export const MARKER_FORGE_BUNDLES = "Share forged artifacts as one bundle";

const SEMVER = /^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$/;
const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$/;
const QUARANTINE_KEY = ["forge-bundles", "quarantine"];

const STATUS_TEXT: Record<ArtifactOutcome["status"], string> = {
  installed: "Installed",
  forged: "Forged (gates passed)",
  pending_approval: "Awaiting approval under Plugins",
  quarantined: "Awaiting review below",
  failed: "Failed",
  not_attempted: "Not attempted",
};

function message(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

function selKey(s: Selection) {
  return `${s.kind}:${s.id}`;
}

function ExportSection({ csrf }: { csrf: string }) {
  const inv = useQuery({ queryKey: ["forge-bundles", "exportable"], queryFn: fetchExportable });
  const [picked, setPicked] = useState<Record<string, Selection>>({});
  const [bundleId, setBundleId] = useState("");
  const [bundleVersion, setBundleVersion] = useState("1.0.0");
  const [description, setDescription] = useState("");

  const selections = Object.values(picked);
  // Tool Forge tools carry no version; they are exported under the bundle's version.
  const resolved = selections.map((s) => (s.kind === "tool" ? { ...s, version: bundleVersion } : s));
  const valid = selections.length > 0 && SAFE_ID.test(bundleId) && SEMVER.test(bundleVersion) && description.length <= 2000;

  const run = useMutation({
    mutationFn: () => exportBundle(
      { bundle_id: bundleId, bundle_version: bundleVersion, description: description || undefined, selections: resolved },
      csrf,
    ),
    onSuccess: (blob) => {
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${bundleId}-${bundleVersion}.zip`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    },
  });

  const toggle = (s: Selection) => {
    run.reset();
    setPicked((prev) => {
      const next = { ...prev };
      if (next[selKey(s)]) delete next[selKey(s)];
      else next[selKey(s)] = s;
      return next;
    });
  };

  const row = (s: Selection, label: string, extra?: string) => (
    <label key={selKey(s)} className="flex items-center gap-2 py-1 text-sm">
      <input type="checkbox" checked={!!picked[selKey(s)]} onChange={() => toggle(s)} aria-label={`select ${s.kind} ${s.id}`} />
      <span className="font-mono">{label}</span>
      {extra && <span className="text-xs text-muted-foreground">{extra}</span>}
    </label>
  );

  return (
    <Card>
      <CardHeader>
        <CardTitle>Export</CardTitle>
        <CardDescription>
          Pick artifacts and download one ZIP. Status, call counts and audit records never travel; a
          bundle that would contain a credential-shaped string is refused.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {inv.isLoading && <Loader2 className="h-4 w-4 animate-spin" />}
        {inv.error && <p className="text-sm text-destructive">Could not load exportable artifacts: {message(inv.error)}</p>}
        {inv.data && (
          <div className="grid gap-4 md:grid-cols-3">
            <div>
              <h4 className="mb-1 text-sm font-medium">Skills</h4>
              {inv.data.skills.length === 0 && <p className="text-xs text-muted-foreground">None forged.</p>}
              {inv.data.skills.map((s) => row({ kind: "skill", id: s.id, version: s.version }, `${s.id}@${s.version}`))}
            </div>
            <div>
              <h4 className="mb-1 text-sm font-medium">Tools</h4>
              {inv.data.tools.length === 0 && <p className="text-xs text-muted-foreground">None forged.</p>}
              {inv.data.tools.map((t) => row({ kind: "tool", id: t.id, version: "" }, t.id, t.runtime))}
            </div>
            <div>
              <h4 className="mb-1 text-sm font-medium">Layers</h4>
              {inv.data.layers.length === 0 && <p className="text-xs text-muted-foreground">None forged.</p>}
              {inv.data.layers.map((l) => row({ kind: "layer", id: l.id, version: l.version }, `${l.id}@${l.version}`, l.status ?? undefined))}
            </div>
          </div>
        )}
        <p className="text-xs text-muted-foreground">
          Tools have no version of their own and are exported under the bundle version. Plugins are
          exported from the command line (scripts/forge_bundle_cli.py), which takes the built package path.
        </p>
        <div className="grid gap-3 md:grid-cols-2">
          <div>
            <Label htmlFor="fb-id">Bundle id</Label>
            <Input id="fb-id" value={bundleId} onChange={(e) => { run.reset(); setBundleId(e.target.value); }} placeholder="acme-automation" />
          </div>
          <div>
            <Label htmlFor="fb-version">Bundle version</Label>
            <Input id="fb-version" value={bundleVersion} onChange={(e) => { run.reset(); setBundleVersion(e.target.value); }} />
          </div>
        </div>
        <div>
          <Label htmlFor="fb-desc">Description (optional)</Label>
          <Textarea id="fb-desc" value={description} maxLength={2000} onChange={(e) => { run.reset(); setDescription(e.target.value); }} />
        </div>
        {run.error && <p className="text-sm text-destructive">Export refused: {message(run.error)}</p>}
        {run.isSuccess && <p className="text-sm text-emerald-700 dark:text-emerald-300">Bundle downloaded.</p>}
        <Button onClick={() => run.mutate()} disabled={!valid || run.isPending}>
          {run.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Download className="mr-2 h-4 w-4" />}
          Export {selections.length > 0 ? `${selections.length} artifact${selections.length > 1 ? "s" : ""}` : ""}
        </Button>
      </CardContent>
    </Card>
  );
}

function partialResult(err: unknown): ImportResult | null {
  // A 503 mid-import carries what already landed (BundleImportAborted).
  if (err instanceof ApiError && err.status === 503 && err.detail && typeof err.detail === "object") {
    const d = (err.detail as { detail?: unknown }).detail;
    if (d && typeof d === "object" && Array.isArray((d as ImportResult).outcomes)) return d as ImportResult;
  }
  return null;
}

function ImportSection({ csrf }: { csrf: string }) {
  const qc = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [report, setReport] = useState<ValidationResult | null>(null);
  const [result, setResult] = useState<ImportResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"checking" | "importing" | null>(null);
  // Every pick starts a new generation; a late answer for an older file is dropped.
  const generation = useRef(0);

  const refresh = () => {
    void qc.invalidateQueries({ queryKey: QUARANTINE_KEY });
    void qc.invalidateQueries({ queryKey: ["forge-bundles", "exportable"] });
  };

  const pick = async (f: File | null) => {
    const gen = ++generation.current;
    setFile(f);
    setReport(null);
    setResult(null);
    setError(null);
    if (!f) { setBusy(null); return; }
    setBusy("checking");
    try {
      const r = await validateBundle(f, csrf);
      if (gen === generation.current) setReport(r);
    } catch (err) {
      if (gen === generation.current) setError(`Could not check the bundle: ${message(err)}`);
    } finally {
      if (gen === generation.current) setBusy(null);
    }
  };

  const runImport = async () => {
    if (!file) return;
    const gen = generation.current;
    setBusy("importing");
    setError(null);
    try {
      const r = await importBundle(file, csrf);
      if (gen === generation.current) setResult(r);
    } catch (err) {
      if (gen === generation.current) {
        const partial = partialResult(err);
        if (partial) setResult(partial);
        setError(`Import ${partial ? "stopped" : "refused"}: ${message(err)}`);
      }
    } finally {
      refresh();
      if (gen === generation.current) setBusy(null);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Import</CardTitle>
        <CardDescription>
          Upload a bundle to see what it contains before anything is written. Importing proposes each
          artifact to its own forge — it never activates a tool.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <Input type="file" accept=".zip,application/zip" aria-label="bundle file" disabled={busy === "importing"}
               onChange={(e) => void pick(e.target.files?.[0] ?? null)} />
        {busy === "checking" && <Loader2 className="h-4 w-4 animate-spin" aria-label="checking" />}
        {error && <p className="text-sm text-destructive">{error}</p>}
        {report && report.valid === false && (
          <p className="text-sm text-destructive">Rejected at stage “{report.stage}”: {report.reason}</p>
        )}
        {report && report.valid && !result && (
          <div className="space-y-2">
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <span className="font-mono">{report.bundle_id}@{report.bundle_version}</span>
              <UnverifiedOriginBadge />
            </div>
            {report.description && <p className="text-sm text-muted-foreground">{report.description}</p>}
            <ul className="text-sm">
              {report.artifacts.map((a) => (
                <li key={`${a.kind}:${a.id}`} className="font-mono">
                  <Badge variant="outline" className="mr-2">{a.kind}</Badge>{a.id}@{a.version}
                </li>
              ))}
            </ul>
            <Button onClick={() => void runImport()} disabled={busy !== null}>
              {busy === "importing" ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Upload className="mr-2 h-4 w-4" />}
              Import {report.artifacts.length} artifact{report.artifacts.length === 1 ? "" : "s"}
            </Button>
          </div>
        )}
        {result && (
          <div className="space-y-1">
            <p className="text-sm">
              {result.bundle_id}@{result.bundle_version}:{" "}
              {result.outcomes.filter((o) => o.status !== "failed" && o.status !== "not_attempted").length} of{" "}
              {result.artifact_count} artifacts proposed.
            </p>
            <ul className="text-sm">
              {result.outcomes.map((o) => (
                <li key={`${o.kind}:${o.id}`} className="flex flex-wrap items-center gap-2">
                  <Badge variant={o.status === "failed" ? "danger" : o.status === "not_attempted" ? "warn" : "ok"}>
                    {STATUS_TEXT[o.status]}
                  </Badge>
                  <span className="font-mono">{o.kind} {o.id}@{o.version}</span>
                  {(o.status === "failed" || (o.status === "forged" && o.detail.includes("("))) && (
                    <span className="text-xs text-muted-foreground">{o.detail}</span>
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function ReviewSection({ csrf }: { csrf: string }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: QUARANTINE_KEY, queryFn: fetchQuarantine });
  const decide = useMutation({
    mutationFn: ({ qid, action }: { qid: string; action: "accept" | "reject" }) => decideQuarantine(qid, action, csrf),
    onSettled: () => void qc.invalidateQueries({ queryKey: QUARANTINE_KEY }),
  });
  const items = useMemo(() => q.data?.items ?? [], [q.data]);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Imported tools awaiting review</CardTitle>
        <CardDescription>
          An imported tool is foreign code. It cannot be called until you accept it here; rejecting deletes it.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-2">
        {q.isLoading && <Loader2 className="h-4 w-4 animate-spin" />}
        {q.error && <p className="text-sm text-destructive">Could not load the review queue: {message(q.error)}</p>}
        {q.data && items.length === 0 && <p className="text-sm text-muted-foreground">Nothing awaiting review.</p>}
        {decide.error && <p className="text-sm text-destructive">{message(decide.error)}</p>}
        {items.map((it) => (
          <div key={it.quarantine_id} className="flex flex-wrap items-center gap-2 rounded border border-border p-2 text-sm">
            <span className="font-mono">{it.tool_id}</span>
            <Badge variant="outline">{it.runtime}</Badge>
            <span className="text-xs text-muted-foreground">from {it.bundle_id}@{it.bundle_version}</span>
            <UnverifiedOriginBadge />
            {it.requirements.length > 0 && (
              <span className="w-full text-xs text-amber-700 dark:text-amber-300">
                Installs packages: {it.requirements.join(", ")}
              </span>
            )}
            {it.secrets.length > 0 && (
              <span className="w-full text-xs text-amber-700 dark:text-amber-300">
                Requests secrets: {it.secrets.join(", ")}
              </span>
            )}
            <span className="ml-auto flex gap-2">
              <Button size="sm" variant="outline" disabled={decide.isPending}
                      onClick={() => decide.mutate({ qid: it.quarantine_id, action: "reject" })}>
                <X className="mr-1 h-3 w-3" />Reject
              </Button>
              <Button size="sm" disabled={decide.isPending}
                      onClick={() => decide.mutate({ qid: it.quarantine_id, action: "accept" })}>
                <Check className="mr-1 h-3 w-3" />Accept
              </Button>
            </span>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

export default function ForgeBundlesPanel() {
  const { session } = useAuth();
  const csrf = session?.csrf_token ?? "";
  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">{MARKER_FORGE_BUNDLES}.</p>
      <ExportSection csrf={csrf} />
      <ImportSection csrf={csrf} />
      <ReviewSection csrf={csrf} />
    </div>
  );
}
