/**
 * Connectors — MCP tool registry for workflow nodes.
 * Route: /app/connectors
 */
import * as React from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Brain,
  CalendarDays,
  Check,
  FileText,
  FolderOpen,
  GitBranch,
  HardDrive,
  Loader2,
  Lock,
  Mail,
  MessageSquare,
  Music,
  Plug,
  Search,
  Trash2,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";
import {
  listConnectors,
  updateConnector,
  type ConnectorSummary,
  listCustomConnectors,
  registerCustomConnector,
  removeCustomConnector,
  type CustomConnectorRegisterRequest,
} from "@/lib/api";

// ── Icon map ──────────────────────────────────────────────────────────────

const ICON_MAP: Record<string, React.ComponentType<{ className?: string }>> = {
  Mail,
  HardDrive,
  CalendarDays,
  Music,
  GitBranch,
  Search,
  FileText,
  MessageSquare,
  FolderOpen,
  Brain,
};

function ConnectorIcon({ name, className }: { name: string; className?: string }) {
  const Icon = ICON_MAP[name] ?? Plug;
  return <Icon className={className} />;
}

// ── Status badge ──────────────────────────────────────────────────────────

function StatusBadge({ status }: { status: ConnectorSummary["status"] }) {
  const map = {
    connected: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/30",
    needs_key: "bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/30",
    disabled: "bg-muted/60 text-muted-foreground border-border",
  };
  const labels = { connected: "Connected", needs_key: "Needs API key", disabled: "Disabled" };
  return (
    <span className={cn("rounded-full border px-2 py-0.5 text-[10px] font-medium", map[status])}>
      {labels[status]}
    </span>
  );
}

// ── Configure modal ───────────────────────────────────────────────────────

