/**
 * Marketplace Hub — Search & Filter UI (ADR-0691)
 *
 * Components:
 * - SearchInput: text query field
 * - FilterPanel: type & status filters
 * - ResultsTable: sortable results with filtering
 *
 * Session 4 Milestone C
 *
 * NOTE: Uses Tailwind CSS + lucide-react (consistent with project design system).
 * Refactored from Mantine/Tabler to reduce dependencies.
 */

import React, { useState, useMemo } from 'react';
import {
  Search,
  X,
  ChevronUp,
  ChevronDown,
  Filter,
} from 'lucide-react';

// ============================================================================
// TYPE DEFINITIONS
// ============================================================================

export type ContentType = 'plugin' | 'skill' | 'dataset' | 'service' | 'template';
export type SortOrder = 'asc' | 'desc';
export type SortField = 'name' | 'type' | 'updated' | 'rating';

export interface SearchResult {
  id: string;
  name: string;
  type: ContentType;
  description?: string;
  updated?: string;
  rating?: number;
  status?: 'active' | 'inactive';
}

export interface SearchFilters {
  types: ContentType[];
  statuses: ('active' | 'inactive')[];
  sortField: SortField;
  sortOrder: SortOrder;
}

export interface MarketplaceSearchProps {
  data: SearchResult[];
  onResultSelect: (result: SearchResult) => void;
}

// ============================================================================
// HELPER: Badge Component
// ============================================================================

const Badge: React.FC<{ children: React.ReactNode; color?: string; size?: 'sm' | 'md' }> = ({
  children,
  color = 'gray',
  size = 'sm',
}) => {
  const colorClasses = {
    gray: 'bg-gray-100 text-gray-800',
    green: 'bg-green-100 text-green-800',
    red: 'bg-red-100 text-red-800',
  }[color] || 'bg-gray-100 text-gray-800';

  const sizeClasses = {
    sm: 'px-2 py-0.5 text-xs',
    md: 'px-3 py-1 text-sm',
  }[size];

  return <span className={`inline-block rounded-full font-medium ${colorClasses} ${sizeClasses}`}>{children}</span>;
};

// ============================================================================
// COMPONENT: SEARCH INPUT
// ============================================================================

export const SearchInput: React.FC<{
  value: string;
  onChange: (value: string) => void;
  onClear: () => void;
}> = ({ value, onChange, onClear }) => (
  <div className="relative mb-4">
    <Search className="absolute left-3 top-2.5 text-gray-400" size={16} />
    <input
      data-testid="search-query"
      type="text"
      placeholder="Find plugins, skills, datasets, services, templates…"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="w-full pl-10 pr-10 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
    />
    {value && (
      <button
        onClick={onClear}
        className="absolute right-3 top-2.5 text-gray-400 hover:text-gray-600"
      >
        <X size={14} />
      </button>
    )}
  </div>
);

// ============================================================================
// COMPONENT: FILTER PANEL
// ============================================================================

