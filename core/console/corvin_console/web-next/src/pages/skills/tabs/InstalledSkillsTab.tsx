/**
 * Installed Skills Tab — list + uninstall.
 *
 * GET /v1/console/skills-manager/skills/installed answers
 * {skills: [{skill_id, version, boot_layer, verified}], total}; there is no
 * name/author/install date on that record, so none is shown. Uninstall is the
 * real DELETE route (it used to call the install path).
 */

import { useEffect, useState, useCallback } from 'react';
import { Trash2, RefreshCw } from 'lucide-react';
import { useSkillManager } from '../SkillManagerContext';
import { SkillCard } from '../components/SkillCard';
import { SKILLS_INSTALLED, errorMessage } from '../endpoints';

export interface SkillInfo {
  skill_id: string;
  version: string;
  boot_layer: string;
  verified: boolean;
}

interface InstalledSkillsTabProps {
  canUninstall: boolean;
}

export function InstalledSkillsTab({ canUninstall }: InstalledSkillsTabProps) {
  const { uninstall, revision } = useSkillManager();
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await fetch(SKILLS_INSTALLED, { credentials: 'same-origin' });
      if (!r.ok) throw new Error(await errorMessage(r));
      const data = await r.json();
      setSkills(Array.isArray(data?.skills) ? data.skills : []);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, revision]);

  const handleUninstall = useCallback(
    async (skill: SkillInfo) => {
      if (!window.confirm(`Remove "${skill.skill_id}" v${skill.version}? This cannot be undone.`)) {
        return;
      }
      await uninstall(skill.skill_id, skill.version);
    },
    [uninstall],
  );

  if (loading) {
    return <div className="text-center py-12">Loading installed skills…</div>;
  }

  if (error) {
    return (
      <div className="bg-destructive/10 border border-destructive rounded p-4 text-destructive">
        <p className="font-medium">Failed to load skills: {error}</p>
        <button
          onClick={() => void load()}
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
        <p className="text-sm mt-2">Use the "Upload" tab to install a skill package.</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center">
        <h2 className="text-lg font-semibold">Installed Skills ({skills.length})</h2>
        <button
          onClick={() => void load()}
          className="px-3 py-1 text-sm bg-muted hover:bg-muted/80 rounded flex items-center gap-2"
        >
          <RefreshCw className="h-4 w-4" />
          Refresh
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {skills.map((skill) => (
          <SkillCard
            key={`${skill.skill_id}@${skill.version}`}
            skill={{
              skill_id: skill.skill_id,
              name: skill.skill_id,
              version: skill.version,
              status: skill.verified ? 'verified' : 'unverified',
              boot_layer: skill.boot_layer,
            }}
            action={canUninstall ? 'uninstall' : 'none'}
            onAction={() => void handleUninstall(skill)}
            actionIcon={<Trash2 className="h-4 w-4" />}
            actionLabel="Uninstall"
          />
        ))}
      </div>
    </div>
  );
}
