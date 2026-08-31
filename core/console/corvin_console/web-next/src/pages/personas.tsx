import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router-dom";
import {
  ArrowLeft,
  Copy,
  Cpu,
  Hammer,
  Loader2,
  Lock,
  Plus,
  Power,
  Save,
  Search,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { ReauthDialog } from "@/components/reauth-dialog";
import {
  copyPersonaFromBundle,
  createPersona,
  deletePersona,
  getPersona,
  getPersonaEngine,
  listPersonas,
  setPersonaDisabled,
  setPersonaEngine,
  updatePersona,
  type PersonaDetailResponse,
  type PersonaEngineConfig,
  type PersonaSummary,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";

// A minimal valid new persona — DNS-label-like name + a sensible default so the
// freshly-created file is immediately editable and engine-assignable.
function _newPersonaBody(name: string): Record<string, unknown> {
  return {
    name,
    description: "My custom persona",
    permission_mode: "bypassPermissions",
    engine: null,
    forge_enabled: false,
    skill_forge_enabled: false,
    ldd_preset: "off",
  };
}

const _NAME_RE = /^[a-z0-9][a-z0-9_-]{0,62}$/;

export function PersonasListPage() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { session } = useAuth();
  const [search, setSearch] = React.useState("");
  const [filter, setFilter] = React.useState<"all" | "bundle" | "user">("all");
  const [creating, setCreating] = React.useState(false);
  const [newName, setNewName] = React.useState("");
  const [toast, setToast] = React.useState<{ kind: "ok" | "err"; msg: string } | null>(null);

  const q = useQuery({
    queryKey: ["personas", "list"],
    queryFn: ({ signal }) => listPersonas(signal),
  });

  const refresh = () => qc.invalidateQueries({ queryKey: ["personas", "list"] });

  const createMut = useMutation({
    mutationFn: async (name: string) =>
      createPersona(name, _newPersonaBody(name), session!.csrf_token),
    onSuccess: async (_d, name) => {
      setCreating(false);
      setNewName("");
      await refresh();
      navigate(`/app/personas/${encodeURIComponent(name)}`);
    },
    onError: (e: Error) => setToast({ kind: "err", msg: e.message }),
  });

  const toggleMut = useMutation({
    mutationFn: async (v: { name: string; disabled: boolean }) =>
      setPersonaDisabled(v.name, v.disabled, session!.csrf_token),
    onSuccess: async (_d, v) => {
      await refresh();
      setToast({ kind: "ok", msg: v.disabled ? "Persona deactivated." : "Persona activated." });
    },
    onError: (e: Error) => setToast({ kind: "err", msg: e.message }),
  });

  const deleteMut = useMutation({
    mutationFn: async (name: string) => deletePersona(name, session!.csrf_token),
    onSuccess: async (d) => {
      await refresh();
      setToast({
        kind: "ok",
        msg: d.reverted_to_bundle ? "Override deleted — reverted to bundle." : "Persona deleted.",
      });
    },
    onError: (e: Error) => setToast({ kind: "err", msg: e.message }),
  });

  const existingNames = React.useMemo(
    () => new Set((q.data?.personas ?? []).map((p) => p.name)),
    [q.data],
  );
  const nameValid = _NAME_RE.test(newName) && !existingNames.has(newName);

  const items = React.useMemo<PersonaSummary[]>(() => {
    const all = q.data?.personas ?? [];
    return all.filter((p) => {
      if (filter !== "all" && p.source !== filter) return false;
      if (search) {
        const needle = search.toLowerCase();
        if (
          !p.name.toLowerCase().includes(needle) &&
          !p.description.toLowerCase().includes(needle)
        ) {
          return false;
        }
      }
      return true;
    });
  }, [q.data, search, filter]);

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-serif text-3xl font-light tracking-tight">Personas</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Customise your assistant's personality and capabilities. Create your own, edit, assign an
            engine, deactivate, or delete. Bundle personas are read-only — copy one to make it yours.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {(["all", "bundle", "user"] as const).map((k) => (
            <Button
              key={k}
              variant={filter === k ? "accent" : "ghost"}
              size="sm"
              onClick={() => setFilter(k)}
            >
              {k}
            </Button>
          ))}
          <Button variant="accent" size="sm" onClick={() => setCreating((v) => !v)}>
            <Plus className="h-4 w-4" /> New persona
          </Button>
        </div>
      </div>

      {creating && (
        <Card className="border-accent/40">
          <CardContent className="flex flex-wrap items-center gap-3 py-4">
            <div className="flex-1 min-w-[16rem]">
              <Input
                autoFocus
                value={newName}
                onChange={(e) => setNewName(e.target.value.toLowerCase())}
                placeholder="new-persona-name (lowercase, a–z 0–9 - _)"
                onKeyDown={(e) => {
                  if (e.key === "Enter" && nameValid) createMut.mutate(newName);
                  if (e.key === "Escape") setCreating(false);
                }}
              />
              <p className="mt-1 text-[11px] text-muted-foreground">
                {newName && !nameValid
                  ? existingNames.has(newName)
                    ? "A persona with this name already exists."
                    : "Use a lowercase DNS-label-like name."
                  : "A fresh user-scope persona is created and opened for editing."}
              </p>
            </div>
            <Button
              variant="accent"
              disabled={!nameValid || createMut.isPending}
              onClick={() => createMut.mutate(newName)}
            >
              {createMut.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
              Create
            </Button>
            <Button variant="ghost" onClick={() => setCreating(false)}>
              Cancel
            </Button>
          </CardContent>
        </Card>
      )}

      <div className="relative max-w-md">
        <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search by name or description…"
          className="pl-9"
        />
      </div>

      {(q.isLoading || (q.isError && q.isFetching)) && (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-40" />
          ))}
        </div>
      )}

      {q.isError && !q.isFetching && (
        <Card className="border-destructive/40 bg-destructive/5">
          <CardContent className="py-4 text-sm text-destructive">
            Persona list failed to load: {(q.error as Error).message}
          </CardContent>
        </Card>
      )}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {items.map((p) => (
          <Card
            key={p.path}
            className={`flex h-full flex-col border-border/60 transition-all hover:-translate-y-0.5 hover:border-accent/40 hover:shadow-lg ${
              p.disabled ? "opacity-60" : ""
            }`}
          >
            <button
              onClick={() => navigate(`/app/personas/${encodeURIComponent(p.name)}`)}
              className="flex-1 text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-t-xl"
            >
              <CardHeader className="pb-2">
                <div className="flex items-center justify-between gap-2">
                  <CardTitle className="text-lg capitalize">
                    {p.name.replace(/-/g, " ")}
                  </CardTitle>
                  <div className="flex items-center gap-1.5">
                    {p.disabled && <Badge variant="secondary">off</Badge>}
                    <Badge variant={p.source === "user" ? "ok" : "outline"}>{p.source}</Badge>
                  </div>
                </div>
                <CardDescription className="line-clamp-3 min-h-[2.75rem]">
                  {p.description || "No description provided."}
                </CardDescription>
              </CardHeader>
              <CardContent className="flex flex-wrap items-center gap-1.5">
                {p.forge_enabled && (
                  <Badge variant="accent">
                    <Hammer className="mr-1 h-3 w-3" /> Forge
                  </Badge>
                )}
                {p.skill_forge_enabled && <Badge variant="accent">SkillForge</Badge>}
                {p.engine && (
                  <Badge variant="secondary" className="font-mono text-[10px]">
                    <Cpu className="mr-1 h-3 w-3" /> {p.engine}
                  </Badge>
                )}
                {p.permission_mode && (
                  <Badge variant="secondary" className="font-mono text-[10px]">
                    {p.permission_mode}
                  </Badge>
                )}
                {p.ldd_preset && p.ldd_preset !== "off" && (
                  <Badge variant="secondary">LDD · {p.ldd_preset}</Badge>
                )}
                <span className="ml-auto font-mono text-[10px] text-muted-foreground">
                  mcp:{p.mcp_count}
                </span>
              </CardContent>
            </button>
            <div className="flex items-center gap-2 border-t border-border/50 px-4 py-2">
              <Button
                variant="ghost"
                size="sm"
                disabled={toggleMut.isPending}
                onClick={() => toggleMut.mutate({ name: p.name, disabled: !p.disabled })}
              >
                <Power className="h-3.5 w-3.5" />
                {p.disabled ? "Activate" : "Deactivate"}
              </Button>
              {p.source === "user" && (
                <Button
                  variant="ghost"
                  size="sm"
                  className="text-destructive hover:text-destructive"
                  disabled={deleteMut.isPending}
                  onClick={() => {
                    if (window.confirm(`Delete persona "${p.name}"? This removes your user-scope copy.`)) {
                      deleteMut.mutate(p.name);
                    }
                  }}
                >
                  <Trash2 className="h-3.5 w-3.5" /> Delete
                </Button>
              )}
            </div>
          </Card>
        ))}
      </div>

      {q.data && items.length === 0 && (
        <Card className="border-dashed">
          <CardContent className="py-12 text-center text-sm text-muted-foreground">
            No personas match your filters.
          </CardContent>
        </Card>
      )}

      {toast && (
        <div
          className={
            toast.kind === "ok"
              ? "fixed bottom-6 right-6 z-50 flex items-center gap-3 rounded-md border border-emerald-500/30 bg-emerald-500/10 px-4 py-2 text-sm text-emerald-700 shadow-lg dark:text-emerald-300"
              : "fixed bottom-6 right-6 z-50 flex items-center gap-3 rounded-md border border-destructive/30 bg-destructive/10 px-4 py-2 text-sm text-destructive shadow-lg"
          }
          onClick={() => setToast(null)}
        >
          {toast.msg}
          <X className="h-4 w-4 cursor-pointer" />
        </div>
      )}
    </div>
  );
}

