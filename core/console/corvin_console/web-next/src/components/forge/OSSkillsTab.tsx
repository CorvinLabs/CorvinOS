import React, { useState, useRef } from 'react';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Cog, Download, Upload, Plus } from 'lucide-react';
import { ForgeOSSkill } from '@/types/forge';
import {
  exportSingleItem,
  exportBatchItems,
  importBundle,
  isValidZipFile,
} from '@/lib/forge-bundles';

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
  filterStatus }: OSSkillsTabProps) {
  const [_selectedOSSkill, setSelectedOSSkill] = useState<string | null>(null);
  const [_editingConfig, setEditingConfig] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [importing, setImporting] = useState(false);
  const [toast, setToast] = useState<{ kind: 'ok' | 'err'; msg: string } | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

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

  const _handleConfigUpdate = async (skillId: string, config: Record<string, unknown>) => {
    try {
      const res = await fetch(`/v1/console/forge/os-skills/${skillId}/config`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config) });
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

  const handleDownloadSingle = async (skillId: string) => {
    const result = await exportSingleItem('os_skills', skillId);
    if (!result.success) {
      setToast({ kind: 'err', msg: `Download failed: ${result.error}` });
    }
  };

  const handleDownloadSelected = async () => {
    if (selectedIds.size === 0) {
      setToast({ kind: 'err', msg: 'No items selected' });
      return;
    }

    const result = await exportBatchItems('os_skills', Array.from(selectedIds));
    if (!result.success) {
      setToast({ kind: 'err', msg: `Download failed: ${result.error}` });
    } else {
      setToast({ kind: 'ok', msg: `Downloaded ${selectedIds.size} OS-Skills` });
      setSelectedIds(new Set());
    }
  };

  const handleImport = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    if (!isValidZipFile(file.name)) {
      setToast({ kind: 'err', msg: 'Please select a .zip file' });
      return;
    }

    setImporting(true);
    try {
      const result = await importBundle(file);
      if (result.success) {
        setToast({
          kind: 'ok',
          msg: `Imported successfully (${result.imported_count} OS-Skills)`,
        });
        // Refresh OS-Skills list
        try {
          const res = await fetch('/v1/console/forge/os-skills');
          if (res.ok) setOSSkills((await res.json()).os_skills || []);
        } catch (err) {
          console.error('Failed to refresh OS-Skills:', err);
        }
      } else {
        setToast({ kind: 'err', msg: result.message });
      }
    } finally {
      setImporting(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  const toggleSelection = (skillId: string) => {
    const newSelected = new Set(selectedIds);
    if (newSelected.has(skillId)) {
      newSelected.delete(skillId);
    } else {
      newSelected.add(skillId);
    }
    setSelectedIds(newSelected);
  };

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center">
        <h2 className="text-xl font-semibold">OS-Skills (System-Level)</h2>
        <div className="flex gap-2">
          <input
            ref={fileInputRef}
            type="file"
            accept=".zip"
            onChange={handleImport}
            className="hidden"
            aria-label="Import OS-Skills bundle"
          />
          <Button
            size="sm"
            variant="outline"
            onClick={() => fileInputRef.current?.click()}
            disabled={importing}
          >
            <Upload className="w-4 h-4 mr-2" />
            {importing ? 'Importing...' : 'Import'}
          </Button>
          {selectedIds.size > 0 && (
            <Button
              size="sm"
              variant="outline"
              onClick={handleDownloadSelected}
            >
              <Download className="w-4 h-4 mr-2" />
              Download selected ({selectedIds.size})
            </Button>
          )}
          <Button size="sm">
            <Plus className="w-4 h-4 mr-2" />
            New OS-Skill
          </Button>
        </div>
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
                  <div className="flex items-start gap-3 flex-1">
                    <input
                      type="checkbox"
                      checked={selectedIds.has(skill.id)}
                      onChange={() => toggleSelection(skill.id)}
                      onClick={(e) => e.stopPropagation()}
                      className="mt-1"
                      aria-label={`Select ${skill.name}`}
                    />
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
                    <Button
                      variant="ghost"
                      size="sm"
                      aria-label={`Download ${skill.name}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        handleDownloadSingle(skill.id);
                      }}
                    >
                      <Download className="w-4 h-4" />
                    </Button>
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

      {toast && (
        <div
          className={
            toast.kind === 'ok'
              ? 'fixed bottom-6 right-6 z-50 rounded-md border border-emerald-500/30 bg-emerald-500/10 px-4 py-2 text-sm text-emerald-700 shadow-lg dark:text-emerald-300'
              : 'fixed bottom-6 right-6 z-50 rounded-md border border-destructive/30 bg-destructive/10 px-4 py-2 text-sm text-destructive shadow-lg'
          }
          onClick={() => setToast(null)}
        >
          {toast.msg}
        </div>
      )}
    </div>
  );
}
