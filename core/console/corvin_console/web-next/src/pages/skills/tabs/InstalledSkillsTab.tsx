/**
 * Installed Skills Tab — List + Uninstall (Phase 5 K=3)
 *
 * Displays installed skills with version, author, install date.
 * Uninstall button triggers polling via SkillManager context.
 */

import { useEffect, useState, useCallback } from 'react';
import { Trash2, RefreshCw } from 'lucide-react';
import { useSkillManager } from '../SkillManagerContext';
import { SkillCard } from '../components/SkillCard';

export interface SkillInfo {
  skill_id: string;
  name: string;
  version: string;
  author: string;
  installed_at: string;
  status: 'active' | 'inactive' | 'error';
}

interface InstalledSkillsTabProps {
  canUninstall: boolean;
}

export function InstalledSkillsTab({ canUninstall }: InstalledSkillsTabProps) {
  const { startInstall } = useSkillManager();
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Fetch installed skills on mount
  useEffect(() => {
    setLoading(true);
    setError(null);
    fetch('/v1/skills/installed')
      .then((r) => {
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
        return r.json();
      })
      .then((data) => {
        setSkills(data.skills || []);
      })
      .catch((err) => {
        setError(String(err));
        console.error('Failed to load installed skills:', err);
      })
      .finally(() => setLoading(false));
  }, []);

  const handleUninstall = useCallback(
    async (skillId: string, skillName: string) => {
      if (!window.confirm(`Remove "${skillName}"? This cannot be undone.`)) {
        return;
      }
      await startInstall(skillId, skillName, 'marketplace'); // Reuse startInstall for uninstall
    },
    [startInstall],
  );

  const handleRefresh = useCallback(() => {
    setLoading(true);
    setError(null);
    fetch('/v1/skills/installed')
      .then((r) => {
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
        return r.json();
      })
      .then((data) => {
        setSkills(data.skills || []);
      })
      .catch((err) => {
        setError(String(err));
      })
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return <div className="text-center py-12">Loading installed skills…</div>;
  }

  if (error) {
    return (
      <div className="bg-destructive/10 border border-destructive rounded p-4 text-destructive">
        <p className="font-medium">Failed to load skills: {error}</p>
        <button
          onClick={handleRefresh}
          className="mt-2 px-3 py-1 text-sm bg-destructive text-destructive-foreground rounded hover:opacity-90"
        >
          Retry
        </button>
      </div>
    );
  }

  if (skills.length === 0) {
    return (
      <div className="text-center py-12 text-muted-foreground">
        <p>No skills installed yet.</p>
        <p className="text-sm mt-2">Visit the "Available" tab to install skills.</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center">
        <h2 className="text-lg font-semibold">Installed Skills ({skills.length})</h2>
        <button
          onClick={handleRefresh}
          className="px-3 py-1 text-sm bg-muted hover:bg-muted/80 rounded flex items-center gap-2"
        >
          <RefreshCw className="h-4 w-4" />
          Refresh
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {skills.map((skill) => (
          <SkillCard
            key={skill.skill_id}
            skill={skill}
            action={canUninstall ? 'uninstall' : 'none'}
            onAction={() => handleUninstall(skill.skill_id, skill.name)}
            actionIcon={<Trash2 className="h-4 w-4" />}
            actionLabel="Uninstall"
          />
        ))}
      </div>
    </div>
  );
}