// ── Cost-tier badge helper ────────────────────────────────────────────────────

function costTier(modelId: string): { label: string; cls: string } {
  const m = modelId.toLowerCase();
  if (!m || m === "null") return { label: "inherit", cls: "text-muted-foreground" };
  if (m.includes("haiku")) return { label: "$ budget", cls: "text-emerald-600 dark:text-emerald-400" };
  if (m.includes("sonnet")) return { label: "$$ standard", cls: "text-sky-600 dark:text-sky-400" };
  if (m.includes("opus") || m.includes("fable")) return { label: "$$$ premium", cls: "text-violet-600 dark:text-violet-400" };
  // local models (Hermes/Ollama)
  if (
    m.includes("qwen") || m.includes("llama") || m.includes("mistral") ||
    m.includes("gemma") || m.includes("phi")
  ) {
    return { label: "free (local)", cls: "text-slate-500" };
  }
  return { label: "$$ standard", cls: "text-sky-600 dark:text-sky-400" };
}

const ENGINE_LABELS: Record<string, string> = {
  claude_code: "Claude Code",
  hermes:      "Hermes (Ollama)",
  codex_cli:   "Codex CLI",
  opencode:    "OpenCode",
  copilot:     "Copilot (worker-only)",
};

// ── PersonaEngineEditor ───────────────────────────────────────────────────────

