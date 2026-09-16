/**
 * Skill Marketplace Discovery Panel
 *
 * ADR-0682 Phase 6: Marketplace Discovery
 * - Search with fuzzy matching
 * - Multi-facet filtering (tier, domain, rating)
 * - Sorting (popularity, rating, recency, name)
 * - Install with progress tracking
 * - Reviews panel
 */

import React, { useState, useEffect, useCallback } from 'react';
import { Search, Star, Download, Filter, Loader, AlertCircle } from 'lucide-react';

interface Skill {
  skill_id: string;
  name: string;
  version: string;
  short_description: string;
  domain: string;
  tier: 'compliance' | 'core' | 'installed';
  origin: 'builtin' | 'vetted' | 'community';
  rating: number;
  rating_count: number;
  install_count: number;
  created_at: string;
  updated_at: string;
  tags: string[];
}

interface SkillDetail extends Skill {
  full_description: string;
  dependencies: string[];
  author: string;
  homepage_url?: string;
  repository_url?: string;
  license: string;
  reviews: Review[];
}

interface Review {
  reviewer: string;
  rating: number;
  comment: string;
  created_at: string;
}

interface InstallJob {
  job_id: string;
  status: 'installing' | 'complete' | 'error';
  progress: number;
  skill_id: string;
  version: string;
  logs: string[];
  result?: any;
}

// ============================================================================
// SearchBar Component
// ============================================================================

const SearchBar: React.FC<{
  onSearch: (query: string) => void;
  onFilterChange: (filters: FilterState) => void;
}> = ({ onSearch, onFilterChange }) => {
  const [query, setQuery] = useState('');
  const [showFilters, setShowFilters] = useState(false);
  const [filters, setFilters] = useState<FilterState>({
    tier: undefined,
    domain: undefined,
    min_rating: 0,
  });

  const handleSearch = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const value = e.target.value;
      setQuery(value);
      onSearch(value);
    },
    [onSearch]
  );

  const handleFilterChange = (key: string, value: any) => {
    const newFilters = { ...filters, [key]: value };
    setFilters(newFilters);
    onFilterChange(newFilters);
  };

  return (
    <div className="flex flex-col gap-4 bg-white p-6 rounded-lg border border-gray-200">
      {/* Search Input */}
      <div className="flex gap-2">
        <div className="flex-1 relative">
          <Search className="absolute left-3 top-3 text-gray-400" size={20} />
          <input
            type="text"
            placeholder="Search skills..."
            value={query}
            onChange={handleSearch}
            className="w-full pl-10 pr-4 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-amber-500"
          />
        </div>
        <button
          onClick={() => setShowFilters(!showFilters)}
          className="flex items-center gap-2 px-4 py-2 border border-gray-300 rounded-lg hover:bg-gray-50"
        >
          <Filter size={18} />
          Filters
        </button>
      </div>

      {/* Filter Panel */}
      {showFilters && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 p-4 bg-gray-50 rounded-lg">
          {/* Tier Filter */}
          <div>
            <label className="text-sm font-semibold text-gray-700">Tier</label>
            <select
              value={filters.tier || ''}
              onChange={(e) => handleFilterChange('tier', e.target.value || undefined)}
              className="w-full mt-1 px-3 py-2 border border-gray-300 rounded-lg text-sm"
            >
              <option value="">All Tiers</option>
              <option value="compliance">Compliance</option>
              <option value="core">Core</option>
              <option value="installed">Installed</option>
            </select>
          </div>

          {/* Domain Filter */}
          <div>
            <label className="text-sm font-semibold text-gray-700">Domain</label>
            <input
              type="text"
              placeholder="e.g., automation"
              value={filters.domain || ''}
              onChange={(e) => handleFilterChange('domain', e.target.value || undefined)}
              className="w-full mt-1 px-3 py-2 border border-gray-300 rounded-lg text-sm"
            />
          </div>

          {/* Rating Filter */}
          <div>
            <label className="text-sm font-semibold text-gray-700">
              Min Rating: {filters.min_rating.toFixed(1)}
            </label>
            <input
              type="range"
              min="0"
              max="5"
              step="0.5"
              value={filters.min_rating}
              onChange={(e) => handleFilterChange('min_rating', parseFloat(e.target.value))}
              className="w-full mt-1"
            />
          </div>
        </div>
      )}
    </div>
  );
};

// ============================================================================
// SkillCard Component
// ============================================================================

