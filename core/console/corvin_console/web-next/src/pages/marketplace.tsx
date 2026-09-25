/**
 * Phase 6: Skill Marketplace Discovery
 * ADR-0682: Browse, search, filter, and install skills
 */

import React, { useState, useEffect } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Alert, AlertDescription } from '@/components/ui/alert';

interface SkillCard {
  skill_id: string;
  name: string;
  description: string;
  domain: string;
  tier: string;
  rating: number;
  install_count: number;
}

interface SkillDetail extends SkillCard {
  version: string;
  origin: string;
  tags: string[];
  created_at: string;
  updated_at: string;
  dependencies: string[];
}

export function Marketplace() {
  const [skills, setSkills] = useState<SkillCard[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedDomain, setSelectedDomain] = useState<string>('');
  const [selectedTier, setSelectedTier] = useState<string>('');
  const [sortBy, setSortBy] = useState('relevance');
  const [selectedSkill, setSelectedSkill] = useState<SkillDetail | null>(null);
  const [installing, setInstalling] = useState<Set<string>>(new Set());

  const domains = ['routing', 'learning', 'optimization', 'integration', 'security', 'observability'];
  const tiers = ['compliance', 'core', 'installed', 'community'];
  const sorts = ['relevance', 'popularity', 'rating', 'recency', 'alphabetical'];

  useEffect(() => {
    fetchSkills();
  }, []);

  const fetchSkills = async () => {
    try {
      setLoading(true);
      const params = new URLSearchParams({
        q: searchQuery,
        ...(selectedDomain && { domain: selectedDomain }),
        ...(selectedTier && { tier: selectedTier }),
        sort_by: sortBy,
      });

      const endpoint = searchQuery 
        ? `/v1/console/marketplace/search?${params}`
        : `/v1/console/marketplace/index?sort_by=${sortBy}`;

      const response = await fetch(endpoint);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      
      const data = await response.json();
      setSkills(data.skills || []);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Error fetching skills');
      setSkills([]);
    } finally {
      setLoading(false);
    }
  };

  const fetchDetail = async (skillId: string) => {
    try {
      const response = await fetch(`/v1/console/marketplace/${skillId}`);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      setSelectedSkill(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Error fetching skill details');
    }
  };

  const handleInstall = async (skillId: string) => {
    try {
      setInstalling(prev => new Set(prev).add(skillId));
      const response = await fetch(`/v1/console/marketplace/${skillId}/install`, {
        method: 'POST',
      });
      
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail);
      
      setError(null);
      // Auto-refresh skills after install
      setTimeout(() => fetchSkills(), 2000);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Install failed');
    } finally {
      setInstalling(prev => {
        const updated = new Set(prev);
        updated.delete(skillId);
        return updated;
      });
    }
  };

  return (
    <div className="space-y-6 p-6">
      {/* Header */}
      <div>
        <h1 className="text-4xl font-bold">Skill Marketplace</h1>
        <p className="text-gray-600 mt-2">Discover and install skills from the community</p>
      </div>

      {/* Search & Filters */}
      <Card>
        <CardContent className="pt-6 space-y-4">
          <input
            type="text"
            placeholder="Search skills..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && fetchSkills()}
            className="w-full px-4 py-2 border rounded"
          />

          <div className="grid gap-4 md:grid-cols-4">
            <div>
              <label className="text-sm font-medium">Domain</label>
              <select
                value={selectedDomain}
                onChange={(e) => setSelectedDomain(e.target.value)}
                className="w-full px-3 py-2 border rounded mt-1"
              >
                <option value="">All Domains</option>
                {domains.map(d => <option key={d} value={d}>{d}</option>)}
              </select>
            </div>

            <div>
              <label className="text-sm font-medium">Tier</label>
              <select
                value={selectedTier}
                onChange={(e) => setSelectedTier(e.target.value)}
                className="w-full px-3 py-2 border rounded mt-1"
              >
                <option value="">All Tiers</option>
                {tiers.map(t => <option key={t} value={t}>{t}</option>)}
              </select>
            </div>

            <div>
              <label className="text-sm font-medium">Sort By</label>
              <select
                value={sortBy}
                onChange={(e) => setSortBy(e.target.value)}
                className="w-full px-3 py-2 border rounded mt-1"
              >
                {sorts.map(s => <option key={s} value={s}>{s}</option>)}
              </select>
            </div>

            <div className="flex items-end">
              <button
                onClick={fetchSkills}
                disabled={loading}
                className="w-full px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50"
              >
                {loading ? 'Searching...' : 'Search'}
              </button>
            </div>
          </div>
        </CardContent>
      </Card>

      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {/* Skills Grid */}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        {skills.map((skill) => (
          <Card key={skill.skill_id} className="cursor-pointer hover:shadow-lg"
            onClick={() => fetchDetail(skill.skill_id)}>
            <CardHeader>
              <div className="flex justify-between items-start">
                <div className="flex-1">
                  <CardTitle className="text-lg">{skill.name}</CardTitle>
                  <p className="text-xs text-gray-500 mt-1">{skill.skill_id}</p>
                </div>
                <Badge className="bg-blue-100 text-blue-800">{skill.tier}</Badge>
              </div>
            </CardHeader>
            <CardContent className="space-y-2">
              <p className="text-sm text-gray-600">{skill.description}</p>
              <div className="flex gap-2">
                <Badge variant="outline">{skill.domain}</Badge>
                <Badge variant="outline">⭐ {skill.rating.toFixed(1)}</Badge>
                <Badge variant="outline">📦 {skill.install_count}</Badge>
              </div>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  handleInstall(skill.skill_id);
                }}
                disabled={installing.has(skill.skill_id)}
                className="w-full mt-3 px-3 py-2 bg-green-600 text-white rounded text-sm hover:bg-green-700 disabled:opacity-50"
              >
                {installing.has(skill.skill_id) ? 'Installing...' : 'Install'}
              </button>
            </CardContent>
          </Card>
        ))}
      </div>

      {/* Detail Modal */}
      {selectedSkill && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center p-4"
          onClick={() => setSelectedSkill(null)}>
          <Card className="max-w-2xl max-h-96 overflow-y-auto" onClick={(e) => e.stopPropagation()}>
            <CardHeader>
              <div className="flex justify-between items-start">
                <div>
                  <CardTitle>{selectedSkill.name}</CardTitle>
                  <p className="text-sm text-gray-500">v{selectedSkill.version}</p>
                </div>
                <button onClick={() => setSelectedSkill(null)} className="text-2xl">✕</button>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              <div>
                <h3 className="font-semibold">Description</h3>
                <p className="text-sm text-gray-600">{selectedSkill.description}</p>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <p className="text-xs text-gray-500">Domain</p>
                  <p className="font-semibold">{selectedSkill.domain}</p>
                </div>
                <div>
                  <p className="text-xs text-gray-500">Tier</p>
                  <p className="font-semibold">{selectedSkill.tier}</p>
                </div>
                <div>
                  <p className="text-xs text-gray-500">Rating</p>
                  <p className="font-semibold">⭐ {selectedSkill.rating}/5</p>
                </div>
                <div>
                  <p className="text-xs text-gray-500">Installs</p>
                  <p className="font-semibold">{selectedSkill.install_count}</p>
                </div>
              </div>

              {selectedSkill.tags.length > 0 && (
                <div>
                  <p className="text-xs text-gray-500 mb-2">Tags</p>
                  <div className="flex flex-wrap gap-1">
                    {selectedSkill.tags.map(tag => (
                      <Badge key={tag} variant="outline" className="text-xs">{tag}</Badge>
                    ))}
                  </div>
                </div>
              )}

              {selectedSkill.dependencies.length > 0 && (
                <div>
                  <p className="text-xs text-gray-500 mb-2">Dependencies</p>
                  <ul className="text-sm list-disc ml-4">
                    {selectedSkill.dependencies.map(dep => (
                      <li key={dep}>{dep}</li>
                    ))}
                  </ul>
                </div>
              )}

              <button
                onClick={() => handleInstall(selectedSkill.skill_id)}
                disabled={installing.has(selectedSkill.skill_id)}
                className="w-full px-4 py-3 bg-green-600 text-white rounded hover:bg-green-700 disabled:opacity-50"
              >
                {installing.has(selectedSkill.skill_id) ? 'Installing...' : 'Install Skill'}
              </button>
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}

export default Marketplace;