function PersonaEngineEditor({
  personaName,
  isEditable,
  onToast,
}: {
  personaName: string;
  isEditable: boolean;
  onToast: (t: { kind: "ok" | "err"; msg: string }) => void;
}) {
  const { session } = useAuth();
  const qc = useQueryClient();

  const q = useQuery<PersonaEngineConfig>({
    queryKey: ["persona-engine", personaName],
    queryFn: ({ signal }) => getPersonaEngine(personaName, signal),
  });

  const [engine, setEngine] = React.useState<string>("");
  const [osModel, setOsModel] = React.useState<string>("");
  const [workerModel, setWorkerModel] = React.useState<string>("");
  const [engineLock, setEngineLock] = React.useState(false);
  const [dirty, setDirty] = React.useState(false);
  // Track dirty state via ref so the useEffect can read it without adding
  // it as a dependency (avoids resetting unsaved edits on external refetch).
  const dirtyRef = React.useRef(false);

  // Sync from API data only when the user has no unsaved edits.
  // If dirty=true (user is editing), ignore external refetch updates.
  React.useEffect(() => {
    if (q.data && !dirtyRef.current) {
      setEngine(q.data.engine ?? "");
      setOsModel(q.data.os_model ?? "");
      setWorkerModel(q.data.worker_model ?? "");
      setEngineLock(q.data.engine_lock ?? false);
      setDirty(false);
    }
  }, [q.data]);

  const reg = q.data?.registry ?? {};
  const availEngines = (q.data?.available_engines ?? []).filter(
    (e) => e !== "copilot",  // copilot is worker-only, excluded per ADR-0071
  );

  const osMods = engine && reg[engine]
    ? (reg[engine].os_models ?? []).map((m) => m.id)
    : [];
  const wmMods = engine && reg[engine]
    ? (reg[engine].worker_models ?? []).map((m) => m.id)
    : [];

  const saveMut = useMutation({
    mutationFn: () =>
      setPersonaEngine(
        personaName,
        {
          engine: engine || null,
          os_model: osModel || null,
          worker_model: workerModel || null,
          engine_lock: engineLock,
        },
        session!.csrf_token,
      ),
    onSuccess: async () => {
      dirtyRef.current = false;
      setDirty(false);
      onToast({ kind: "ok", msg: "Engine config saved." });
      await qc.invalidateQueries({ queryKey: ["persona-engine", personaName] });
    },
    onError: (e: Error) => onToast({ kind: "err", msg: e.message }),
  });

  const handleEngineChange = (val: string) => {
    dirtyRef.current = true;
    setEngine(val);
    setOsModel("");
    setWorkerModel("");
    setDirty(true);
  };

  if (q.isLoading) {
    return <Skeleton className="h-40 w-full" />;
  }

  const osTier = costTier(osModel);
  const wmTier = costTier(workerModel);

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <Cpu className="h-4 w-4 text-accent" />
            <CardTitle className="text-base">Engine &amp; Model</CardTitle>
          </div>
          {dirty && isEditable && (
            <Button
              size="sm"
              variant="accent"
              onClick={() => saveMut.mutate()}
              disabled={saveMut.isPending}
            >
              {saveMut.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
              Save
            </Button>
          )}
        </div>
        <CardDescription>
          Per-persona engine and model pin. Overrides tenant defaults; cannot
          override the operator policy gate.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Engine selector */}
        <div className="grid grid-cols-[8rem_1fr] items-center gap-3">
          <label className="text-sm font-medium text-muted-foreground">Engine</label>
          <select
            disabled={!isEditable}
            value={engine}
            onChange={(e) => handleEngineChange(e.target.value)}
            className="h-8 rounded-md border border-input bg-background px-2 text-sm disabled:cursor-not-allowed disabled:opacity-50"
          >
            <option value="">— inherit from tenant —</option>
            {availEngines.map((eid) => (
              <option key={eid} value={eid}>
                {ENGINE_LABELS[eid] ?? eid}
              </option>
            ))}
          </select>
        </div>

        {/* OS-turn model */}
        <div className="grid grid-cols-[8rem_1fr_auto] items-center gap-3">
          <label className="text-sm font-medium text-muted-foreground">OS-turn model</label>
          <select
            disabled={!isEditable || !engine}
            value={osModel}
            onChange={(e) => { dirtyRef.current = true; setOsModel(e.target.value); setDirty(true); }}
            className="h-8 rounded-md border border-input bg-background px-2 text-sm disabled:cursor-not-allowed disabled:opacity-50"
          >
            <option value="">— inherit —</option>
            {osMods.map((id) => (
              <option key={id} value={id}>{id}</option>
            ))}
          </select>
          <span className={`text-[11px] font-mono ${osTier.cls}`}>{osTier.label}</span>
        </div>

        {/* Worker model */}
        <div className="grid grid-cols-[8rem_1fr_auto] items-center gap-3">
          <label className="text-sm font-medium text-muted-foreground">Worker model</label>
          <select
            disabled={!isEditable || !engine}
            value={workerModel}
            onChange={(e) => { dirtyRef.current = true; setWorkerModel(e.target.value); setDirty(true); }}
            className="h-8 rounded-md border border-input bg-background px-2 text-sm disabled:cursor-not-allowed disabled:opacity-50"
          >
            <option value="">— inherit —</option>
            {wmMods.map((id) => (
              <option key={id} value={id}>{id}</option>
            ))}
          </select>
          <span className={`text-[11px] font-mono ${wmTier.cls}`}>{wmTier.label}</span>
        </div>

        {/* Engine lock */}
        <div className="flex items-center justify-between rounded-md border border-border/50 bg-muted/30 px-3 py-2">
          <div>
            <p className="text-sm font-medium">Lock engine</p>
            <p className="text-[11px] text-muted-foreground">
              Prevent per-chat <span className="font-mono">/engine</span> overrides for this persona.
              Does not affect the operator policy gate (L10).
            </p>
          </div>
          <Switch
            disabled={!isEditable}
            checked={engineLock}
            onCheckedChange={(v) => { dirtyRef.current = true; setEngineLock(v); setDirty(true); }}
          />
        </div>

        {!isEditable && (
          <p className="text-[11px] text-muted-foreground">
            Copy this persona to your tenant to edit engine config.
          </p>
        )}
      </CardContent>
    </Card>
  );
}