function ConfigureModal({
  connector,
  open,
  onOpenChange,
  csrf,
  onSaved,
}: {
  connector: ConnectorSummary;
  open: boolean;
  onOpenChange: (v: boolean) => void;
  csrf: string;
  onSaved: () => void;
}) {
  const [apiKey, setApiKey] = React.useState("");
  const [extra, setExtra] = React.useState<Record<string, string>>(
    connector.extra_values ?? {},
  );
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  const save = async (enable: boolean) => {
    setSaving(true);
    setError(null);
    try {
      await updateConnector(
        connector.id,
        { enabled: enable, api_key: apiKey || undefined, extra },
        csrf,
      );
      onSaved();
      onOpenChange(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2.5">
            <ConnectorIcon name={connector.icon} className="h-5 w-5 text-accent" />
            {connector.name}
          </DialogTitle>
          <DialogDescription>{connector.description}</DialogDescription>
        </DialogHeader>

        <div className="space-y-4 pt-1">
          {/* Kind info */}
          {connector.kind === "session_mcp" ? (
            <div className="flex items-start gap-2.5 rounded-lg border border-emerald-500/30 bg-emerald-500/5 p-3 text-sm">
              <Check className="mt-0.5 h-4 w-4 shrink-0 text-emerald-500" />
              <div>
                <div className="font-medium text-emerald-700 dark:text-emerald-400">
                  OAuth — no key required
                </div>
                <div className="text-xs text-muted-foreground">
                  Connected via your Claude Code session. Just enable it to use in workflows.
                </div>
              </div>
            </div>
          ) : connector.api_key_label ? (
            <div>
              <Label htmlFor="apikey" className="mb-1.5 block text-xs">
                {connector.api_key_label}
                {connector.api_key_set && (
                  <span className="ml-2 text-emerald-500">✓ key stored</span>
                )}
              </Label>
              <Input
                id="apikey"
                type="password"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder={connector.api_key_set ? "Leave blank to keep existing key" : "Paste API key…"}
                className="font-mono text-sm"
                autoComplete="new-password"
                autoCorrect="off"
                spellCheck={false}
                data-lpignore="true"
                data-1p-ignore
                data-bwignore
              />
            </div>
          ) : null}

          {/* Extra config fields */}
          {Object.entries(connector.config_extra ?? {}).map(([key, meta]) => (
            <div key={key}>
              <Label className="mb-1.5 block text-xs">{meta.label}</Label>
              <Input
                value={extra[key] ?? meta.default}
                onChange={(e) => setExtra((prev) => ({ ...prev, [key]: e.target.value }))}
                placeholder={meta.default}
              />
            </div>
          ))}

          {/* Capabilities */}
          <div>
            <div className="mb-1.5 text-[10px] font-medium uppercase tracking-widest text-muted-foreground">
              Available operations
            </div>
            <div className="flex flex-wrap gap-1.5">
              {connector.capabilities.map((cap) => (
                <Badge key={cap} variant="secondary" className="font-mono text-[10px]">
                  {cap}
                </Badge>
              ))}
            </div>
          </div>

          {/* Example */}
          <div className="rounded-md border border-border bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
            <span className="font-medium text-foreground">Example:</span>{" "}
            <em>{connector.example_instruction}</em>
          </div>

          {error && <p className="text-xs text-destructive">{error}</p>}

          <div className="flex justify-between pt-1">
            {connector.enabled && (
              <Button variant="outline" size="sm" onClick={() => save(false)} disabled={saving}>
                Disable
              </Button>
            )}
            <div className="ml-auto flex gap-2">
              <Button variant="ghost" size="sm" onClick={() => onOpenChange(false)}>
                Cancel
              </Button>
              <Button size="sm" onClick={() => save(true)} disabled={saving}>
                {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : "Enable"}
              </Button>
            </div>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

// ── Connector card ────────────────────────────────────────────────────────

const CATEGORY_ORDER = ["Communication", "Storage", "Productivity", "Development", "Search", "Media", "Utilities"];

function ConnectorCard({
  connector,
  onConfigure,
}: {
  connector: ConnectorSummary;
  onConfigure: () => void;
}) {
  return (
    <Card
      className={cn(
        "group transition-all hover:border-accent/40 cursor-pointer",
        connector.status === "connected" && "border-emerald-500/30",
      )}
      onClick={onConfigure}
    >
      <CardContent className="p-4">
        <div className="mb-3 flex items-start justify-between">
          <div
            className={cn(
              "rounded-lg p-2",
              connector.status === "connected"
                ? "bg-accent/15 text-accent"
                : "bg-muted text-muted-foreground",
            )}
          >
            <ConnectorIcon name={connector.icon} className="h-5 w-5" />
          </div>
          <StatusBadge status={connector.status} />
        </div>
        <div className="font-semibold leading-tight">{connector.name}</div>
        <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">{connector.description}</p>
        <div className="mt-3 flex flex-wrap gap-1">
          {connector.capabilities.slice(0, 3).map((c) => (
            <span key={c} className="rounded bg-muted px-1.5 py-0.5 font-mono text-[9px] text-muted-foreground">
              {c}
            </span>
          ))}
          {connector.capabilities.length > 3 && (
            <span className="rounded bg-muted px-1.5 py-0.5 font-mono text-[9px] text-muted-foreground">
              +{connector.capabilities.length - 3}
            </span>
          )}
        </div>
        {connector.kind === "session_mcp" && (
          <div className="mt-2 flex items-center gap-1 text-[10px] text-muted-foreground">
            <Lock className="h-2.5 w-2.5" />
            OAuth via Claude Code session
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ── CustomConnectorsSection ───────────────────────────────────────────────

type TransportKind = "stdio" | "sse" | "http";

function CustomConnectorsSection() {
  const { session } = useAuth();
  const qc = useQueryClient();

  const [dialogOpen, setDialogOpen] = React.useState(false);
  const [formError, setFormError] = React.useState<string | null>(null);

  // Form state
  const [connectorId, setConnectorId] = React.useState("");
  const [displayName, setDisplayName] = React.useState("");
  const [transport, setTransport] = React.useState<TransportKind>("stdio");
  const [command, setCommand] = React.useState("");
  const [url, setUrl] = React.useState("");
  const [description, setDescription] = React.useState("");

  const resetForm = () => {
    setConnectorId("");
    setDisplayName("");
    setTransport("stdio");
    setCommand("");
    setUrl("");
    setDescription("");
    setFormError(null);
  };

  const customList = useQuery({
    queryKey: ["custom-connectors"],
    queryFn: ({ signal }) => listCustomConnectors(signal),
  });

  const registerMutation = useMutation({
    mutationFn: ({ id, body }: { id: string; body: CustomConnectorRegisterRequest }) =>
      registerCustomConnector(id, body, session!.csrf_token),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["custom-connectors"] });
      setDialogOpen(false);
      resetForm();
    },
    onError: (e) => {
      setFormError(e instanceof Error ? e.message : String(e));
    },
  });

  const removeMutation = useMutation({
    mutationFn: (id: string) => removeCustomConnector(id, session!.csrf_token),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["custom-connectors"] });
    },
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setFormError(null);

    if (!connectorId.trim() || !displayName.trim()) {
      setFormError("Connector ID and Display Name are required.");
      return;
    }
    if (transport === "stdio" && !command.trim()) {
      setFormError("Command is required for stdio transport.");
      return;
    }
    if ((transport === "sse" || transport === "http") && !url.trim()) {
      setFormError("URL is required for sse/http transport.");
      return;
    }

    const body: CustomConnectorRegisterRequest = {
      display_name: displayName.trim(),
      transport,
      ...(transport === "stdio"
        ? { command: command.split(",").map((s) => s.trim()).filter(Boolean) }
        : { url: url.trim() }),
      ...(description.trim() ? { description: description.trim() } : {}),
    };

    registerMutation.mutate({ id: connectorId.trim(), body });
  };

  const connectors = customList.data?.connectors ?? [];

  return (
    <div className="space-y-4">
      {/* Section header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold">Custom Connectors</h2>
          <p className="mt-0.5 text-xs text-muted-foreground">
            Register your own MCP servers as connectors available in workflow nodes.
          </p>
        </div>
        <Button
          size="sm"
          onClick={() => { resetForm(); setDialogOpen(true); }}
          data-testid="add-custom-connector-btn"
        >
          + Add Connector
        </Button>
      </div>

      {/* Loading skeletons */}
      {customList.isLoading && (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-28 w-full" />
          ))}
        </div>
      )}

      {/* Empty state */}
      {!customList.isLoading && connectors.length === 0 && (
        <div className="rounded-lg border border-dashed border-border bg-muted/20 px-6 py-10 text-center">
          <Plug className="mx-auto mb-3 h-8 w-8 text-muted-foreground/50" />
          <p className="text-sm text-muted-foreground">No custom connectors registered yet.</p>
          <p className="mt-1 text-xs text-muted-foreground">
            Click <span className="font-medium text-foreground">+ Add Connector</span> to register an MCP server.
          </p>
        </div>
      )}

      {/* Connector cards */}
      {connectors.length > 0 && (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {connectors.map((c) => (
            <Card
              key={c.connector_id}
              data-testid={`custom-connector-card-${c.connector_id}`}
              className="group transition-all hover:border-accent/30"
            >
              <CardContent className="p-4">
                <div className="mb-2 flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="truncate font-semibold leading-tight">{c.display_name}</div>
                    <div className="mt-0.5 truncate font-mono text-[10px] text-muted-foreground">
                      {c.connector_id}
                    </div>
                  </div>
                  <Badge variant="outline" className="shrink-0 font-mono text-[10px]">
                    {c.transport}
                  </Badge>
                </div>

                {c.description && (
                  <p className="mb-2 line-clamp-2 text-xs text-muted-foreground">{c.description}</p>
                )}

                {c.capabilities && c.capabilities.length > 0 && (
                  <div className="mb-3 flex flex-wrap gap-1">
                    {c.capabilities.slice(0, 4).map((cap: string) => (
                      <span
                        key={cap}
                        className="rounded bg-muted px-1.5 py-0.5 font-mono text-[9px] text-muted-foreground"
                      >
                        {cap}
                      </span>
                    ))}
                    {c.capabilities.length > 4 && (
                      <span className="rounded bg-muted px-1.5 py-0.5 font-mono text-[9px] text-muted-foreground">
                        +{c.capabilities.length - 4}
                      </span>
                    )}
                  </div>
                )}

                <div className="flex justify-end">
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-7 px-2 text-destructive hover:bg-destructive/10 hover:text-destructive"
                    onClick={() => removeMutation.mutate(c.connector_id)}
                    disabled={removeMutation.isPending}
                    aria-label={`Remove ${c.display_name}`}
                  >
                    {removeMutation.isPending ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <Trash2 className="h-3.5 w-3.5" />
                    )}
                    <span className="ml-1 text-xs">Remove</span>
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {/* Add Connector dialog */}
      <Dialog open={dialogOpen} onOpenChange={(v) => { if (!v) { setDialogOpen(false); resetForm(); } else { setDialogOpen(true); } }}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Add Custom Connector</DialogTitle>
            <DialogDescription>
              Register an MCP server as a connector available in your workflow nodes.
            </DialogDescription>
          </DialogHeader>

          <form
            data-testid="custom-connector-form"
            onSubmit={handleSubmit}
            className="space-y-4 pt-1"
          >
            {/* Connector ID */}
            <div>
              <Label htmlFor="custom-connector-id" className="mb-1.5 block text-xs">
                Connector ID <span className="text-destructive">*</span>
              </Label>
              <Input
                id="custom-connector-id"
                data-testid="connector-id-input"
                value={connectorId}
                onChange={(e) => setConnectorId(e.target.value)}
                placeholder="e.g. my-org-search"
                className="font-mono text-sm"
                required
              />
            </div>

            {/* Display Name */}
            <div>
              <Label htmlFor="custom-display-name" className="mb-1.5 block text-xs">
                Display Name <span className="text-destructive">*</span>
              </Label>
              <Input
                id="custom-display-name"
                data-testid="custom-connector-display-name-input"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                placeholder="e.g. My Org Search"
                required
              />
            </div>

            {/* Transport */}
            <div>
              <Label htmlFor="custom-transport" className="mb-1.5 block text-xs">
                Transport <span className="text-destructive">*</span>
              </Label>
              <Select
                id="custom-transport"
                value={transport}
                onChange={(e) => setTransport(e.target.value as TransportKind)}
              >
                <option value="stdio">stdio</option>
                <option value="sse">sse</option>
                <option value="http">http</option>
              </Select>
            </div>

            {/* Command — shown for stdio only */}
            {transport === "stdio" && (
              <div>
                <Label htmlFor="custom-command" className="mb-1.5 block text-xs">
                  Command <span className="text-destructive">*</span>
                  <span className="ml-1 font-normal text-muted-foreground">(comma-separated, e.g. npx,@org/server)</span>
                </Label>
                <Input
                  id="custom-command"
                  value={command}
                  onChange={(e) => setCommand(e.target.value)}
                  placeholder="npx,@org/server"
                  className="font-mono text-sm"
                />
              </div>
            )}

            {/* URL — shown for sse and http */}
            {(transport === "sse" || transport === "http") && (
              <div>
                <Label htmlFor="custom-url" className="mb-1.5 block text-xs">
                  URL <span className="text-destructive">*</span>
                </Label>
                <Input
                  id="custom-url"
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  placeholder="https://my-mcp-server.example.com/mcp"
                  className="font-mono text-sm"
                  type="url"
                />
              </div>
            )}

            {/* Description */}
            <div>
              <Label htmlFor="custom-description" className="mb-1.5 block text-xs">
                Description <span className="font-normal text-muted-foreground">(optional)</span>
              </Label>
              <Input
                id="custom-description"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="Short description of what this connector does"
              />
            </div>

            {formError && (
              <p className="text-xs text-destructive">{formError}</p>
            )}

            <div className="flex justify-end gap-2 pt-1">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => { setDialogOpen(false); resetForm(); }}
              >
                Cancel
              </Button>
              <Button
                type="submit"
                size="sm"
                data-testid="custom-connector-submit"
                disabled={registerMutation.isPending}
              >
                {registerMutation.isPending ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  "Add Connector"
                )}
              </Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// ── ConnectorsPage ────────────────────────────────────────────────────────

export function ConnectorsPage() {
  const { session } = useAuth();
  const qc = useQueryClient();
  const [configuring, setConfiguring] = React.useState<ConnectorSummary | null>(null);
  const [filter, setFilter] = React.useState<"all" | "connected" | "available">("all");

  const list = useQuery({
    queryKey: ["connectors"],
    queryFn: ({ signal }) => listConnectors(signal),
  });

  const connectors = React.useMemo(() => list.data?.connectors ?? [], [list.data?.connectors]);
  const connected = connectors.filter((c) => c.status === "connected");

  const filtered = React.useMemo(() => {
    if (filter === "connected") return connectors.filter((c) => c.status === "connected");
    if (filter === "available") return connectors.filter((c) => c.status !== "connected");
    return connectors;
  }, [connectors, filter]);

  // Group by category
  const byCategory = React.useMemo(() => {
    const map = new Map<string, ConnectorSummary[]>();
    for (const cat of CATEGORY_ORDER) map.set(cat, []);
    for (const c of filtered) {
      const arr = map.get(c.category) ?? [];
      arr.push(c);
      map.set(c.category, arr);
    }
    return map;
  }, [filtered]);

  return (
    <div className="mx-auto max-w-6xl space-y-8">
      {/* Header */}
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-serif text-3xl font-light tracking-tight">Connectors</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Connect external tools and APIs to your workflow nodes. Enabled connectors
            can be added to any node via <span className="font-mono text-xs">tools: [id]</span>.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant="outline" className="text-xs">
            <span className="text-emerald-500 font-medium">{connected.length}</span>
            &nbsp;connected / {connectors.length} total
          </Badge>
        </div>
      </div>

      {/* Filter tabs */}
      <div className="flex gap-1 rounded-lg border border-border bg-muted/30 p-1 w-fit">
        {(["all", "connected", "available"] as const).map((f) => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={cn(
              "rounded-md px-3 py-1.5 text-sm transition-colors",
              filter === f
                ? "bg-background text-foreground shadow-sm"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            {f === "all" ? "All" : f === "connected" ? "Connected" : "Not connected"}
          </button>
        ))}
      </div>

      {/* Usage hint */}
      {connected.length > 0 && (
        <div className="rounded-lg border border-accent/20 bg-accent/5 px-4 py-3 text-sm">
          <div className="font-medium text-accent mb-1">Using connectors in workflows</div>
          <div className="text-muted-foreground text-xs">
            Add <span className="font-mono bg-background border border-border rounded px-1">tools: [{connected.map(c => c.id).slice(0, 3).join(", ")}]</span> to any workflow node,
            or tell the design assistant: <em>"use Gmail to send the result"</em>.
          </div>
        </div>
      )}

      {list.isLoading && (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
          {Array.from({ length: 8 }).map((_, i) => (
            <Skeleton key={i} className="h-36 w-full" />
          ))}
        </div>
      )}

      {/* Grouped connector grid */}
      {Array.from(byCategory.entries()).map(([cat, items]) => {
        if (!items.length) return null;
        return (
          <div key={cat}>
            <div className="mb-3 flex items-center gap-2">
              <span className="text-sm font-semibold">{cat}</span>
              <span className="text-xs text-muted-foreground">({items.length})</span>
            </div>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
              {items.map((c) => (
                <ConnectorCard
                  key={c.id}
                  connector={c}
                  onConfigure={() => setConfiguring(c)}
                />
              ))}
            </div>
          </div>
        );
      })}

      {configuring && (
        <ConfigureModal
          connector={configuring}
          open
          onOpenChange={(v) => !v && setConfiguring(null)}
          csrf={session!.csrf_token}
          onSaved={() => qc.invalidateQueries({ queryKey: ["connectors"] })}
        />
      )}

      {/* Divider */}
      <div className="border-t border-border" />

      {/* Custom Connectors */}
      <CustomConnectorsSection />
    </div>
  );
}
