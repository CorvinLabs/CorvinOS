/**
 * Marketplace Hub - Unified Discovery UI
 *
 * ADR-0686: Marketplace Hub Phase 1
 *
 * Features:
 * - 5-card category grid (Skills, Plugins, Tools, Connectors, Layers)
 * - Fuzzy search + multi-facet filtering
 * - Trending & newest sections
 * - Detail modal with drill-down navigation
 * - Responsive layout (mobile/tablet/desktop)
 *
 * License: Apache-2.0
 */

import React, { useState, useEffect, useCallback } from 'react';
import {
  Search,
  Zap,
  Package,
  Wrench,
  Plug,
  Layers,
  Star,
  TrendingUp,
  Clock,
  Filter,
  ChevronRight,
  X,
  Loader,
  AlertCircle,
} from 'lucide-react';

interface DiscoveryItem {
  id: string;
  category: string;
  name: string;
  description: string;
  version: string;
  author: string;
  rating: number;
  rating_count: number;
  tags: string[];
  domain: string;
  tier: string;
  origin: string;
  install_count: number;
  created_at: string;
  updated_at: string;
  is_trending: boolean;
  is_new: boolean;
  badge: string;
}

interface SearchFilters {
  tier?: string;
  domain?: string;
  origin?: string;
  min_rating?: number;
}

interface SearchResult {
  items: DiscoveryItem[];
  total: number;
  page: number;
  per_page: number;
  query: string;
  filters: SearchFilters;
  facets: Record<string, Record<string, number>>;
}

interface HubIndex {
  skills: DiscoveryItem[];
  plugins: DiscoveryItem[];
  tools: DiscoveryItem[];
  connectors: DiscoveryItem[];
  layers: DiscoveryItem[];
  timestamp: string;
  total_count: number;
}

// ============================================================================
// Category Cards (Grid View)
// ============================================================================

interface CategoryCardProps {
  icon: React.ReactNode;
  label: string;
  count: number;
  color: string;
  onClick: () => void;
}

const CategoryCard: React.FC<CategoryCardProps> = ({
  icon,
  label,
  count,
  color,
  onClick,
}) => (
  <div
    onClick={onClick}
    className={`
      p-6 rounded-lg border-2 cursor-pointer transition-all
      hover:shadow-lg hover:border-opacity-100
      ${color}
    `}
  >
    <div className="flex items-center justify-between mb-4">
      <div className="text-4xl opacity-80">{icon}</div>
    </div>
    <h3 className="text-xl font-bold mb-1">{label}</h3>
    <p className="text-sm opacity-70 mb-4">{count} items</p>
    <div className="flex items-center text-sm opacity-70 hover:opacity-100">
      Explore <ChevronRight size={16} className="ml-1" />
    </div>
  </div>
);

// ============================================================================
// Search & Filter Bar
// ============================================================================

interface SearchBarProps {
  onSearch: (query: string) => void;
  onFilterChange: (filters: SearchFilters) => void;
  facets?: Record<string, Record<string, number>>;
  loading?: boolean;
}

