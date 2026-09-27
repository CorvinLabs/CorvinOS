/**
 * Available Skills Tab — Marketplace Search + Install (Phase 5 K=3)
 *
 * Search marketplace, filter by category, install skills.
 * Uses Suspense + lazy loading for search results.
 */

import { useEffect, useState, useCallback, useMemo } from 'react';
import { Search, Download } from 'lucide-react';
import { useSkillManager } from '../SkillManagerContext';
import { SkillCard } from '../components/SkillCard';

export interface MarketplaceSkill {
  skill_id: string;
  name: string;
  version: string;
  author: string;
  description: string;
  category: string;
  rating: number;
  reviews: number;
}

const CATEGORIES = ['all', 'networking', 'storage', 'security', 'ai', 'data', 'monitoring'];

export function AvailableSkillsTab() {
  const { startInstall } = useSkillManager();
  const [allSkills, setAllSkills] = useState<MarketplaceSkill[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedCategory, setSelectedCategory] = useState('all');

  // Fetch all marketplace skills on mount
  useEffect(() => {
    setLoading(true);
    setError(null);
    fetch('/v1/skills/available')
      .then((r) => {
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
        return r.json();
      })
      .then((data) => {
        setAllSkills(data.skills || []);
      })
      .catch((err) => {
        setError(String(err));
        console.error('Failed to load marketplace skills:', err);
      })
      .finally(() => setLoading(false));
  }, []);

  // Filter skills based on search + category
  const filteredSkills = useMemo(() => {
    let result = allSkills;

    // Category filter
    if (selectedCategory !== 'all') {
      result = result.filter((skill) => skill.category === selectedCategory);
    }

    // Search filter (name + description)
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      result = result.filter(
        (skill) => skill.name.toLowerCase().includes(q) || skill.description.toLowerCase().includes(q),
      );
    }

    return result;
  }, [allSkills, searchQuery, selectedCategory]);

  const handleInstall = useCallback(
    async (skillId: string, skillName: string) => {
      await startInstall(skillId, skillName, 'marketplace');
    },
    [startInstall],
  );

  if (loading) {
    return <div className="text-center py-12">Loading marketplace…</div>;
  }

  if (error) {
    return (
      <div className="bg-destructive/10 border border-destructive rounded p-4 text-destructive">
        <p className="font-medium">Failed to load marketplace: {error}</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold mb-4">Marketplace Skills</h2>

        {/* Search + Filter */}
        <div className="space-y-4">
          <div className="relative">
            <Search className="absolute left-3 top-3 h-4 w-4 text-muted-foreground" />
            <input
              type="text"
              placeholder="Search skills by name or description…"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full pl-10 pr-4 py-2 border rounded bg-background text-foreground"
            />
          </div>

          {/* Category filter */}
          <div className="flex gap-2 flex-wrap">
            {CATEGORIES.map((cat) => (
              <button
                key={cat}
                onClick={() => setSelectedCategory(cat)}
                className={`px-3 py-1 text-sm rounded capitalize ${
                  selectedCategory === cat
                    ? 'bg-primary text-primary-foreground'
                    : 'bg-muted hover:bg-muted/80'
                }`}
              >
                {cat}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Results */}
      {filteredSkills.length === 0 ? (
        <div className="text-center py-12 text-muted-foreground">
          <p>
            {searchQuery || selectedCategory !== 'all'
              ? 'No skills match your search.'
              : 'No skills available.'}
          </p>
        </div>
      ) : (
        <div>
          <p className="text-sm text-muted-foreground mb-4">Found {filteredSkills.length} skill(s)</p>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {filteredSkills.map((skill) => (
              <SkillCard
                key={skill.skill_id}
                skill={skill}
                action="install"
                onAction={() => handleInstall(skill.skill_id, skill.name)}
                actionIcon={<Download className="h-4 w-4" />}
                actionLabel="Install"
                subtitle={`${skill.rating} ★ (${skill.reviews} reviews)`}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
