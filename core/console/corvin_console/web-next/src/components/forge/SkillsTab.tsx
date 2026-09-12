import React, { useState } from 'react';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Zap, Plus } from 'lucide-react';
import { ForgeSkill } from '@/types/forge';

interface SkillsTabProps {
  skills: ForgeSkill[];
  setSkills: (skills: ForgeSkill[]) => void;
  searchQuery: string;
  filterStatus: 'all' | 'enabled' | 'disabled';
}

export default function SkillsTab({
  skills,
  setSkills,
  searchQuery,
  filterStatus,
}: SkillsTabProps) {
  const [selectedSkill, setSelectedSkill] = useState<string | null>(null);

  const filtered = React.useMemo(() => {
    return skills.filter((skill) => {
      const matchesSearch =
        searchQuery === '' ||
        skill.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
        (skill.description?.toLowerCase().includes(searchQuery.toLowerCase()) ?? false);

      const matchesStatus =
        filterStatus === 'all' ||
        (filterStatus === 'enabled' && skill.enabled) ||
        (filterStatus === 'disabled' && !skill.enabled);

      return matchesSearch && matchesStatus;
    });
  }, [skills, searchQuery, filterStatus]);

  const handleRollback = async (skillId: string, version: string) => {
    try {
      const res = await fetch(`/v1/console/forge/skills/${skillId}/rollback?version=${version}`, {
        method: 'POST',
      });
      if (!res.ok) throw new Error('Failed to rollback skill');

      const updated = await res.json();
      setSkills(skills.map((s) => (s.id === skillId ? updated : s)));
    } catch (err) {
      console.error('Error rolling back skill:', err);
    }
  };

  const handleToggle = async (skillId: string) => {
    const skill = skills.find((s) => s.id === skillId);
    if (!skill) return;

    try {
      const endpoint = skill.enabled
        ? `/v1/console/forge/skills/${skillId}/disable`
        : `/v1/console/forge/skills/${skillId}/enable`;

      const res = await fetch(endpoint, { method: 'POST' });
      if (!res.ok) throw new Error('Failed to toggle skill');

      setSkills(
        skills.map((s) => (s.id === skillId ? { ...s, enabled: !s.enabled } : s))
      );
    } catch (err) {
      console.error('Error toggling skill:', err);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center">
        <h2 className="text-xl font-semibold">SkillForge Skills</h2>
        <Button size="sm">
          <Plus className="w-4 h-4 mr-2" />
          New Skill
        </Button>
      </div>

      {filtered.length === 0 ? (
        <Card className="border-dashed">
          <CardContent className="py-12 text-center text-sm text-muted-foreground">
            {skills.length === 0
              ? 'No skills yet. Create one to get started.'
              : 'No skills match your search.'}
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-3">
          {filtered.map((skill) => (
            <Card
              key={skill.id}
              className="cursor-pointer hover:border-accent/50 transition-colors"
              onClick={() => setSelectedSkill(skill.id)}
            >
              <CardContent className="py-4">
                <div className="flex items-start justify-between">
                  <div className="flex-1">
                    <div className="flex items-center gap-2 mb-2">
                      <Zap className="w-4 h-4 text-accent" />
                      <h3 className="font-semibold">{skill.name}</h3>
                      <Badge variant={skill.enabled ? 'ok' : 'secondary'} className="text-[10px]">
                        {skill.enabled ? 'Enabled' : 'Disabled'}
                      </Badge>
                      {skill.version && (
                        <Badge variant="outline" className="text-[10px]">
                          v{skill.version}
                        </Badge>
                      )}
                      {skill.learning_state && (
                        <Badge variant="secondary" className="text-[10px]">
                          {skill.learning_state.confidence.toFixed(2)} confidence
                        </Badge>
                      )}
                    </div>
                    {skill.description && (
                      <p className="text-sm text-muted-foreground line-clamp-2">
                        {skill.description}
                      </p>
                    )}
                    {skill.versions && skill.versions.length > 1 && (
                      <p className="text-xs text-muted-foreground mt-2">
                        {skill.versions.length} version{skill.versions.length === 1 ? '' : 's'}
                      </p>
                    )}
                  </div>
                  <div className="flex gap-2">
                    {skill.versions && skill.versions.length > 1 && (
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={(e) => {
                          e.stopPropagation();
                          // Show rollback menu
                        }}
                      >
                        Rollback
                      </Button>
                    )}
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={(e) => {
                        e.stopPropagation();
                        handleToggle(skill.id);
                      }}
                    >
                      {skill.enabled ? 'Disable' : 'Enable'}
                    </Button>
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