// ── PersonaDetailPage ─────────────────────────────────────────────────────────

export function PersonaDetailPage() {
  const { name } = useParams<{ name: string }>();
  const navigate = useNavigate();
  const { session } = useAuth();
  const qc = useQueryClient();

  const detail = useQuery({
    queryKey: ["persona", name],
    queryFn: ({ signal }) => getPersona(name!, signal),
    enabled: !!name,
  });

  const [draft, setDraft] = React.useState<string>("");
  const [parseError, setParseError] = React.useState<string | null>(null);
  const [reauthOpen, setReauthOpen] = React.useState(false);
  const [pendingAction, setPendingAction] = React.useState<"save" | null>(null);
  const [toast, setToast] = React.useState<{ kind: "ok" | "err"; msg: string } | null>(null);

  React.useEffect(() => {
    if (detail.data) {
      setDraft(JSON.stringify(detail.data.body, null, 2));
      setParseError(null);
    }
  }, [detail.data]);

  const copyMutation = useMutation({
    mutationFn: async () => copyPersonaFromBundle(name!, session!.csrf_token),
    onSuccess: async (data) => {
      setToast({ kind: "ok", msg: data.copied ? "Copied to user scope." : "User override already exists." });
      await qc.invalidateQueries({ queryKey: ["persona", name] });
      await qc.invalidateQueries({ queryKey: ["personas", "list"] });
    },
    onError: (e: Error) => setToast({ kind: "err", msg: e.message }),
  });

  const saveMutation = useMutation({
    mutationFn: async () => {
      const parsed = JSON.parse(draft);
      return updatePersona(name!, parsed, session!.csrf_token);
    },
    onSuccess: async () => {
      setToast({ kind: "ok", msg: "Persona saved." });
      await qc.invalidateQueries({ queryKey: ["persona", name] });
      await qc.invalidateQueries({ queryKey: ["personas", "list"] });
    },
    onError: (e: Error) => {
      // Surfaces both re-auth failures and validation/IO errors.
      setToast({ kind: "err", msg: e.message });
      throw e;
    },
  });

  const toggleMutation = useMutation({
    mutationFn: async (disabled: boolean) =>
      setPersonaDisabled(name!, disabled, session!.csrf_token),
    onSuccess: async (d) => {
      setToast({ kind: "ok", msg: d.disabled ? "Persona deactivated." : "Persona activated." });
      await qc.invalidateQueries({ queryKey: ["persona", name] });
      await qc.invalidateQueries({ queryKey: ["personas", "list"] });
    },
    onError: (e: Error) => setToast({ kind: "err", msg: e.message }),
  });

  const deleteMutation = useMutation({
    mutationFn: async () => deletePersona(name!, session!.csrf_token),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["personas", "list"] });
      navigate("/app/personas");
    },
    onError: (e: Error) => setToast({ kind: "err", msg: e.message }),
  });

  const validate = (text: string) => {
    setDraft(text);
    try {
      const obj = JSON.parse(text);
      if (typeof obj !== "object" || obj == null || Array.isArray(obj)) {
        setParseError("Top-level must be an object.");
        return;
      }
      if (obj.name !== name) {
        setParseError(`body.name must equal "${name}"`);
        return;
      }
      setParseError(null);
    } catch (e) {
      setParseError(e instanceof Error ? e.message : "Invalid JSON");
    }
  };

  if (detail.isLoading) {
    return (
      <div className="mx-auto max-w-5xl space-y-4">
        <Skeleton className="h-9 w-1/3" />
        <Skeleton className="h-80 w-full" />
      </div>
    );
  }

  if (detail.isError || !detail.data) {
    return (
      <div className="mx-auto max-w-3xl">
        <Card className="border-destructive/40 bg-destructive/5">
          <CardContent className="py-4 text-sm text-destructive">
            Could not load persona "{name}": {(detail.error as Error | undefined)?.message ?? "not found"}
          </CardContent>
        </Card>
      </div>
    );
  }

  const data: PersonaDetailResponse = detail.data;
  const isEditable = data.editable;

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div className="space-y-1">
          <Button variant="ghost" size="sm" onClick={() => navigate("/app/personas")}>
            <ArrowLeft className="h-4 w-4" /> All personas
          </Button>
          <div className="flex items-center gap-3">
            <Sparkles className="h-6 w-6 text-accent" />
            <h1 className="font-serif text-3xl font-light tracking-tight">
              {data.name}
            </h1>
            <Badge variant={data.source === "user" ? "ok" : "outline"}>{data.source}</Badge>
            {data.disabled && <Badge variant="secondary">off</Badge>}
          </div>
          <p className="font-mono text-xs text-muted-foreground">{data.path}</p>
        </div>
        <div className="flex items-center gap-2">
          {!isEditable && (
            <Button
              variant="outline"
              onClick={() => copyMutation.mutate()}
              disabled={copyMutation.isPending}
            >
              {copyMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Copy className="h-4 w-4" />
              )}
              Copy to my tenant
            </Button>
          )}
          <Button
            variant="outline"
            disabled={toggleMutation.isPending}
            onClick={() => toggleMutation.mutate(!data.disabled)}
            title="Deactivated personas are dropped from auto-routing"
          >
            {toggleMutation.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Power className="h-4 w-4" />
            )}
            {data.disabled ? "Activate" : "Deactivate"}
          </Button>
          {isEditable && (
            <Button
              variant="outline"
              className="text-destructive hover:text-destructive"
              disabled={deleteMutation.isPending}
              onClick={() => {
                if (window.confirm(`Delete persona "${data.name}"? This removes your user-scope copy.`)) {
                  deleteMutation.mutate();
                }
              }}
            >
              {deleteMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Trash2 className="h-4 w-4" />
              )}
              Delete
            </Button>
          )}
          <Button
            variant="accent"
            disabled={!isEditable || !!parseError || saveMutation.isPending}
            onClick={() => {
              setPendingAction("save");
              setReauthOpen(true);
            }}
          >
            {saveMutation.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Save className="h-4 w-4" />
            )}
            Save
          </Button>
        </div>
      </div>

      {!isEditable && (
        <Card className="border-amber-500/40 bg-amber-500/5">
          <CardContent className="flex items-center gap-3 py-3 text-sm">
            <Lock className="h-4 w-4 text-amber-600 dark:text-amber-300" />
            <span>
              This persona is part of the bundle (read-only). Copy it to your tenant to edit.
            </span>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Persona JSON</CardTitle>
          <CardDescription>
            Full body. <span className="font-mono">body.name</span> must equal{" "}
            <span className="font-mono">{name}</span>. Max 64 KiB.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <Textarea
            spellCheck={false}
            disabled={!isEditable}
            value={draft}
            onChange={(e) => validate(e.target.value)}
            className="min-h-[420px]"
          />
          {parseError && (
            <p className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
              {parseError}
            </p>
          )}
          <div className="flex items-center justify-between text-[11px] text-muted-foreground">
            <span>
              {draft.length.toLocaleString()} chars · {new Blob([draft]).size.toLocaleString()} bytes
            </span>
            <span>
              {isEditable ? "User scope — changes persist to disk on Save." : "Read-only view"}
            </span>
          </div>
        </CardContent>
      </Card>

      <PersonaEngineEditor
        personaName={name!}
        isEditable={isEditable}
        onToast={setToast}
      />

      {toast && (
        <div
          className={
            toast.kind === "ok"
              ? "fixed bottom-6 right-6 z-50 flex items-center gap-3 rounded-md border border-emerald-500/30 bg-emerald-500/10 px-4 py-2 text-sm text-emerald-700 shadow-lg dark:text-emerald-300"
              : "fixed bottom-6 right-6 z-50 flex items-center gap-3 rounded-md border border-destructive/30 bg-destructive/10 px-4 py-2 text-sm text-destructive shadow-lg"
          }
          onClick={() => setToast(null)}
        >
          {toast.msg}
          <X className="h-4 w-4 cursor-pointer" />
        </div>
      )}

      <ReauthDialog
        open={reauthOpen}
        onOpenChange={setReauthOpen}
        onConfirm={async () => {
          if (pendingAction === "save") {
            await saveMutation.mutateAsync();
          }
          setPendingAction(null);
        }}
      />
    </div>
  );
}
