import React, { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Search,
  Star,
  Download,
  TrendingUp,
  Filter,
  Upload,
  FileDown,
  MessageSquarePlus,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { api, ApiError, listRAGProviders } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

interface RAGHubProvider {
  id: string;
  name: string;
  description: string;
  author: string;
  version: string;
  data_classification: string;
  compliance_zone: string;
  capabilities: string[];
  rating: number;
  review_count: number;
  download_count: number;
  published_at: number;
  trending_score: number;
}

interface RAGHubTab {
  id: "discover" | "trending" | "top-rated";
  label: string;
  icon: React.ReactNode;
}

type Notice = { kind: "ok" | "error"; text: string } | null;

/** Human text for a failed hub call (409 exists, 402 licence, 404 ...). */
function describeError(err: unknown): string {
  if (err instanceof ApiError) {
    const d = err.detail as Record<string, unknown> | string | null;
    if (d && typeof d === "object") {
      return String(d.msg ?? d.error ?? `Request failed (${err.status})`);
    }
    if (typeof d === "string" && d) return d;
    return `Request failed (${err.status})`;
  }
  return err instanceof Error ? err.message : "Request failed";
}

export default function RAGHubPage() {
  const [activeTab, setActiveTab] = useState<RAGHubTab["id"]>("discover");
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedZone, setSelectedZone] = useState("");
  const [notice, setNotice] = useState<Notice>(null);
  const queryClient = useQueryClient();

  // Fetch providers based on active tab
  const { data: providersData, isLoading } = useQuery({
    queryKey: ["rag-hub-providers", activeTab, searchQuery, selectedZone],
    queryFn: async () => {
      const endpoint =
        activeTab === "trending"
          ? "/v1/console/hub/trending"
          : activeTab === "top-rated"
          ? "/v1/console/hub/top-rated"
          : "/v1/console/hub/providers";

      const url = new URL(endpoint, window.location.origin);
      if (activeTab === "discover") {
        if (searchQuery) url.searchParams.set("q", searchQuery);
        if (selectedZone) url.searchParams.set("zone", selectedZone);
      }

      const response = await fetch(url.toString(), { credentials: "include" });
      if (!response.ok) throw new Error("Failed to fetch providers");
      return response.json();
    },
    refetchInterval: 30000, // Poll every 30s
  });

  const refreshHub = () => {
    void queryClient.invalidateQueries({ queryKey: ["rag-hub-providers"] });
    void queryClient.invalidateQueries({ queryKey: ["rag-hub-all"] });
    void queryClient.invalidateQueries({ queryKey: ["rag-providers"] });
  };

  const tabs: RAGHubTab[] = [
    { id: "discover", label: "Discover", icon: <Search className="w-4 h-4" /> },
    { id: "trending", label: "Trending", icon: <TrendingUp className="w-4 h-4" /> },
    { id: "top-rated", label: "Top Rated", icon: <Star className="w-4 h-4" /> },
  ];

  return (
    <div className="space-y-6 p-6">
      {/* Header */}
      <div>
        <h1 className="text-3xl font-bold">RAG Hub</h1>
        <p className="text-muted-foreground mt-1">
          This workspace&apos;s provider catalogue. Publish a registered provider to keep a reusable
          copy of it here, and import any entry as a provider under the id you choose. Nothing is
          shared outside this workspace.
        </p>
      </div>

      {notice && (
        <div
          data-testid="hub-notice"
          className={cn(
            "rounded-lg border px-4 py-3 text-sm",
            notice.kind === "ok"
              ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400"
              : "border-destructive/30 bg-destructive/10 text-destructive",
          )}
        >
          {notice.text}
          {notice.kind === "ok" && notice.text.includes("imported") && (
            <>
              {" "}
              <Link to="/app/rag" className="underline">
                Open Knowledge
              </Link>
            </>
          )}
        </div>
      )}

      <PublishPanel onDone={(n) => { setNotice(n); refreshHub(); }} />

      {/* Tabs */}
      <div className="flex border-b border-border space-x-8">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            onClick={() => {
              setActiveTab(tab.id);
              setSearchQuery("");
            }}
            className={cn(
              "px-4 py-3 font-medium border-b-2 transition",
              activeTab === tab.id
                ? "border-accent text-accent"
                : "border-transparent text-muted-foreground hover:text-foreground",
            )}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Search & Filters (Discover tab) */}
      {activeTab === "discover" && (
        <div className="space-y-4">
          <div className="relative">
            <Search className="absolute left-3 top-3 w-5 h-5 text-muted-foreground" />
            <Input
              type="text"
              placeholder="Search providers by name, type, or capability..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="pl-10"
            />
          </div>

          <div className="flex items-center space-x-4">
            <Filter className="w-4 h-4 text-muted-foreground" />
            <select
              value={selectedZone}
              onChange={(e) => setSelectedZone(e.target.value)}
              className={cn(
                "flex h-10 rounded-md border border-input bg-background px-4 py-2 text-sm",
                "ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
              )}
            >
              <option value="">All Zones</option>
              <option value="EU">EU (GDPR)</option>
              <option value="US">US</option>
              <option value="APAC">Asia-Pacific</option>
              <option value="HYBRID">Hybrid</option>
            </select>
          </div>
        </div>
      )}

      {isLoading && (
        <div className="text-center py-12">
          <p className="text-muted-foreground mt-2">Loading providers...</p>
        </div>
      )}

      {providersData?.providers && providersData.providers.length > 0 ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {providersData.providers.map((provider: RAGHubProvider) => (
            <ProviderCard
              key={provider.id}
              provider={provider}
              onDone={(n) => {
                setNotice(n);
                refreshHub();
              }}
            />
          ))}
        </div>
      ) : (
        !isLoading && (
          <div className="text-center py-12" data-testid="hub-empty">
            <p className="text-muted-foreground">
              {searchQuery
                ? "No providers found matching your search"
                : "Nothing published yet. Publish a registered provider above."}
            </p>
          </div>
        )
      )}
    </div>
  );
}