const SearchBar: React.FC<SearchBarProps> = ({
  onSearch,
  onFilterChange,
  facets,
  loading,
}) => {
  const [query, setQuery] = useState('');
  const [showFilters, setShowFilters] = useState(false);
  const [filters, setFilters] = useState<SearchFilters>({});

  const handleSearch = useCallback(
    (value: string) => {
      setQuery(value);
      onSearch(value);
    },
    [onSearch]
  );

  const handleFilterChange = (key: keyof SearchFilters, value: any) => {
    const newFilters = { ...filters, [key]: value || undefined };
    setFilters(newFilters);
    onFilterChange(newFilters);
  };

  return (
    <div className="space-y-4 mb-6">
      {/* Search Input */}
      <div className="flex gap-2">
        <div className="flex-1 relative">
          <Search className="absolute left-3 top-3 text-gray-400" size={20} />
          <input
            type="text"
            placeholder="Search skills, plugins, tools, connectors, layers..."
            value={query}
            onChange={(e) => handleSearch(e.target.value)}
            disabled={loading}
            className="
              w-full pl-10 pr-4 py-2 border border-gray-300 rounded-lg
              focus:outline-none focus:ring-2 focus:ring-amber-500
              disabled:opacity-50 disabled:cursor-not-allowed
            "
          />
        </div>
        <button
          onClick={() => setShowFilters(!showFilters)}
          className="
            px-4 py-2 border border-gray-300 rounded-lg
            hover:border-amber-500 hover:text-amber-600
            transition-colors
          "
        >
          <Filter size={20} />
        </button>
      </div>

      {/* Filters Panel */}
      {showFilters && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 p-4 bg-gray-50 rounded-lg border border-gray-200">
          {/* Tier Filter */}
          <div>
            <label className="text-sm font-medium block mb-2">Tier</label>
            <select
              value={filters.tier || ''}
              onChange={(e) => handleFilterChange('tier', e.target.value || undefined)}
              className="w-full px-3 py-1 border border-gray-300 rounded text-sm"
            >
              <option value="">All Tiers</option>
              <option value="compliance">Compliance</option>
              <option value="core">Core</option>
              <option value="installed">Installed</option>
              <option value="builtin">Built-in</option>
              <option value="vetted">Vetted</option>
              <option value="community">Community</option>
            </select>
          </div>

          {/* Origin Filter */}
          <div>
            <label className="text-sm font-medium block mb-2">Origin</label>
            <select
              value={filters.origin || ''}
              onChange={(e) => handleFilterChange('origin', e.target.value || undefined)}
              className="w-full px-3 py-1 border border-gray-300 rounded text-sm"
            >
              <option value="">All Origins</option>
              <option value="builtin">Built-in</option>
              <option value="vetted">Vetted</option>
              <option value="community">Community</option>
            </select>
          </div>

          {/* Rating Filter */}
          <div>
            <label className="text-sm font-medium block mb-2">Min Rating</label>
            <select
              value={filters.min_rating || ''}
              onChange={(e) =>
                handleFilterChange(
                  'min_rating',
                  e.target.value ? parseFloat(e.target.value) : undefined
                )
              }
              className="w-full px-3 py-1 border border-gray-300 rounded text-sm"
            >
              <option value="">Any Rating</option>
              <option value="4">4.0+</option>
              <option value="4.5">4.5+</option>
              <option value="4.8">4.8+</option>
            </select>
          </div>

          {/* Domain Filter */}
          <div>
            <label className="text-sm font-medium block mb-2">Domain</label>
            <input
              type="text"
              placeholder="e.g., routing, learning"
              value={filters.domain || ''}
              onChange={(e) => handleFilterChange('domain', e.target.value || undefined)}
              className="w-full px-3 py-1 border border-gray-300 rounded text-sm"
            />
          </div>
        </div>
      )}
    </div>
  );
};

// ============================================================================
// Item Grid
// ============================================================================

interface ItemGridProps {
  items: DiscoveryItem[];
  onSelect: (item: DiscoveryItem) => void;
  loading?: boolean;
  emptyMessage?: string;
}

