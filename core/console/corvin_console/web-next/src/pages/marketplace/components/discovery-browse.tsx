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
import { Select } from "@/components/ui/select";
import { SkillCard } from "./skill-card";
import { SkillDetailsPanel } from "./skill-details-panel";
import { SkillCollections } from "./skill-collections";
import {
  getCategories,
  getTags,
  searchSkills,
  SortBy,
  type SearchResponse,
} from "../discovery-api";

// SortBy is a TS enum, not a string union, so its members are not
// interchangeable with their own string values.
const SORT_OPTIONS: { value: SortBy; label: string }[] = [
  { value: SortBy.RELEVANCE, label: "Relevance" },
  { value: SortBy.RATING, label: "Rating" },
  { value: SortBy.DOWNLOADS, label: "Most Downloaded" },
  { value: SortBy.RECENCY, label: "Recently Updated" },
  { value: SortBy.NAME, label: "Name (A-Z)" },
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
  const [selectedSort, setSelectedSort] = useState<SortBy>(SortBy.RELEVANCE);
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
    // react-query v5 dropped keepPreviousData; this is the idiom used elsewhere
    // in this console (see pages/initiatives.tsx). The stale v4 option did not
    // merely fail to hold rows across a filter change - an unknown key makes the
    // overload resolution fail, which typed the whole query as `unknown` and is
    // where every downstream `.total` / `.results` error came from.
    placeholderData: (prev: SearchResponse | undefined) => prev,
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
    setSelectedSort(SortBy.RELEVANCE);
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
          {/*
            components/ui/select.tsx is a native <select> wrapper, not Radix: it
            takes value/onChange and plain <option> children. The Radix-shaped
            composition these filters used (Select > SelectTrigger > SelectValue
            + SelectContent > SelectItem) would have rendered a <select> nested
            inside a <select> — so this was never only a type error.
          */}
          {/* Category Filter */}
          <Select
            className="w-40"
            aria-label="Category"
            value={selectedCategory || ""}
            onChange={(e) => handleCategoryChange(e.target.value || null)}
          >
            <option value="">All Categories</option>
            {Object.entries(categoriesData?.categories || {}).map(([cat, count]) => (
              <option key={cat} value={cat}>
                {cat} ({count})
              </option>
            ))}
          </Select>

          {/* Tier Filter */}
          <Select
            className="w-40"
            aria-label="Tier"
            value={selectedTier || ""}
            onChange={(e) => handleTierChange(e.target.value || null)}
          >
            <option value="">All Tiers</option>
            {TIER_OPTIONS.map((tier) => (
              <option key={tier.value} value={tier.value}>
                {tier.label}
              </option>
            ))}
          </Select>

          {/* Sort */}
          <Select
            className="w-40"
            aria-label="Sort by"
            value={selectedSort}
            onChange={(e) => setSelectedSort(e.target.value as SortBy)}
          >
            {SORT_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </Select>

          {/* Min Rating */}
          <Select
            className="w-40"
            aria-label="Minimum rating"
            value={String(minRating)}
            onChange={(e) => handleRatingChange(Number(e.target.value))}
          >
            <option value="0">All Ratings</option>
            <option value="3">3+ stars</option>
            <option value="4">4+ stars</option>
            <option value="4.5">4.5+ stars</option>
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
