/**
 * Marketplace Discovery API (Phase 1 Session 1).
 *
 * Exposes search, filtering, sorting, and collection discovery endpoints.
 */

import { api } from "@/lib/api/client";

export enum SortBy {
  RELEVANCE = "relevance",
  DOWNLOADS = "downloads",
  RATING = "rating",
  RECENCY = "recency",
  NAME = "name",
}

export interface SkillMetrics {
  downloads: number;
  installs: number;
  rating: number; // 0-5 stars
  review_count: number;
  success_rate: number; // 0-100%
  avg_latency_ms: number;
  last_updated: string; // ISO 8601
}

export interface SkillVersion {
  version: string;
  release_date: string; // ISO 8601
  release_notes: string;
  downloads: number;
  required_version?: string;
}

export interface SkillDetailsResponse {
  id: string;
  name: string;
  version: string;
  author: string;
  description: string;
  long_description: string;
  category: string;
  tier: string;
  license: string;
  tags: string[];
  dependencies: string[];
  requires_version?: string;
  boot_layer?: string;
  sla_level?: string;
  readme_url?: string;
  source_url?: string;
  documentation_url?: string;
  support_url?: string;
  metrics: SkillMetrics;
  versions: SkillVersion[];
  reviews: Record<string, unknown>[];
}

export interface SearchResult {
  id: string;
  name: string;
  description: string;
  version: string;
  author: string;
  tier: string;
  category: string;
  tags?: string[];
  installable: boolean;
  installed: boolean;
  enabled: boolean;
  rating?: number;
  download_count?: number;
}

export interface SearchResponse {
  results: SearchResult[];
  total: number;
  query: {
    q: string;
    category: string | null;
    tier: string | null;
    tag: string | null;
    license: string | null;
    min_rating: number;
    sort_by: string;
    limit: number;
    offset: number;
  };
  facets: {
    categories: Record<string, number>;
    tiers: Record<string, number>;
    tags: Record<string, number>;
  };
}

export interface SkillCollection {
  id: string;
  name: string;
  description: string;
  icon: string;
  skills: string[];
  target_use_case: string;
  difficulty: "beginner" | "intermediate" | "advanced";
  estimated_setup_time_minutes: number;
}

export interface CollectionsResponse {
  collections: SkillCollection[];
}

export interface CategoriesResponse {
  categories: Record<string, number>;
}

export interface TagsResponse {
  tags: Record<string, number>;
}

/**
 * Full-text search with filtering and sorting.
 */
export function searchSkills(
  q: string,
  options?: {
    category?: string;
    tier?: string;
    tag?: string;
    license?: string;
    min_rating?: number;
    sort_by?: SortBy;
    limit?: number;
    offset?: number;
  },
  signal?: AbortSignal
): Promise<SearchResponse> {
  const params = new URLSearchParams();
  if (q) params.append("q", q);
  if (options?.category) params.append("category", options.category);
  if (options?.tier) params.append("tier", options.tier);
  if (options?.tag) params.append("tag", options.tag);
  if (options?.license) params.append("license", options.license);
  if (options?.min_rating !== undefined) params.append("min_rating", String(options.min_rating));
  if (options?.sort_by) params.append("sort_by", options.sort_by);
  if (options?.limit) params.append("limit", String(options.limit));
  if (options?.offset) params.append("offset", String(options.offset));

  const url = `/api/v1/marketplace/search?${params.toString()}`;
  return api<SearchResponse>(url, { signal });
}

/**
 * Get full skill details with version history and reviews.
 */
export function getSkillDetails(
  pluginId: string,
  signal?: AbortSignal
): Promise<SkillDetailsResponse> {
  return api<SkillDetailsResponse>(
    `/api/v1/marketplace/plugins/${encodeURIComponent(pluginId)}`,
    { signal }
  );
}

/**
 * Get pre-curated skill collections.
 */
export function getCollections(signal?: AbortSignal): Promise<CollectionsResponse> {
  return api<CollectionsResponse>("/api/v1/marketplace/collections", { signal });
}

/**
 * Get available categories.
 */
export function getCategories(signal?: AbortSignal): Promise<CategoriesResponse> {
  return api<CategoriesResponse>("/api/v1/marketplace/categories", { signal });
}

/**
 * Get available tags.
 */
export function getTags(signal?: AbortSignal): Promise<TagsResponse> {
  return api<TagsResponse>("/api/v1/marketplace/tags", { signal });
}