// ── Publish panel ──────────────────────────────────────────

function PublishPanel({ onDone }: { onDone: (n: Notice) => void }) {
  const { session } = useAuth();
  const [selected, setSelected] = useState("");

  const { data: registered } = useQuery({
    queryKey: ["rag-providers"],
    queryFn: () => listRAGProviders(),
  });
  const { data: published } = useQuery({
    queryKey: ["rag-hub-all"],
    queryFn: async () => {
      const r = await fetch("/v1/console/hub/providers?limit=100", { credentials: "include" });
      if (!r.ok) throw new Error("Failed to fetch providers");
      return (await r.json()) as { providers: RAGHubProvider[] };
    },
  });

  const publishedIds = new Set((published?.providers ?? []).map((p) => p.id));
  const candidates = (registered?.providers ?? []).filter((p) => !publishedIds.has(p.id));

  const publish = useMutation({
    mutationFn: async (providerId: string) =>
      api<{ status?: string; error?: string }>("/hub/providers", {
        method: "POST",
        csrf: session?.csrf_token,
        body: { provider_id: providerId },
      }),
    onSuccess: (res, providerId) => {
      if (res.status === "published") {
        setSelected("");
        onDone({ kind: "ok", text: `Published "${providerId}" to this workspace's catalogue.` });
      } else {
        onDone({ kind: "error", text: String(res.error ?? "Publish failed") });
      }
    },
    onError: (err) => onDone({ kind: "error", text: describeError(err) }),
  });

  return (
    <div className="flex flex-wrap items-end gap-3 rounded-lg border border-border bg-card p-4">
      <div className="min-w-[16rem] flex-1">
        <label htmlFor="hub-publish-select" className="text-sm font-medium">
          Publish a registered provider
        </label>
        <select
          id="hub-publish-select"
          data-testid="hub-publish-select"
          value={selected}
          onChange={(e) => setSelected(e.target.value)}
          className="mt-1 flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
        >
          <option value="">
            {candidates.length === 0 ? "No unpublished provider" : "Choose a provider..."}
          </option>
          {candidates.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name} ({p.id})
            </option>
          ))}
        </select>
      </div>
      <Button
        data-testid="hub-publish-btn"
        variant="accent"
        disabled={!selected || publish.isPending}
        onClick={() => publish.mutate(selected)}
      >
        <Upload className="w-4 h-4 mr-2" />
        {publish.isPending ? "Publishing..." : "Publish"}
      </Button>
    </div>
  );
}

// ── Provider Card Component ────────────────────────────────

