import React, { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Search,
  Star,
  Download,
  TrendingUp,
  Filter,
} from "lucide-react";

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
  id: "discover" | "trending" | "top-rated" | "published";
  label: string;
  icon: React.ReactNode;
}

export default function RAGHubPage() {
  const [activeTab, setActiveTab] = useState<RAGHubTab["id"]>("discover");
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedZone, setSelectedZone] = useState("");

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

      const response = await fetch(url.toString(), { credentials: 'include' });
      if (!response.ok) throw new Error("Failed to fetch providers");
      return response.json();
    },
    refetchInterval: 30000, // Poll every 30s
  });

  const tabs: RAGHubTab[] = [
    {
      id: "discover",
      label: "🔍 Discover",
      icon: <Search className="w-4 h-4" />,
    },
    {
      id: "trending",
      label: "🔥 Trending",
      icon: <TrendingUp className="w-4 h-4" />,
    },
    {
      id: "top-rated",
      label: "⭐ Top Rated",
      icon: <Star className="w-4 h-4" />,
    },
  ];

  return (
    <div className="space-y-6 p-6">
      {/* Header */}
      <div>
        <h1 className="text-3xl font-bold">RAG Hub</h1>
        <p className="text-muted-foreground mt-1">
          Discover and import RAG providers published by the community
        </p>
      </div>

      {/* Tabs */}
      <div className="flex border-b border-border space-x-8">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            onClick={() => {
              setActiveTab(tab.id);
              setSearchQuery("");
            }}
            className={`px-4 py-3 font-medium border-b-2 transition ${
              activeTab === tab.id
                ? "border-blue-500 text-blue-600 dark:text-blue-400"
                : "border-transparent text-muted-foreground hover:text-foreground dark:hover:text-foreground"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Search & Filters (Discover tab) */}
      {activeTab === "discover" && (
        <div className="space-y-4">
          {/* Search Input */}
          <div className="relative">
            <Search className="absolute left-3 top-3 w-5 h-5 text-muted-foreground dark:text-muted-foreground" />
            <input
              type="text"
              placeholder="Search providers by name, type, or capability..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full pl-10 pr-4 py-2 border border-input rounded-lg bg-background dark:bg-muted/30 dark:border-input dark:text-foreground dark:placeholder:text-muted-foreground focus:ring-2 focus:ring-blue-500 dark:focus:ring-blue-500 focus:border-transparent"
            />
          </div>

          {/* Filter by Zone */}
          <div className="flex items-center space-x-4">
            <Filter className="w-4 h-4 text-muted-foreground dark:text-muted-foreground" />
            <select
              value={selectedZone}
              onChange={(e) => setSelectedZone(e.target.value)}
              className="px-4 py-2 border border-input rounded-lg bg-background dark:bg-muted/30 dark:border-input dark:text-foreground focus:ring-2 focus:ring-blue-500 dark:focus:ring-blue-500"
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

      {/* Loading State */}
      {isLoading && (
        <div className="text-center py-12">
          <div className="inline-block animate-spin">⏳</div>
          <p className="text-muted-foreground mt-2">Loading providers...</p>
        </div>
      )}

      {/* Providers Grid */}
      {providersData?.providers && providersData.providers.length > 0 ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {providersData.providers.map((provider: RAGHubProvider) => (
            <ProviderCard key={provider.id} provider={provider} />
          ))}
        </div>
      ) : (
        <div className="text-center py-12">
          <p className="text-muted-foreground">
            {searchQuery
              ? "No providers found matching your search"
              : "No providers available"}
          </p>
        </div>
      )}
    </div>
  );
}

// ── Provider Card Component ────────────────────────────────

interface ProviderCardProps {
  provider: RAGHubProvider;
}

function ProviderCard({ provider }: ProviderCardProps) {
  const [showImportDialog, setShowImportDialog] = useState(false);
  const [importLoading, setImportLoading] = useState(false);

  const handleImport = async () => {
    setImportLoading(true);
    try {
      // In real implementation, this would download the manifest and import
      // For now, just show a success message
      alert(`Importing ${provider.id}... (real implementation would fetch manifest)`);
      setShowImportDialog(false);
    } finally {
      setImportLoading(false);
    }
  };

  return (
    <div className="border border-border rounded-lg p-6 hover:shadow-lg transition space-y-4 bg-card dark:bg-card">
      {/* Header */}
      <div>
        <h3 className="font-bold text-lg">{provider.name}</h3>
        <p className="text-sm text-muted-foreground">by {provider.author}</p>
      </div>

      {/* Description */}
      <p className="text-foreground text-sm dark:text-foreground/90">{provider.description}</p>

      {/* Version & Classification */}
      <div className="flex items-center justify-between text-sm">
        <span className="text-muted-foreground">v{provider.version}</span>
        <div className="flex space-x-2">
          <span className="px-2 py-1 bg-blue-100 dark:bg-blue-500/20 text-blue-700 dark:text-blue-300 rounded text-xs font-medium">
            {provider.data_classification}
          </span>
          <span className="px-2 py-1 bg-green-100 dark:bg-green-500/20 text-green-700 dark:text-green-300 rounded text-xs font-medium">
            {provider.compliance_zone}
          </span>
        </div>
      </div>

      {/* Capabilities */}
      <div className="flex flex-wrap gap-2">
        {provider.capabilities.slice(0, 3).map((cap) => (
          <span
            key={cap}
            className="px-2 py-1 bg-muted dark:bg-muted/60 text-foreground dark:text-foreground rounded text-xs"
          >
            {cap}
          </span>
        ))}
        {provider.capabilities.length > 3 && (
          <span className="px-2 py-1 bg-muted dark:bg-muted/60 text-foreground dark:text-foreground rounded text-xs">
            +{provider.capabilities.length - 3} more
          </span>
        )}
      </div>

      {/* Stats */}
      <div className="grid grid-cols-3 gap-4 pt-4 border-t border-border">
        {/* Rating */}
        <div className="text-center">
          <div className="flex items-center justify-center space-x-1">
            <Star className="w-4 h-4 text-yellow-500 fill-yellow-500" />
            <span className="font-bold">{provider.rating}</span>
          </div>
          <p className="text-xs text-muted-foreground">{provider.review_count} reviews</p>
        </div>

        {/* Downloads */}
        <div className="text-center">
          <div className="flex items-center justify-center">
            <Download className="w-4 h-4 text-blue-600 dark:text-blue-400 mr-1" />
            <span className="font-bold">{provider.download_count}</span>
          </div>
          <p className="text-xs text-muted-foreground">downloads</p>
        </div>

        {/* Trending */}
        <div className="text-center">
          <div className="flex items-center justify-center">
            <TrendingUp className="w-4 h-4 text-green-600 dark:text-green-400 mr-1" />
            <span className="font-bold">
              {(provider.trending_score * 100).toFixed(0)}
            </span>
          </div>
          <p className="text-xs text-muted-foreground">trending</p>
        </div>
      </div>

      {/* Import Button */}
      <button
        onClick={() => setShowImportDialog(true)}
        className="w-full bg-blue-600 hover:bg-blue-700 text-white font-medium py-2 rounded-lg transition"
      >
        Import Provider
      </button>

      {/* Import Dialog */}
      {showImportDialog && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-card dark:bg-card rounded-lg p-6 max-w-md w-full space-y-4">
            <h2 className="text-lg font-bold">Import {provider.name}?</h2>
            <p className="text-muted-foreground text-sm">
              This will download and register the provider in your local RAG registry.
            </p>
            <div className="flex space-x-4">
              <button
                onClick={() => setShowImportDialog(false)}
                className="flex-1 px-4 py-2 border border-border rounded-lg hover:bg-muted dark:hover:bg-muted/50 transition text-foreground"
              >
                Cancel
              </button>
              <button
                onClick={handleImport}
                disabled={importLoading}
                className="flex-1 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 dark:bg-blue-600 dark:hover:bg-blue-700 disabled:opacity-50 transition"
              >
                {importLoading ? "Importing..." : "Import"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