export const FilterPanel: React.FC<{
  filters: SearchFilters;
  onFilterChange: (filters: SearchFilters) => void;
  onOpen?: () => void;
  isDrawer?: boolean;
}> = ({ filters, onFilterChange, onOpen, isDrawer = false }) => {
  const typeOptions = [
    { label: 'Plugin', value: 'plugin' },
    { label: 'Skill', value: 'skill' },
    { label: 'Dataset', value: 'dataset' },
    { label: 'Service', value: 'service' },
    { label: 'Template', value: 'template' },
  ];

  const statusOptions = [
    { value: 'active', label: 'Active' },
    { value: 'inactive', label: 'Inactive' },
  ];

  const sortOptions = [
    { value: 'name', label: 'Name' },
    { value: 'type', label: 'Type' },
    { value: 'updated', label: 'Recently Updated' },
    { value: 'rating', label: 'Rating' },
  ];

  const content = (
    <div className="space-y-4">
      {/* Content Type Filter */}
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-2">Content Type</label>
        <div className="space-y-2">
          {typeOptions.map((option) => (
            <label key={option.value} className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                data-testid={`filter-type-${option.value}`}
                checked={filters.types.includes(option.value as ContentType)}
                onChange={(e) => {
                  const newTypes = e.currentTarget.checked
                    ? [...filters.types, option.value as ContentType]
                    : filters.types.filter((t) => t !== option.value);
                  onFilterChange({ ...filters, types: newTypes });
                }}
                className="w-4 h-4 text-blue-600 rounded"
              />
              <span className="text-sm text-gray-700">{option.label}</span>
            </label>
          ))}
        </div>
      </div>

      {/* Status Filter */}
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-2">Status</label>
        <div className="space-y-2">
          {statusOptions.map((option) => (
            <label key={option.value} className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={filters.statuses.includes(option.value as any)}
                onChange={(e) => {
                  const newStatuses = e.currentTarget.checked
                    ? [...filters.statuses, option.value as any]
                    : filters.statuses.filter((s) => s !== option.value);
                  onFilterChange({ ...filters, statuses: newStatuses });
                }}
                className="w-4 h-4 text-blue-600 rounded"
              />
              <span className="text-sm text-gray-700">{option.label}</span>
            </label>
          ))}
        </div>
      </div>

      {/* Sort By */}
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-2">Sort By</label>
        <select
          value={filters.sortField}
          onChange={(e) => onFilterChange({ ...filters, sortField: e.target.value as SortField })}
          className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
        >
          {sortOptions.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </div>

      {/* Clear Filters Button */}
      <button
        onClick={() => {
          onFilterChange({
            types: [],
            statuses: [],
            sortField: 'name',
            sortOrder: 'asc',
          });
        }}
        className="w-full px-4 py-2 bg-gray-100 text-gray-900 rounded-lg hover:bg-gray-200 transition text-sm font-medium"
      >
        Clear Filters
      </button>
    </div>
  );

  if (isDrawer) {
    return content;
  }

  return <div className="border-r border-gray-200 pr-4 min-w-60">{content}</div>;
};

// ============================================================================
// COMPONENT: RESULTS TABLE
// ============================================================================

export const ResultsTable: React.FC<{
  data: SearchResult[];
  onRowClick: (result: SearchResult) => void;
}> = ({ data, onRowClick }) => {
  return (
    <div className="overflow-x-auto border border-gray-200 rounded-lg" data-testid="results-table">
      <table className="w-full text-sm">
        <thead className="bg-gray-50 border-b border-gray-200">
          <tr>
            <th className="text-left px-4 py-2 font-medium text-gray-900">Name</th>
            <th className="text-left px-4 py-2 font-medium text-gray-900">Type</th>
            <th className="text-left px-4 py-2 font-medium text-gray-900">Updated</th>
            <th className="text-left px-4 py-2 font-medium text-gray-900">Rating</th>
            <th className="text-left px-4 py-2 font-medium text-gray-900">Status</th>
          </tr>
        </thead>
        <tbody>
          {data.map((item) => (
            <tr
              key={item.id}
              onClick={() => onRowClick(item)}
              className="border-b border-gray-200 hover:bg-gray-50 cursor-pointer transition"
            >
              <td className="px-4 py-2 font-medium text-gray-900">{item.name}</td>
              <td className="px-4 py-2">
                <Badge size="sm">{item.type}</Badge>
              </td>
              <td className="px-4 py-2 text-gray-600">{item.updated || '—'}</td>
              <td className="px-4 py-2 text-gray-600">{item.rating ? `${item.rating}★` : '—'}</td>
              <td className="px-4 py-2">
                {item.status && (
                  <Badge size="sm" color={item.status === 'active' ? 'green' : 'red'}>
                    {item.status}
                  </Badge>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};

// ============================================================================
// COMPONENT: MARKETPLACE SEARCH (FULL PAGE)
// ============================================================================

export const MarketplaceSearch: React.FC<MarketplaceSearchProps> = ({ data, onResultSelect }) => {
  const [searchQuery, setSearchQuery] = useState('');
  const [filters, setFilters] = useState<SearchFilters>({
    types: [],
    statuses: [],
    sortField: 'name',
    sortOrder: 'asc',
  });
  const [drawerOpen, setDrawerOpen] = useState(false);

  // Filter & sort results
  const filteredResults = useMemo(() => {
    let results = data;

    // Text search
    if (searchQuery) {
      const query = searchQuery.toLowerCase();
      results = results.filter((item) => item.name.toLowerCase().includes(query));
    }

    // Type filter
    if (filters.types.length > 0) {
      results = results.filter((item) => filters.types.includes(item.type));
    }

    // Status filter
    if (filters.statuses.length > 0 && results[0]?.status) {
      results = results.filter((item) => filters.statuses.includes(item.status as any));
    }

    // Sort
    results.sort((a, b) => {
      let aVal = a[filters.sortField] ?? '';
      let bVal = b[filters.sortField] ?? '';

      // Convert to string for comparison if needed
      if (typeof aVal === 'number') aVal = String(aVal);
      if (typeof bVal === 'number') bVal = String(bVal);

      if (typeof aVal === 'string' && typeof bVal === 'string') {
        aVal = aVal.toLowerCase();
        bVal = bVal.toLowerCase();
      }

      const comparison = aVal < bVal ? -1 : aVal > bVal ? 1 : 0;
      return filters.sortOrder === 'asc' ? comparison : -comparison;
    });

    return results;
  }, [data, searchQuery, filters]);

  return (
    <div className="w-full" data-testid="marketplace-search">
      <div className="space-y-4">
        {/* Search Input */}
        <SearchInput
          value={searchQuery}
          onChange={setSearchQuery}
          onClear={() => setSearchQuery('')}
        />

        {/* Desktop Layout: Filters + Results */}
        <div className="flex gap-4">
          {/* Desktop Filters (hidden on mobile) */}
          <div className="hidden md:block min-w-60">
            <FilterPanel filters={filters} onFilterChange={setFilters} />
          </div>

          {/* Results */}
          <div className="flex-1" data-testid="card-grid">
            <div className="flex items-center justify-between mb-4">
              <Badge>{filteredResults.length} results</Badge>
              <button
                onClick={() => setDrawerOpen(true)}
                className="md:hidden inline-flex items-center gap-2 px-3 py-2 bg-gray-100 text-gray-900 rounded-lg hover:bg-gray-200 transition text-sm font-medium"
              >
                <Filter size={14} />
                Filters
              </button>
            </div>

            {filteredResults.length > 0 ? (
              <ResultsTable data={filteredResults} onRowClick={onResultSelect} />
            ) : (
              <div className="text-center py-8">
                <p className="text-gray-600">No results found</p>
              </div>
            )}
          </div>
        </div>

        {/* Mobile Filter Drawer */}
        {drawerOpen && (
          <div className="fixed inset-0 z-50 bg-black/50">
            <div className="absolute bottom-0 left-0 right-0 bg-white rounded-t-lg p-4 max-h-[80vh] overflow-y-auto">
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-lg font-semibold text-gray-900">Filters</h2>
                <button
                  onClick={() => setDrawerOpen(false)}
                  className="text-gray-400 hover:text-gray-600"
                >
                  <X size={20} />
                </button>
              </div>
              <FilterPanel filters={filters} onFilterChange={setFilters} isDrawer={true} />
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default MarketplaceSearch;
