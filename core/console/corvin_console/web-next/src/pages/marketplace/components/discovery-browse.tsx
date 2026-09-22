/**
 * Discovery Browse Component (Phase 1 Session 1).
 *
 * Main marketplace browsing interface with:
 * - Free-text search
 * - Category and tier filtering
 * - Sorting options (relevance, downloads, rating, recency)
 * - Skill grid with cards
 * - Pagination
 * - Pre-curated collections
 */

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  AlertCircle,
  Filter,
  Loader2,
  Search,
  X,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { SkillCard } from "./skill-card";
import { SkillDetailsPanel } from "./skill-details-panel";
import { SkillCollections } from "./skill-collections";
import {
  getCategories,
  getTags,
  searchSkills,
  type SortBy,
} from "../discovery-api";

const SORT_OPTIONS: { value: SortBy; label: string }[] = [
  { value: "relevance", label: "Relevance" },
  { value: "rating", label: "Rating" },
  { value: "downloads", label: "Most Downloaded" },
  { value: "recency", label: "Recently Updated" },
  { value: "name", label: "Name (A-Z)" },
];

const TIER_OPTIONS = [
  { value: "buildin", label: "Built-in" },
  { value: "contributor", label: "Community" },
];

export function DiscoveryBrowse() {
  // Search & filter state
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedCategory, setSelectedCategory] = useState<string | null>(null);
  const [selectedTier, setSelectedTier] = useState<string | null>(null);
  const [selectedTag, setSelectedTag] = useState<string | null>(null);
  const [selectedSort, setSelectedSort] = useState<SortBy>("relevance");
  const [minRating, setMinRating] = useState(0);
  const [page, setPage] = useState(1);

  // Details panel state
  const [selectedPluginId, setSelectedPluginId] = useState<string | null>(null);
  const [detailsOpen, setDetailsOpen] = useState(false);

  // Fetch categories and tags for filters
  const { data: categoriesData } = useQuery({
    queryKey: ["marketplace", "categories"],
    queryFn: () => getCategories(),
  });

  const { data: tagsData } = useQuery({
    queryKey: ["marketplace", "tags"],
    queryFn: () => getTags(),
  });

  // Execute search
  const { data: searchResults, isLoading: isSearching } = useQuery({
    queryKey: [
      "marketplace",
      "search",
      searchQuery,
      selectedCategory,
      selectedTier,
      selectedTag,
      minRating,
      selectedSort,
      page,
    ],
    queryFn: () =>
      searchSkills(searchQuery, {
        category: selectedCategory || undefined,
        tier: selectedTier || undefined,
        tag: selectedTag || undefined,
        min_rating: minRating,
        sort_by: selectedSort,
        limit: 12,
        offset: (page - 1) * 12,
      }),
    keepPreviousData: true,
  });

  // Calculate pagination
  const totalPages = useMemo(() => {
    const total = searchResults?.total || 0;
    return Math.ceil(total / 12);
  }, [searchResults?.total]);

  // Reset to page 1 when filters change
  const handleFilterChange = () => {
    setPage(1);
  };

  const handleCategoryChange = (cat: string | null) => {
    setSelectedCategory(cat);
    handleFilterChange();
  };

  const handleTierChange = (tier: string | null) => {
    setSelectedTier(tier);
    handleFilterChange();
  };

  const handleTagChange = (tag: string | null) => {
    setSelectedTag(tag);
    handleFilterChange();
  };

  const handleRatingChange = (rating: number) => {
    setMinRating(rating);
    handleFilterChange();
  };

  const handleSearch = (query: string) => {
    setSearchQuery(query);
    handleFilterChange();
  };

  // Clear all filters
  const hasActiveFilters =
    searchQuery ||
    selectedCategory ||
    selectedTier ||
    selectedTag ||
    minRating > 0;

  const handleClearFilters = () => {
    setSearchQuery("");
    setSelectedCategory(null);
    setSelectedTier(null);
    setSelectedTag(null);
    setMinRating(0);
    setSelectedSort("relevance");
    setPage(1);
  };

  return (
    <div className="space-y-6">
      {/* Search Bar */}
      <div className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
        <Input
          placeholder="Search skills by name, description, or author..."
          value={searchQuery}
          onChange={(e) => handleSearch(e.target.value)}
          className="pl-10"
        />
      </div>

      {/* Filters */}
      <div className="space-y-3">
        <div className="flex items-center gap-2 flex-wrap">
          {/* Category Filter */}
          <Select
            value={selectedCategory || ""}
            onValueChange={(v) => handleCategoryChange(v || null)}
          >
            <SelectTrigger className="w-40">
              <SelectValue placeholder="Category" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="">All Categories</SelectItem>
              {Object.entries(categoriesData?.categories || {}).map(([cat, count]) => (
                <SelectItem key={cat} value={cat}>
                  {cat} ({count})
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          {/* Tier Filter */}
          <Select
            value={selectedTier || ""}
            onValueChange={(v) => handleTierChange(v || null)}
          >
            <SelectTrigger className="w-40">
              <SelectValue placeholder="Tier" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="">All Tiers</SelectItem>
              {TIER_OPTIONS.map((tier) => (
                <SelectItem key={tier.value} value={tier.value}>
                  {tier.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          {/* Sort */}
          <Select value={selectedSort} onValueChange={(v) => setSelectedSort(v as SortBy)}>
            <SelectTrigger className="w-40">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {SORT_OPTIONS.map((opt) => (
                <SelectItem key={opt.value} value={opt.value}>
                  {opt.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          {/* Min Rating */}
          <Select value={String(minRating)} onValueChange={(v) => handleRatingChange(Number(v))}>
            <SelectTrigger className="w-40">
              <SelectValue placeholder="Min Rating" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="0">All Ratings</SelectItem>
              <SelectItem value="3">3+ stars</SelectItem>
              <SelectItem value="4">4+ stars</SelectItem>
              <SelectItem value="4.5">4.5+ stars</SelectItem>
            </SelectContent>
          </Select>

          {/* Clear Filters */}
          {hasActiveFilters && (
            <Button
              size="sm"
              variant="ghost"
              onClick={handleClearFilters}
              className="gap-1"
            >
              <X className="w-4 h-4" />
              Clear
            </Button>
          )}
        </div>

        {/* Tag Filters (from search results facets) */}
        {selectedTag && (
          <div className="flex items-center gap-2">
            <Badge variant="secondary" className="gap-1">
              {selectedTag}
              <X
                className="w-3 h-3 cursor-pointer"
                onClick={() => handleTagChange(null)}
              />
            </Badge>
          </div>
        )}
      </div>

      {/* Results Count */}
      {searchResults && (
        <div className="text-sm text-gray-600">
          {searchResults.total === 0
            ? "No skills found matching your filters"
            : `${searchResults.total} skill${searchResults.total === 1 ? "" : "s"} found`}
        </div>
      )}

      {/* Error State */}
      {isSearching && !searchResults && (
        <div className="flex items-center justify-center h-64">
          <Loader2 className="w-8 h-8 animate-spin text-gray-400" />
        </div>
      )}

      {/* Results Grid */}
      {searchResults && searchResults.results.length > 0 && (
        <>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {searchResults.results.map((skill) => (
              <SkillCard
                key={skill.id}
                skill={skill}
                onViewDetails={() => {
                  setSelectedPluginId(skill.id);
                  setDetailsOpen(true);
                }}
              />
            ))}
          </div>

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="flex justify-center items-center gap-2 mt-6">
              <Button
                variant="outline"
                onClick={() => setPage(Math.max(1, page - 1))}
                disabled={page === 1}
              >
                Previous
              </Button>
              <span className="text-sm text-gray-600">
                Page {page} of {totalPages}
              </span>
              <Button
                variant="outline"
                onClick={() => setPage(Math.min(totalPages, page + 1))}
                disabled={page === totalPages}
              >
                Next
              </Button>
            </div>
          )}
        </>
      )}

      {/* Empty State */}
      {searchResults && searchResults.results.length === 0 && (
        <Card className="p-8 text-center">
          <AlertCircle className="w-12 h-12 mx-auto text-gray-400 mb-4" />
          <p className="text-gray-600">
            {searchQuery || selectedCategory || selectedTier || selectedTag
              ? "No skills match your search criteria"
              : "Start by searching or browsing categories"}
          </p>
        </Card>
      )}

      {/* Collections Section (below search results) */}
      {!searchQuery && !selectedCategory && !selectedTier && !selectedTag && (
        <div className="mt-12">
          <SkillCollections />
        </div>
      )}

      {/* Details Modal */}
      {selectedPluginId && (
        <SkillDetailsPanel
          pluginId={selectedPluginId}
          open={detailsOpen}
          onOpenChange={setDetailsOpen}
        />
      )}
    </div>
  );
}
