import React, { useState } from 'react';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Cog, Plus } from 'lucide-react';
import { Input } from '@/components/ui/input';
import { ForgeOSSkill } from '@/types/forge';

interface OSSkillsTabProps {
  osSkills: ForgeOSSkill[];
  setOSSkills: (skills: ForgeOSSkill[]) => void;
  searchQuery: string;
  filterStatus: 'all' | 'enabled' | 'disabled';
}

export default function OSSkillsTab({
  osSkills,
  setOSSkills,
  searchQuery,
  filterStatus,
}: OSSkillsTabProps) {
  const [selectedOSSkill, setSelectedOSSkill] = useState<string | null>(null);
  const [editingConfig, setEditingConfig] = useState<string | null>(null);

  const filtered = React.useMemo(() => {
    return osSkills.filter((skill) => {
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
  }, [osSkills, searchQuery, filterStatus]);

  const handleConfigUpdate = async (skillId: string, config: Record<string, any>) => {
    try {
      const res = await fetch(`/v1/console/forge/os-skills/${skillId}/config`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config),
      });
      if (!res.ok) throw new Error('Failed to update config');

      const updated = await res.json();
      setOSSkills(osSkills.map((s) => (s.id === skillId ? updated : s)));
      setEditingConfig(null);
    } catch (err) {
      console.error('Error updating config:', err);
    }
  };

  const handleToggle = async (skillId: string) => {
    const skill = osSkills.find((s) => s.id === skillId);
    if (!skill) return;

    try {
      const endpoint = skill.enabled
        ? `/v1/console/forge/os-skills/${skillId}/disable`
        : `/v1/console/forge/os-skills/${skillId}/enable`;

      const res = await fetch(endpoint, { method: 'POST' });
      if (!res.ok) throw new Error('Failed to toggle skill');

      setOSSkills(
        osSkills.map((s) => (s.id === skillId ? { ...s, enabled: !s.enabled } : s))
      );
    } catch (err) {
      console.error('Error toggling OS-Skill:', err);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center">
        <h2 className="text-xl font-semibold">OS-Skills (System-Level)</h2>
      </div>

      {filtered.length === 0 ? (
        <Card className="border-dashed">
          <CardContent className="py-12 text-center text-sm text-muted-foreground">
            {osSkills.length === 0
              ? 'No OS-Skills yet.'
              : 'No OS-Skills match your search.'}
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-3">
          {filtered.map((skill) => (
            <Card
              key={skill.id}
              className="cursor-pointer hover:border-accent/50 transition-colors"
              onClick={() => setSelectedOSSkill(skill.id)}
            >
              <CardContent className="py-4">
                <div className="flex items-start justify-between">
                  <div className="flex-1">
                    <div className="flex items-center gap-2 mb-2">
                      <Cog className="w-4 h-4 text-accent" />
                      <h3 className="font-semibold">{skill.name}</h3>
                      <Badge variant={skill.enabled ? 'ok' : 'secondary'} className="text-[10px]">
                        {skill.enabled ? 'Enabled' : 'Disabled'}
                      </Badge>
                      {skill.version && (
                        <Badge variant="outline" className="text-[10px]">
                          v{skill.version}
                        </Badge>
                      )}
                      {skill.is_meta_skill && (
                        <Badge variant="danger" className="text-[10px]">
                          Meta (Locked)
                        </Badge>
                      )}
                    </div>
                    {skill.description && (
                      <p className="text-sm text-muted-foreground line-clamp-2">
                        {skill.description}
                      </p>
                    )}
                    {skill.layer && (
                      <p className="text-xs text-muted-foreground mt-2">
                        Layer: {skill.layer} | Boot Layer: {skill.boot_layer}
                      </p>
                    )}
                  </div>
                  <div className="flex gap-2">
                    {!skill.is_meta_skill && skill.config && (
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={(e) => {
                          e.stopPropagation();
                          setEditingConfig(skill.id);
                        }}
                      >
                        Tune Config
                      </Button>
                    )}
                    {!skill.is_meta_skill && (
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
                    )}
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
