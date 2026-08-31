/**
 * RAG Integration Console
 * Route: /app/rag
 *
 * Provides:
 * - Provider management and health monitoring
 * - RAG query tester with live execution
 * - Performance dashboard with statistics
 */
import * as React from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import {
  Activity,
  AlertCircle,
  CheckCircle2,
  Database,
  ExternalLink,
  Loader2,
  Lock,
  Network,
  Search,
  Settings,
  TrendingUp,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";
import {
  getLicenseInfo,
  listRAGProviders,
  getRAGProviderHealth,
  executeRAGQuery,
  type RAGProvider,
  type RAGQueryRequest,
  type RAGQueryResponse,
} from "@/lib/api";

// ── Status Badge ──────────────────────────────────────────────────

function HealthBadge({ status }: { status: RAGProvider["health_status"] }) {
  const variants = {
    healthy: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/30",
    unhealthy: "bg-red-500/10 text-red-600 dark:text-red-400 border-red-500/30",
    unknown: "bg-gray-500/10 text-gray-600 dark:text-gray-400 border-gray-500/30",
  };

  const icons = {
    healthy: <CheckCircle2 className="h-4 w-4" />,
    unhealthy: <AlertCircle className="h-4 w-4" />,
    unknown: <Activity className="h-4 w-4" />,
  };

  const labels = {
    healthy: "Healthy",
    unhealthy: "Unhealthy",
    unknown: "Unknown",
  };

  return (
    <span className={cn("inline-flex items-center gap-1 rounded-full border px-2 py-1 text-xs font-medium", variants[status])}>
      {icons[status]}
      {labels[status]}
    </span>
  );
}

// ── Provider Card ─────────────────────────────────────────────────

function ProviderCard({ provider }: { provider: RAGProvider }) {
  const { data: health, isLoading, refetch } = useQuery({
    queryKey: ["rag-provider-health", provider.id],
    queryFn: () => getRAGProviderHealth(provider.id),
    refetchInterval: 30000, // 30s polling
  });

  const current = health || provider;

  return (
    <Card className="hover:shadow-md transition-shadow">
      <CardHeader>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Database className="h-5 w-5 text-blue-500" />
            <div>
              <CardTitle className="text-base">{current.name}</CardTitle>
              <CardDescription className="text-xs">{current.id}</CardDescription>
            </div>
          </div>
          <HealthBadge status={current.health_status} />
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid grid-cols-2 gap-4 text-sm">
          <div>
            <span className="text-muted-foreground">Status</span>
            <p className="font-medium capitalize">{current.status}</p>
          </div>
          <div>
            <span className="text-muted-foreground">Latency</span>
            <p className="font-medium">{current.latency_ms}ms</p>
          </div>
          <div>
            <span className="text-muted-foreground">Total Queries</span>
            <p className="font-medium">{current.query_stats.total_queries.toLocaleString()}</p>
          </div>
          <div>
            <span className="text-muted-foreground">Today</span>
            <p className="font-medium">{current.query_stats.queries_today}</p>
          </div>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={() => refetch()}
          disabled={isLoading}
          className="w-full"
        >
          {isLoading ? (
            <>
              <Loader2 className="h-4 w-4 mr-2 animate-spin" />
              Checking...
            </>
          ) : (
            <>
              <Network className="h-4 w-4 mr-2" />
              Check Health
            </>
          )}
        </Button>
      </CardContent>
    </Card>
  );
}

// ── Query Tester ──────────────────────────────────────────────────

function QueryTester() {
  const [query, setQuery] = React.useState("");
  const [limit, setLimit] = React.useState("5");
  const [result, setResult] = React.useState<RAGQueryResponse | null>(null);
  const { session } = useAuth();

  const { mutate: executeQuery, isPending } = useMutation({
    mutationFn: async (req: RAGQueryRequest) => {
      return executeRAGQuery(req, session?.csrf_token);
    },
    onSuccess: (data) => {
      setResult(data);
    },
  });

  const handleExecute = () => {
    if (!query.trim()) return;
    executeQuery({
      query,
      limit: parseInt(limit) || 5,
    });
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Search className="h-5 w-5" />
          Query Tester
        </CardTitle>
        <CardDescription>Test RAG queries across all providers</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-2">
          <Label htmlFor="query">Query</Label>
          <Input
            id="query"
            placeholder="What is retrieval-augmented generation?"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            disabled={isPending}
            onKeyDown={(e) => e.key === "Enter" && handleExecute()}
          />
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-2">
            <Label htmlFor="limit">Results Limit</Label>
            <Input
              id="limit"
              type="number"
              min="1"
              max="50"
              value={limit}
              onChange={(e) => setLimit(e.target.value)}
              disabled={isPending}
            />
          </div>
          <div className="flex items-end">
            <Button
              onClick={handleExecute}
              disabled={!query.trim() || isPending}
              className="w-full"
            >
              {isPending ? (
                <>
                  <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                  Executing...
                </>
              ) : (
                <>
                  <Search className="h-4 w-4 mr-2" />
                  Execute
                </>
              )}
            </Button>
          </div>
        </div>

        {result && (
          <div className="border-t pt-4 space-y-3">
            <div className="grid grid-cols-3 gap-4 text-sm">
              <div>
                <span className="text-muted-foreground">Total Time</span>
                <p className="font-medium">{result.total_time_ms}ms</p>
              </div>
              <div>
                <span className="text-muted-foreground">Providers Queried</span>
                <p className="font-medium">{result.providers_queried}</p>
              </div>
              <div>
                <span className="text-muted-foreground">Cache Hit</span>
                <p className="font-medium">{result.cache_hit ? "Yes ✓" : "No"}</p>
              </div>
            </div>

            <div className="space-y-2">
              <Label className="text-sm font-medium">Results ({result.items.length})</Label>
              <div className="space-y-2 max-h-64 overflow-y-auto">
                {result.items.map((item, i) => (
                  <div key={i} className="p-2 bg-muted rounded border border-border">
                    <div className="flex items-start justify-between mb-1">
                      <p className="text-sm font-medium line-clamp-2">{item.content}</p>
                      <Badge variant="secondary" className="ml-2 flex-shrink-0">
                        {(item.score * 100).toFixed(0)}%
                      </Badge>
                    </div>
                    {item.source_url && (
                      <a href={item.source_url} target="_blank" rel="noopener noreferrer" className="text-xs text-blue-500 hover:underline">
                        {item.source_url}
                      </a>
                    )}
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ── Provider List ─────────────────────────────────────────────────

function ProviderList() {
  const { data, isLoading } = useQuery({
    queryKey: ["rag-providers"],
    queryFn: () => listRAGProviders(),
  });

  if (isLoading) {
    return (
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {[1, 2, 3, 4].map((i) => (
          <Skeleton key={i} className="h-[200px]" />
        ))}
      </div>
    );
  }

  const providers = data?.providers || [];

  if (providers.length === 0) {
    return (
      <Card className="text-center py-8">
        <Database className="h-8 w-8 mx-auto text-muted-foreground mb-2" />
        <p className="text-muted-foreground">No providers registered</p>
        <p className="text-sm text-muted-foreground mt-1">
          Register a provider on the{" "}
          <a href="/app/custom-provider" className="text-blue-500 hover:underline">
            Create Custom RAG Provider
          </a>{" "}
          page.
        </p>
      </Card>
    );
  }

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
      {providers.map((provider) => (
        <ProviderCard key={provider.id} provider={provider} />
      ))}
    </div>
  );
}

// ── RAG Limit Banner ──────────────────────────────────────────────

function RAGLimitBanner({
  current,
  max,
}: {
  current: number;
  max: number;
}) {
  return (
    <div
      data-testid="rag-limit-banner"
      className="flex items-start gap-3 rounded-lg border border-amber-500/30 bg-amber-500/5 px-4 py-3"
    >
      <Lock className="h-4 w-4 mt-0.5 text-amber-600 shrink-0" />
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-amber-700 dark:text-amber-400">
          RAG provider limit reached ({current}/{max})
        </p>
        <p className="text-xs text-amber-600 dark:text-amber-500 mt-0.5">
          The Free tier allows at most {max} RAG provider.{" "}
          <a
            href="https://corvin-labs.com/pricing"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 underline hover:no-underline"
          >
            Upgrade to Member for unlimited providers
            <ExternalLink className="h-3 w-3" />
          </a>
        </p>
      </div>
    </div>
  );
}

// ── Main Page ─────────────────────────────────────────────────────

export default function RAGPage() {
  const { session } = useAuth();

  const { data: licenseInfo } = useQuery({
    queryKey: ["license", "info"],
    queryFn: ({ signal }) => getLicenseInfo(signal),
    staleTime: 60_000,
  });

  const { data: providersData } = useQuery({
    queryKey: ["rag-providers"],
    queryFn: () => listRAGProviders(),
  });

  // Use registered_count (actual YAML files) — not providers.length which may
  // include mock/demo entries returned when the orchestrator is unavailable.
  const providerCount = providersData?.registered_count ?? 0;
  const ragMax =
    typeof licenseInfo?.limits?.rag_providers_max === "number"
      ? (licenseInfo.limits.rag_providers_max as number)
      : null;
  const atRagLimit = ragMax !== null && providerCount >= ragMax;

  if (!session) {
    return <div>Loading...</div>;
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="space-y-2">
        <h1 className="text-3xl font-bold">RAG Integration</h1>
        <p className="text-muted-foreground">
          Manage retrieval-augmented generation providers and test queries
        </p>
      </div>

      {/* Tabs */}
      <Tabs defaultValue="providers" className="space-y-4">
        <TabsList className="grid w-full grid-cols-3">
          <TabsTrigger value="providers" className="flex items-center gap-2">
            <Database className="h-4 w-4" />
            <span className="hidden sm:inline">Providers</span>
            {ragMax !== null && (
              <Badge
                variant="secondary"
                data-testid="rag-limit-badge"
                className="ml-1 text-[10px] px-1.5 py-0"
              >
                {providerCount}/{ragMax}
              </Badge>
            )}
          </TabsTrigger>
          <TabsTrigger value="tester" className="flex items-center gap-2">
            <Search className="h-4 w-4" />
            <span className="hidden sm:inline">Query Tester</span>
          </TabsTrigger>
          <TabsTrigger value="stats" className="flex items-center gap-2">
            <TrendingUp className="h-4 w-4" />
            <span className="hidden sm:inline">Statistics</span>
          </TabsTrigger>
        </TabsList>

        {/* Providers Tab */}
        <TabsContent value="providers" className="space-y-4">
          {atRagLimit && (
            <RAGLimitBanner current={providerCount} max={ragMax!} />
          )}
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between">
                <div>
                  <CardTitle>Registered Providers</CardTitle>
                  <CardDescription>
                    Active RAG knowledge sources
                    {ragMax !== null && (
                      <span className="ml-2 text-xs">
                        ({providerCount}/{ragMax} used)
                      </span>
                    )}
                  </CardDescription>
                </div>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={atRagLimit}
                  data-testid="rag-register-btn"
                  title={
                    atRagLimit
                      ? `Free tier limit reached (${providerCount}/${ragMax})`
                      : "Register a new RAG provider"
                  }
                >
                  {atRagLimit ? (
                    <>
                      <Lock className="h-4 w-4 mr-2 text-amber-500" />
                      Limit Reached
                    </>
                  ) : (
                    <>
                      <Settings className="h-4 w-4 mr-2" />
                      Register New
                    </>
                  )}
                </Button>
              </div>
            </CardHeader>
          </Card>
          <ProviderList />
        </TabsContent>

        {/* Query Tester Tab */}
        <TabsContent value="tester" className="space-y-4">
          <QueryTester />
        </TabsContent>

        {/* Statistics Tab */}
        <TabsContent value="stats" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <TrendingUp className="h-5 w-5" />
                Performance Dashboard
              </CardTitle>
              <CardDescription>Query statistics and provider metrics</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="text-center py-8 text-muted-foreground">
                <p>Statistics will be available after your first query</p>
              </div>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