const SkillCard: React.FC<{
  skill: Skill;
  onSelect: (skill: Skill) => void;
}> = ({ skill, onSelect }) => {
  const tierColors: Record<string, string> = {
    compliance: 'bg-red-100 text-red-800',
    core: 'bg-blue-100 text-blue-800',
    installed: 'bg-green-100 text-green-800',
  };

  const originIcons: Record<string, string> = {
    builtin: '⚙️',
    vetted: '✅',
    community: '👥',
  };

  return (
    <div
      onClick={() => onSelect(skill)}
      className="p-4 border border-gray-200 rounded-lg hover:shadow-lg hover:border-amber-300 cursor-pointer transition-all"
    >
      <div className="flex justify-between items-start mb-2">
        <div>
          <h3 className="text-lg font-semibold text-gray-900">{skill.name}</h3>
          <p className="text-sm text-gray-600">{skill.version}</p>
        </div>
        <span className="text-xl">{originIcons[skill.origin] || '📦'}</span>
      </div>

      <p className="text-sm text-gray-600 mb-3 line-clamp-2">{skill.short_description}</p>

      <div className="flex gap-2 mb-3">
        <span className={`px-2 py-1 rounded text-xs font-semibold ${tierColors[skill.tier] || 'bg-gray-100'}`}>
          {skill.tier}
        </span>
        <span className="px-2 py-1 bg-gray-100 text-gray-800 rounded text-xs font-semibold">
          {skill.domain}
        </span>
      </div>

      <div className="flex items-center justify-between text-sm">
        <div className="flex items-center gap-1">
          <Star size={14} className="text-amber-500 fill-amber-500" />
          <span className="font-semibold">{skill.rating.toFixed(1)}</span>
          <span className="text-gray-500">({skill.rating_count})</span>
        </div>
        <span className="text-gray-600">📥 {skill.install_count}</span>
      </div>
    </div>
  );
};

// ============================================================================
// SkillDetailModal Component
// ============================================================================