interface ProviderCardProps {
  provider: RAGHubProvider;
  onDone: (n: Notice) => void;
}

function ProviderCard({ provider, onDone }: ProviderCardProps) {
  const { session } = useAuth();
  const [dialog, setDialog] = useState<"import" | "review" | null>(null);
  const [importId, setImportId] = useState(provider.id);
  const [rating, setRating] = useState(5);
  const [reviewText, setReviewText] = useState("");

  const importMutation = useMutation({
    mutationFn: async () =>
      api<{ status?: string; provider_id?: string; error?: string }>("/hub/import", {
        method: "POST",
        csrf: session?.csrf_token,
        body: { hub_provider_id: provider.id, provider_id: importId.trim() },
      }),
    onSuccess: (res) => {
      if (res.status === "imported") {
        setDialog(null);
        onDone({ kind: "ok", text: `Provider "${res.provider_id}" imported and registered.` });
      } else {
        onDone({ kind: "error", text: String(res.error ?? "Import failed") });
      }
    },
    onError: (err) => onDone({ kind: "error", text: describeError(err) }),
  });

  const reviewMutation = useMutation({
    mutationFn: async () =>
      api<{ status?: string; error?: string }>("/hub/reviews", {
        method: "POST",
        csrf: session?.csrf_token,
        body: { provider_id: provider.id, rating, text: reviewText },
      }),
    onSuccess: (res) => {
      if (res.status === "added") {
        setDialog(null);
        setReviewText("");
        onDone({ kind: "ok", text: `Review added to "${provider.id}".` });
      } else {
        onDone({ kind: "error", text: String(res.error ?? "Review failed") });
      }
    },
    onError: (err) => onDone({ kind: "error", text: describeError(err) }),
  });

  const exportManifest = useMutation({
    mutationFn: async () =>
      api<{ status?: string; manifest_yaml?: string; error?: string }>("/hub/export", {
        method: "POST",
        csrf: session?.csrf_token,
        body: { provider_id: provider.id },
      }),
    onSuccess: (res) => {
      if (res.status === "exported" && res.manifest_yaml) {
        const url = URL.createObjectURL(new Blob([res.manifest_yaml], { type: "text/yaml" }));
        const a = document.createElement("a");
        a.href = url;
        a.download = `${provider.id}.yaml`;
        a.click();
        URL.revokeObjectURL(url);
        onDone({ kind: "ok", text: `Exported the manifest of "${provider.id}".` });
      } else {
        onDone({
          kind: "error",
          text: "Export needs the provider to be registered in this workspace.",
        });
      }
    },
    onError: (err) => onDone({ kind: "error", text: describeError(err) }),
  });

  return (
    <div
      data-testid={`hub-card-${provider.id}`}
      className="border border-border rounded-lg p-6 hover:shadow-lg transition space-y-4 bg-card"
    >
      <div>
        <h3 className="font-bold text-lg">{provider.name}</h3>
        <p className="text-sm text-muted-foreground">by {provider.author}</p>
      </div>

      <p className="text-foreground text-sm">{provider.description}</p>

      <div className="flex items-center justify-between text-sm">
        <span className="text-muted-foreground">v{provider.version}</span>
        <div className="flex space-x-2">
          <Badge variant="accent" className="text-xs">
            {provider.data_classification}
          </Badge>
          <Badge variant="ok" className="text-xs">
            {provider.compliance_zone}
          </Badge>
        </div>
      </div>

      <div className="flex flex-wrap gap-2">
        {provider.capabilities.slice(0, 3).map((cap) => (
          <Badge key={cap} variant="secondary" className="text-xs">
            {cap}
          </Badge>
        ))}
        {provider.capabilities.length > 3 && (
          <Badge variant="secondary" className="text-xs">
            +{provider.capabilities.length - 3} more
          </Badge>
        )}
      </div>

      <div className="grid grid-cols-3 gap-4 pt-4 border-t border-border">
        <div className="text-center">
          <div className="flex items-center justify-center space-x-1">
            <Star className="w-4 h-4 text-amber-500 dark:text-amber-400 fill-amber-500 dark:fill-amber-400" />
            <span className="font-bold" data-testid={`hub-rating-${provider.id}`}>{provider.rating}</span>
          </div>
          <p className="text-xs text-muted-foreground">{provider.review_count} reviews</p>
        </div>
        <div className="text-center">
          <div className="flex items-center justify-center">
            <Download className="w-4 h-4 text-accent mr-1" />
            <span className="font-bold">{provider.download_count}</span>
          </div>
          <p className="text-xs text-muted-foreground">imports</p>
        </div>
        <div className="text-center">
          <div className="flex items-center justify-center">
            <TrendingUp className="w-4 h-4 text-emerald-600 dark:text-emerald-400 mr-1" />
            <span className="font-bold">{(provider.trending_score * 100).toFixed(0)}</span>
          </div>
          <p className="text-xs text-muted-foreground">trending</p>
        </div>
      </div>

      <div className="grid grid-cols-3 gap-2">
        <Button
          variant="accent"
          data-testid={`hub-import-btn-${provider.id}`}
          onClick={() => setDialog("import")}
        >
          Import
        </Button>
        <Button
          variant="outline"
          data-testid={`hub-review-btn-${provider.id}`}
          onClick={() => setDialog("review")}
        >
          <MessageSquarePlus className="w-4 h-4 mr-1" />
          Review
        </Button>
        <Button
          variant="outline"
          data-testid={`hub-export-btn-${provider.id}`}
          disabled={exportManifest.isPending}
          onClick={() => exportManifest.mutate()}
        >
          <FileDown className="w-4 h-4 mr-1" />
          Export
        </Button>
      </div>

      {dialog === "import" && (
        <Dialog title={`Import ${provider.name}`} onClose={() => setDialog(null)}>
          <p className="text-muted-foreground text-sm">
            Registers this catalogue entry as a provider in this workspace. Choose the id it will
            get; it must not be in use already.
          </p>
          <label htmlFor="hub-import-id" className="text-sm font-medium">
            Provider id
          </label>
          <Input
            id="hub-import-id"
            data-testid="hub-import-id"
            value={importId}
            onChange={(e) => setImportId(e.target.value)}
          />
          <div className="flex space-x-4">
            <Button variant="outline" className="flex-1" onClick={() => setDialog(null)}>
              Cancel
            </Button>
            <Button
              variant="accent"
              className="flex-1"
              data-testid="hub-import-confirm"
              disabled={!importId.trim() || importMutation.isPending}
              onClick={() => importMutation.mutate()}
            >
              {importMutation.isPending ? "Importing..." : "Import"}
            </Button>
          </div>
        </Dialog>
      )}

      {dialog === "review" && (
        <Dialog title={`Review ${provider.name}`} onClose={() => setDialog(null)}>
          <label htmlFor="hub-review-rating" className="text-sm font-medium">
            Rating
          </label>
          <select
            id="hub-review-rating"
            data-testid="hub-review-rating"
            value={rating}
            onChange={(e) => setRating(Number(e.target.value))}
            className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
          >
            {[5, 4, 3, 2, 1].map((n) => (
              <option key={n} value={n}>
                {n} / 5
              </option>
            ))}
          </select>
          <label htmlFor="hub-review-text" className="text-sm font-medium">
            Comment
          </label>
          <Input
            id="hub-review-text"
            data-testid="hub-review-text"
            maxLength={500}
            value={reviewText}
            onChange={(e) => setReviewText(e.target.value)}
          />
          <div className="flex space-x-4">
            <Button variant="outline" className="flex-1" onClick={() => setDialog(null)}>
              Cancel
            </Button>
            <Button
              variant="accent"
              className="flex-1"
              data-testid="hub-review-confirm"
              disabled={reviewMutation.isPending}
              onClick={() => reviewMutation.mutate()}
            >
              {reviewMutation.isPending ? "Saving..." : "Add review"}
            </Button>
          </div>
        </Dialog>
      )}
    </div>
  );
}

function Dialog({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  return (
    <div
      className="fixed inset-0 bg-background/70 backdrop-blur-sm flex items-center justify-center z-50"
      onKeyDown={(e) => e.key === "Escape" && onClose()}
    >
      <div
        role="dialog"
        aria-label={title}
        className="bg-card border border-border rounded-lg p-6 max-w-md w-full space-y-4 shadow-2xl shadow-black/30"
      >
        <h2 className="text-lg font-bold text-foreground">{title}</h2>
        {children}
      </div>
    </div>
  );
}