const ItemGrid: React.FC<ItemGridProps> = ({
  items,
  onSelect,
  loading,
  emptyMessage,
}) => {
  if (loading) {
    return (
      <div className="flex justify-center items-center py-12">
        <Loader className="animate-spin" size={32} />
      </div>
    );
  }

  if (items.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-12 text-gray-400">
        <AlertCircle size={48} className="mb-2" />
        <p>{emptyMessage || 'No items found'}</p>
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
      {items.map((item) => (
        <div
          key={`${item.category}-${item.id}`}
          onClick={() => onSelect(item)}
          className="
            p-4 border border-gray-200 rounded-lg cursor-pointer
            hover:shadow-lg hover:border-amber-500 transition-all
            hover:bg-amber-50
          "
        >
          {/* Header */}
          <div className="flex items-start justify-between mb-2">
            <div className="flex-1">
              <h4 className="font-bold text-sm">{item.name}</h4>
              <p className="text-xs text-gray-500">{item.version}</p>
            </div>
            {item.badge && (
              <span className="text-xs bg-amber-100 text-amber-700 px-2 py-1 rounded">
                {item.badge}
              </span>
            )}
          </div>

          {/* Description */}
          <p className="text-sm text-gray-600 mb-3 line-clamp-2">
            {item.description}
          </p>

          {/* Meta */}
          <div className="flex items-center gap-4 mb-3 text-xs text-gray-500">
            <div className="flex items-center gap-1">
              <Star size={12} className="text-yellow-500" />
              <span>
                {item.rating.toFixed(1)} ({item.rating_count})
              </span>
            </div>
            <div>{item.install_count.toLocaleString()} installs</div>
          </div>

          {/* Tags */}
          {item.tags.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {item.tags.slice(0, 3).map((tag) => (
                <span
                  key={tag}
                  className="text-xs bg-gray-100 text-gray-600 px-2 py-1 rounded"
                >
                  {tag}
                </span>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
};

// ============================================================================
// Detail Modal
// ============================================================================

interface DetailModalProps {
  item?: DiscoveryItem;
  onClose: () => void;
  onDrillDown: (item: DiscoveryItem) => void;
}

const DetailModal: React.FC<DetailModalProps> = ({ item, onClose, onDrillDown }) => {
  if (!item) return null;

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-lg max-w-2xl w-full max-h-96 overflow-y-auto">
        {/* Header */}
        <div className="sticky top-0 bg-white border-b border-gray-200 p-6 flex items-start justify-between">
          <div>
            <h2 className="text-2xl font-bold">{item.name}</h2>
            <p className="text-sm text-gray-500 mt-1">
              {item.category} • v{item.version} • by {item.author}
            </p>
          </div>
          <button
            onClick={onClose}
            className="p-2 hover:bg-gray-100 rounded transition-colors"
          >
            <X size={20} />
          </button>
        </div>

        {/* Content */}
        <div className="p-6 space-y-4">
          <p className="text-gray-700">{item.description}</p>

          {/* Stats */}
          <div className="grid grid-cols-3 gap-4">
            <div className="bg-gray-50 p-3 rounded">
              <div className="text-sm text-gray-500">Rating</div>
              <div className="text-xl font-bold">
                {item.rating.toFixed(1)} <span className="text-xs text-yellow-500">★</span>
              </div>
              <div className="text-xs text-gray-400">{item.rating_count} reviews</div>
            </div>
            <div className="bg-gray-50 p-3 rounded">
              <div className="text-sm text-gray-500">Installs</div>
              <div className="text-xl font-bold">{(item.install_count / 1000).toFixed(1)}k</div>
            </div>
            <div className="bg-gray-50 p-3 rounded">
              <div className="text-sm text-gray-500">Updated</div>
              <div className="text-xs font-bold">
                {new Date(item.updated_at).toLocaleDateString()}
              </div>
            </div>
          </div>

          {/* Tags */}
          {item.tags.length > 0 && (
            <div>
              <div className="text-sm font-medium mb-2">Tags</div>
              <div className="flex flex-wrap gap-2">
                {item.tags.map((tag) => (
                  <span key={tag} className="bg-amber-100 text-amber-700 px-3 py-1 rounded-full text-sm">
                    {tag}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Details */}
          <div className="grid grid-cols-2 gap-4 text-sm">
            <div>
              <div className="text-gray-500">Tier</div>
              <div className="font-medium">{item.tier}</div>
            </div>
            <div>
              <div className="text-gray-500">Origin</div>
              <div className="font-medium">{item.origin}</div>
            </div>
            <div>
              <div className="text-gray-500">Domain</div>
              <div className="font-medium">{item.domain || 'N/A'}</div>
            </div>
            <div>
              <div className="text-gray-500">Created</div>
              <div className="font-medium">{new Date(item.created_at).toLocaleDateString()}</div>
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="sticky bottom-0 bg-gray-50 border-t border-gray-200 p-6 flex gap-3 justify-end">
          <button
            onClick={onClose}
            className="px-4 py-2 border border-gray-300 rounded-lg hover:bg-gray-100"
          >
            Close
          </button>
          <button
            onClick={() => {
              onDrillDown(item);
              onClose();
            }}
            className="px-4 py-2 bg-amber-500 text-white rounded-lg hover:bg-amber-600 flex items-center gap-2"
          >
            Open in {item.category.slice(0, -1)} <ChevronRight size={16} />
          </button>
        </div>
      </div>
    </div>
  );
};

// ============================================================================
// Main Marketplace Hub Component
// ============================================================================

export const MarketplaceHub: React.FC = () => {
  const [view, setView] = useState<'grid' | 'search' | 'trending' | 'newest'>('grid');
  const [index, setIndex] = useState<HubIndex | null>(null);
  const [searchResults, setSearchResults] = useState<SearchResult | null>(null);
  const [trendingItems, setTrendingItems] = useState<DiscoveryItem[]>([]);
  const [newestItems, setNewestItems] = useState<DiscoveryItem[]>([]);
  const [selectedItem, setSelectedItem] = useState<DiscoveryItem | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Load full index on mount
  useEffect(() => {
    const loadIndex = async () => {
      try {
        setLoading(true);
        const res = await fetch('/v1/marketplace/hub/index');
        if (!res.ok) throw new Error('Failed to load hub index');
        const data = await res.json();
        setIndex(data);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Unknown error');
      } finally {
        setLoading(false);
      }
    };

    loadIndex();
  }, []);

  // Load trending
  useEffect(() => {
    const loadTrending = async () => {
      try {
        const res = await fetch('/v1/marketplace/hub/trending?limit=10');
        if (!res.ok) throw new Error('Failed to load trending');
        const data = await res.json();
        setTrendingItems(data.items || []);
      } catch (err) {
        console.error('Error loading trending:', err);
      }
    };

    loadTrending();
  }, []);

  // Load newest
  useEffect(() => {
    const loadNewest = async () => {
      try {
        const res = await fetch('/v1/marketplace/hub/newest?limit=10');
        if (!res.ok) throw new Error('Failed to load newest');
        const data = await res.json();
        setNewestItems(data.items || []);
      } catch (err) {
        console.error('Error loading newest:', err);
      }
    };

    loadNewest();
  }, []);

  // Search
  const handleSearch = useCallback(async (query: string, filters?: SearchFilters) => {
    try {
      setLoading(true);
      const params = new URLSearchParams({ q: query, page: '1', per_page: '20' });
      if (filters?.tier) params.append('tier', filters.tier);
      if (filters?.domain) params.append('domain', filters.domain);
      if (filters?.origin) params.append('origin', filters.origin);
      if (filters?.min_rating) params.append('min_rating', filters.min_rating.toString());

      const res = await fetch(`/v1/marketplace/hub/search?${params}`);
      if (!res.ok) throw new Error('Search failed');
      const data = await res.json();
      setSearchResults(data);
      setView('search');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Search failed');
    } finally {
      setLoading(false);
    }
  }, []);

  // Drill down
  const handleDrillDown = async (item: DiscoveryItem) => {
    try {
      const res = await fetch('/v1/marketplace/hub/drill-down', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          subsystem_type: item.category,
          item_id: item.id,
          target_page: 'detail',
        }),
      });

      if (res.ok) {
        const data = await res.json();
        window.location.href = data.url;
      }
    } catch (err) {
      setError('Drill-down failed');
    }
  };

  return (
    <div className="w-full max-w-7xl mx-auto p-4 md:p-6">
      {/* Header */}
      <div className="mb-8">
        <h1 className="text-3xl md:text-4xl font-bold mb-2">Marketplace Hub</h1>
        <p className="text-gray-600">
          Discover skills, plugins, tools, connectors, and layers
        </p>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg mb-4">
          {error}
        </div>
      )}

      {/* View: Grid */}
      {view === 'grid' && index && !loading && (
        <>
          {/* Category Cards */}
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-4 mb-8">
            <CategoryCard
              icon={<Zap />}
              label="Skills"
              count={index.skills.length}
              color="border-amber-200 bg-amber-50 hover:bg-amber-100"
              onClick={() => setView('search')}
            />
            <CategoryCard
              icon={<Package />}
              label="Plugins"
              count={index.plugins.length}
              color="border-blue-200 bg-blue-50 hover:bg-blue-100"
              onClick={() => setView('search')}
            />
            <CategoryCard
              icon={<Wrench />}
              label="Tools"
              count={index.tools.length}
              color="border-green-200 bg-green-50 hover:bg-green-100"
              onClick={() => setView('search')}
            />
            <CategoryCard
              icon={<Plug />}
              label="Connectors"
              count={index.connectors.length}
              color="border-purple-200 bg-purple-50 hover:bg-purple-100"
              onClick={() => setView('search')}
            />
            <CategoryCard
              icon={<Layers />}
              label="Layers"
              count={index.layers.length}
              color="border-red-200 bg-red-50 hover:bg-red-100"
              onClick={() => setView('search')}
            />
          </div>

          {/* Trending Section */}
          {trendingItems.length > 0 && (
            <div className="mb-8">
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-2xl font-bold flex items-center gap-2">
                  <TrendingUp size={24} className="text-amber-500" />
                  Trending Now
                </h2>
                <button
                  onClick={() => setView('trending')}
                  className="text-amber-600 hover:text-amber-700"
                >
                  View all
                </button>
              </div>
              <ItemGrid
                items={trendingItems.slice(0, 6)}
                onSelect={setSelectedItem}
              />
            </div>
          )}

          {/* Newest Section */}
          {newestItems.length > 0 && (
            <div>
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-2xl font-bold flex items-center gap-2">
                  <Clock size={24} className="text-blue-500" />
                  Newly Added
                </h2>
                <button
                  onClick={() => setView('newest')}
                  className="text-blue-600 hover:text-blue-700"
                >
                  View all
                </button>
              </div>
              <ItemGrid
                items={newestItems.slice(0, 6)}
                onSelect={setSelectedItem}
              />
            </div>
          )}
        </>
      )}

      {/* View: Search */}
      {(view === 'search' || view === 'trending' || view === 'newest') && (
        <>
          <div className="mb-6">
            <button
              onClick={() => setView('grid')}
              className="text-amber-600 hover:text-amber-700 font-medium"
            >
              ← Back to Grid
            </button>
          </div>

          {view === 'search' && (
            <SearchBar
              onSearch={(q) => handleSearch(q)}
              onFilterChange={(filters) => {
                if (searchResults?.query) {
                  handleSearch(searchResults.query, filters);
                }
              }}
              facets={searchResults?.facets}
              loading={loading}
            />
          )}

          {view === 'search' && searchResults && (
            <ItemGrid
              items={searchResults.items}
              onSelect={setSelectedItem}
              loading={loading}
              emptyMessage={
                searchResults.query
                  ? `No items found for "${searchResults.query}"`
                  : 'Try searching for something...'
              }
            />
          )}

          {view === 'trending' && (
            <ItemGrid
              items={trendingItems}
              onSelect={setSelectedItem}
              emptyMessage="No trending items"
            />
          )}

          {view === 'newest' && (
            <ItemGrid
              items={newestItems}
              onSelect={setSelectedItem}
              emptyMessage="No recent items"
            />
          )}
        </>
      )}

      {/* Detail Modal */}
      <DetailModal
        item={selectedItem || undefined}
        onClose={() => setSelectedItem(null)}
        onDrillDown={handleDrillDown}
      />
    </div>
  );
};

export default MarketplaceHub;