const SkillDetailModal: React.FC<{
  skill: SkillDetail | null;
  onClose: () => void;
  onInstall: (skill_id: string, version: string) => void;
  installing: boolean;
}> = ({ skill, onClose, onInstall, installing }) => {
  if (!skill) return null;

  return (
    <div className="fixed inset-0 z-50 overflow-y-auto">
      <div className="flex items-center justify-center min-h-screen px-4 py-4">
        {/* Overlay */}
        <div
          className="absolute inset-0 bg-black bg-opacity-50"
          onClick={onClose}
        ></div>

        {/* Modal */}
        <div className="relative bg-white rounded-lg shadow-xl max-w-2xl w-full max-h-[90vh] overflow-y-auto">
          <div className="p-6">
            {/* Header */}
            <div className="mb-4">
              <div className="flex justify-between items-start mb-2">
                <div>
                  <h2 className="text-2xl font-bold text-gray-900">{skill.name}</h2>
                  <p className="text-gray-600">v{skill.version} by {skill.author}</p>
                </div>
                <button
                  onClick={onClose}
                  className="text-gray-500 hover:text-gray-700 text-2xl"
                >
                  ✕
                </button>
              </div>
            </div>

            {/* Description */}
            <div className="mb-6">
              <h3 className="text-lg font-semibold mb-2">Description</h3>
              <p className="text-gray-700">{skill.full_description}</p>
            </div>

            {/* Metadata */}
            <div className="grid grid-cols-2 gap-4 mb-6 p-4 bg-gray-50 rounded-lg">
              <div>
                <label className="text-sm font-semibold text-gray-700">Domain</label>
                <p className="text-gray-900">{skill.domain}</p>
              </div>
              <div>
                <label className="text-sm font-semibold text-gray-700">Tier</label>
                <p className="text-gray-900">{skill.tier}</p>
              </div>
              <div>
                <label className="text-sm font-semibold text-gray-700">License</label>
                <p className="text-gray-900">{skill.license}</p>
              </div>
              <div>
                <label className="text-sm font-semibold text-gray-700">Rating</label>
                <div className="flex items-center gap-1">
                  <Star size={16} className="text-amber-500 fill-amber-500" />
                  <span className="text-gray-900">
                    {skill.rating.toFixed(1)} ({skill.rating_count} reviews)
                  </span>
                </div>
              </div>
            </div>

            {/* Dependencies */}
            {skill.dependencies.length > 0 && (
              <div className="mb-6">
                <h3 className="text-lg font-semibold mb-2">Dependencies</h3>
                <ul className="list-disc list-inside text-gray-700">
                  {skill.dependencies.map((dep) => (
                    <li key={dep}>{dep}</li>
                  ))}
                </ul>
              </div>
            )}

            {/* Tags */}
            <div className="mb-6">
              <h3 className="text-lg font-semibold mb-2">Tags</h3>
              <div className="flex gap-2 flex-wrap">
                {skill.tags.map((tag) => (
                  <span
                    key={tag}
                    className="px-3 py-1 bg-amber-100 text-amber-800 rounded-full text-sm"
                  >
                    {tag}
                  </span>
                ))}
              </div>
            </div>

            {/* Links */}
            {(skill.homepage_url || skill.repository_url) && (
              <div className="mb-6 flex gap-4">
                {skill.homepage_url && (
                  <a
                    href={skill.homepage_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-amber-600 hover:text-amber-700 font-semibold"
                  >
                    Homepage →
                  </a>
                )}
                {skill.repository_url && (
                  <a
                    href={skill.repository_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-amber-600 hover:text-amber-700 font-semibold"
                  >
                    Repository →
                  </a>
                )}
              </div>
            )}

            {/* Install Button */}
            <div className="flex gap-2 pt-4 border-t border-gray-200">
              <button
                onClick={() => onInstall(skill.skill_id, skill.version)}
                disabled={installing}
                className="flex-1 flex items-center justify-center gap-2 px-4 py-2 bg-amber-600 text-white rounded-lg hover:bg-amber-700 disabled:bg-gray-400"
              >
                {installing ? (
                  <>
                    <Loader size={18} className="animate-spin" />
                    Installing...
                  </>
                ) : (
                  <>
                    <Download size={18} />
                    Install Skill
                  </>
                )}
              </button>
              <button
                onClick={onClose}
                className="px-4 py-2 border border-gray-300 rounded-lg hover:bg-gray-50"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

// ============================================================================
// InstallProgressOverlay Component
// ============================================================================

const InstallProgressOverlay: React.FC<{
  job: InstallJob | null;
  onClose: () => void;
}> = ({ job, onClose }) => {
  if (!job) return null;

  const isComplete = job.status === 'complete';
  const isError = job.status === 'error';

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black bg-opacity-50">
      <div className="bg-white rounded-lg shadow-xl p-6 max-w-md w-full">
        <h3 className="text-lg font-semibold mb-4">
          {isComplete ? '✅ Installation Complete' : isError ? '❌ Installation Failed' : '⏳ Installing...'}
        </h3>

        <div className="mb-4">
          <div className="flex justify-between mb-2">
            <span className="text-sm text-gray-700">{job.skill_id} v{job.version}</span>
            <span className="text-sm font-semibold">{Math.round(job.progress * 100)}%</span>
          </div>
          <div className="w-full bg-gray-200 rounded-full h-2">
            <div
              className="bg-amber-600 h-2 rounded-full transition-all duration-300"
              style={{ width: `${job.progress * 100}%` }}
            ></div>
          </div>
        </div>

        {/* Logs */}
        <div className="mb-4 p-3 bg-gray-50 rounded-lg max-h-48 overflow-y-auto">
          <div className="text-xs font-mono text-gray-700 space-y-1">
            {job.logs.map((log, idx) => (
              <div key={idx}>{log}</div>
            ))}
          </div>
        </div>

        {isComplete && (
          <div className="mb-4 p-3 bg-green-50 border border-green-200 rounded-lg">
            <p className="text-sm text-green-800">Skill installed successfully!</p>
          </div>
        )}

        {isError && (
          <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-lg flex gap-2">
            <AlertCircle size={18} className="text-red-600 flex-shrink-0" />
            <p className="text-sm text-red-800">Installation failed. Check logs above.</p>
          </div>
        )}

        {(isComplete || isError) && (
          <button
            onClick={onClose}
            className="w-full px-4 py-2 bg-amber-600 text-white rounded-lg hover:bg-amber-700"
          >
            Close
          </button>
        )}
      </div>
    </div>
  );
};

// ============================================================================
// Main Marketplace Component
// ============================================================================

interface FilterState {
  tier?: string;
  domain?: string;
  min_rating: number;
}

export default function MarketplacePanel() {
  const [query, setQuery] = useState('');
  const [filters, setFilters] = useState<FilterState>({ min_rating: 0 });
  const [sortBy, setSortBy] = useState('popularity');
  const [skills, setSkills] = useState<Skill[]>([]);
  const [selectedSkill, setSelectedSkill] = useState<SkillDetail | null>(null);
  const [installJob, setInstallJob] = useState<InstallJob | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Fetch skills based on query/filters
  const fetchSkills = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      let url = '/v1/skills/marketplace/search?limit=20';

      if (query) {
        url += `&q=${encodeURIComponent(query)}`;
      } else {
        url = '/v1/skills/marketplace/index?limit=20';
      }

      if (filters.tier) url += `&tier=${encodeURIComponent(filters.tier)}`;
      if (filters.domain) url += `&domain=${encodeURIComponent(filters.domain)}`;
      if (filters.min_rating > 0) url += `&min_rating=${filters.min_rating}`;
      url += `&sort_by=${encodeURIComponent(sortBy)}`;

      const response = await fetch(url);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);

      const data = await response.json();
      setSkills(data.skills || []);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch skills');
    } finally {
      setLoading(false);
    }
  }, [query, filters, sortBy]);

  // Fetch skill detail
  const fetchDetail = useCallback(async (skill_id: string) => {
    try {
      const response = await fetch(`/v1/skills/marketplace/${skill_id}`);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);

      const data = await response.json();
      setSelectedSkill(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch skill details');
    }
  }, []);

  // Install skill
  const handleInstall = useCallback(async (skill_id: string, version: string) => {
    try {
      const response = await fetch(`/v1/skills/marketplace/${skill_id}/install?version=${version}`, {
        method: 'POST',
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);

      const job = await response.json();
      setInstallJob(job);
      setSelectedSkill(null);

      // Poll for status
      const pollInterval = setInterval(async () => {
        const statusResponse = await fetch(`/v1/skills/marketplace/install/${job.job_id}`);
        if (!statusResponse.ok) {
          clearInterval(pollInterval);
          return;
        }

        const status = await statusResponse.json();
        setInstallJob(status);

        if (status.status === 'complete' || status.status === 'error') {
          clearInterval(pollInterval);
        }
      }, 500);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to install skill');
    }
  }, []);

  // Initial fetch
  useEffect(() => {
    fetchSkills();
  }, [fetchSkills]);

  return (
    <div className="min-h-screen bg-gray-50 p-4">
      <div className="max-w-7xl mx-auto">
        {/* Header */}
        <div className="mb-6">
          <h1 className="text-3xl font-bold text-gray-900">Skill Marketplace</h1>
          <p className="text-gray-600">Discover and install skills for CorvinOS</p>
        </div>

        {/* Search Bar */}
        <SearchBar onSearch={setQuery} onFilterChange={setFilters} />

        {/* Error Alert */}
        {error && (
          <div className="mt-4 p-4 bg-red-50 border border-red-200 rounded-lg flex gap-2">
            <AlertCircle size={20} className="text-red-600 flex-shrink-0" />
            <div>
              <h3 className="font-semibold text-red-800">Error</h3>
              <p className="text-red-700">{error}</p>
            </div>
          </div>
        )}

        {/* Sorting */}
        <div className="mt-4 flex gap-4">
          <label className="text-sm font-semibold text-gray-700">Sort by:</label>
          <select
            value={sortBy}
            onChange={(e) => setSortBy(e.target.value)}
            className="px-3 py-1 border border-gray-300 rounded-lg text-sm"
          >
            <option value="popularity">Popularity</option>
            <option value="rating">Rating</option>
            <option value="recency">Newest</option>
            <option value="name">Name</option>
          </select>
        </div>

        {/* Loading State */}
        {loading && (
          <div className="mt-6 flex justify-center">
            <Loader className="animate-spin text-amber-600" size={32} />
          </div>
        )}

        {/* Skills Grid */}
        {!loading && skills.length > 0 && (
          <div className="mt-6 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {skills.map((skill) => (
              <SkillCard
                key={skill.skill_id}
                skill={skill}
                onSelect={() => fetchDetail(skill.skill_id)}
              />
            ))}
          </div>
        )}

        {/* No Results */}
        {!loading && skills.length === 0 && !error && (
          <div className="mt-12 text-center py-8">
            <p className="text-gray-600 text-lg">No skills found. Try adjusting your search or filters.</p>
          </div>
        )}
      </div>

      {/* Detail Modal */}
      <SkillDetailModal
        skill={selectedSkill}
        onClose={() => setSelectedSkill(null)}
        onInstall={handleInstall}
        installing={installJob !== null && installJob.status === 'installing'}
      />

      {/* Install Progress */}
      <InstallProgressOverlay
        job={installJob}
        onClose={() => setInstallJob(null)}
      />
    </div>
  );
}
