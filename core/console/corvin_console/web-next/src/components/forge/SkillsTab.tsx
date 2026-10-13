import React, { useState, useRef } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { ReauthDialog } from '@/components/reauth-dialog';
import { Zap, Plus, Trash2, Download, Upload } from 'lucide-react';
import { ForgeSkill } from '@/types/forge';
import {
  deleteManualSkill,
  promoteSkill,
  type PromoteTarget,
} from '@/lib/api';
import { useAuth } from '@/lib/auth';
import {
  exportSingleItem,
  exportBatchItems,
  importBundle,
  isValidZipFile,
} from '@/lib/forge-bundles';

/**
 * SkillForge promotion gates per CLAUDE.md § Layer 7:
 *   task → session     : >= 1 positive grade
 *   session → project  : >= 3 grades, mean >= 0.5
 *   project → user     : force = true required (explicit operator decision)
 *
 * Carried over verbatim from the standalone /app/skills page when it was folded
 * into this tab on 2026-09-20. The page was removed as a duplicate VIEW — both
 * it and this tab list the same 643 records — but it was NOT a duplicate of the
 * ACTIONS: promoting and deleting a skill existed only there, and this tab's
 * "New Skill" button had no onClick handler at all.
 *
 * CREATION, however, is no longer here. Later the same day the Creator tab
 * became the one composer for it (orchestrated run + template form, the latter
 * rehomed from the dead /app/skill-forge-generator page), and a create dialog
 * in this tab would have been a third variant of the same POST /skills/manual
 * call to keep in sync. "New Skill" switches to that tab instead — the
 * capability moved, it was not dropped.
 */
function nextPromoteTarget(
  scopeSource: string | undefined,
): { to: PromoteTarget; needsForce: boolean } | null {
  if (!scopeSource) return null;
  if (scopeSource.startsWith('session:') || scopeSource === 'task') {
    return { to: 'session', needsForce: false };
  }
  if (scopeSource === 'session' || scopeSource === 'session-default') {
    return { to: 'project', needsForce: false };
  }
  if (scopeSource === 'project') {
    return { to: 'user', needsForce: true };
  }
  return null;
}

interface SkillsTabProps {
  skills: ForgeSkill[];
  setSkills: (skills: ForgeSkill[]) => void;
  searchQuery: string;
  filterStatus: 'all' | 'enabled' | 'disabled';
  /** Switch the page to the Creator tab. Creating a skill lives THERE, in one
   *  composer that offers both the orchestrated run and the template form
   *  (2026-09-20 merge); this tab's own create dialog was the third
   *  half-duplicate of that and was removed rather than kept in sync. */
  onCreateSkill: () => void;
}

export default function SkillsTab({
  skills,
  setSkills,
  searchQuery,
  filterStatus,
  onCreateSkill,
}: SkillsTabProps) {
  const [_selectedSkill, setSelectedSkill] = useState<string | null>(null);
  const { session } = useAuth();
  const qc = useQueryClient();

  const [reauthOpen, setReauthOpen] = useState(false);
  const [pending, setPending] = useState<{ name: string; to: PromoteTarget; force: boolean } | null>(null);
  const [toast, setToast] = useState<{ kind: 'ok' | 'err'; msg: string } | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [importing, setImporting] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // The forge list is fetched by the parent page, so a mutation refreshes BOTH
  // that prop (via setSkills) and any react-query consumer of ["skills"].
  const refresh = React.useCallback(async () => {
    await qc.invalidateQueries({ queryKey: ['skills'] });
    try {
      const res = await fetch('/v1/console/forge/skills');
      if (res.ok) setSkills((await res.json()).skills || []);
    } catch {
      /* the list simply stays as it was; the toast already reported the action */
    }
  }, [qc, setSkills]);

  const promoteMutation = useMutation({
    mutationFn: async ({ name, to, force }: { name: string; to: PromoteTarget; force: boolean }) =>
      promoteSkill(name, to, session!.csrf_token, force),
    onSuccess: async (_d, vars) => {
      setToast({ kind: 'ok', msg: `Promoted ${vars.name} → ${vars.to}` });
      await refresh();
    },
    onError: (e: Error) => {
      setToast({ kind: 'err', msg: e.message });
      throw e;
    },
  });

  const deleteMutation = useMutation({
    mutationFn: async (name: string) => deleteManualSkill(name, session!.csrf_token),
    onSuccess: async (_d, name) => {
      setToast({ kind: 'ok', msg: `Deleted skill "${name}"` });
      await refresh();
    },
    onError: (e: Error) => setToast({ kind: 'err', msg: e.message }),
  });

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

  const _handleRollback = async (skillId: string, version: string) => {
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

  const handleDownloadSingle = async (skillId: string) => {
    const result = await exportSingleItem('skills', skillId);
    if (!result.success) {
      setToast({ kind: 'err', msg: `Download failed: ${result.error}` });
    }
  };

  const handleDownloadSelected = async () => {
    if (selectedIds.size === 0) {
      setToast({ kind: 'err', msg: 'No items selected' });
      return;
    }

    const result = await exportBatchItems('skills', Array.from(selectedIds));
    if (!result.success) {
      setToast({ kind: 'err', msg: `Download failed: ${result.error}` });
    } else {
      setToast({ kind: 'ok', msg: `Downloaded ${selectedIds.size} skills` });
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
          msg: `Imported successfully (${result.imported_count} skills)`,
        });
        await refresh();
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
        <h2 className="text-xl font-semibold">SkillForge Skills</h2>
        <div className="flex gap-2">
          <input
            ref={fileInputRef}
            type="file"
            accept=".zip"
            onChange={handleImport}
            className="hidden"
            aria-label="Import skills bundle"
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
          <Button size="sm" data-testid="new-skill-btn" onClick={onCreateSkill}>
            <Plus className="w-4 h-4 mr-2" />
            New Skill
          </Button>
        </div>
      </div>

      {filtered.length === 0 ? (
        <Card className="border-dashed">
          <CardContent className="py-12 text-center text-sm text-muted-foreground">
            {skills.length === 0
              ? 'No skills yet. Use "New Skill" to open the Creator.'
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
                    {(() => {
                      const next = nextPromoteTarget(skill.scope_source);
                      if (!next) return null;
                      return (
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={(e) => {
                            e.stopPropagation();
                            setPending({ name: skill.name, to: next.to, force: next.needsForce });
                            setReauthOpen(true);
                          }}
                        >
                          Promote → {next.to}
                        </Button>
                      );
                    })()}
                    <Button
                      variant="ghost"
                      size="sm"
                      aria-label={`Delete ${skill.name}`}
                      disabled={deleteMutation.isPending}
                      onClick={(e) => {
                        e.stopPropagation();
                        if (window.confirm(`Delete skill "${skill.name}"? This cannot be undone.`)) {
                          deleteMutation.mutate(skill.name);
                        }
                      }}
                    >
                      <Trash2 className="w-4 h-4" />
                    </Button>
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

      <ReauthDialog
        open={reauthOpen}
        onOpenChange={setReauthOpen}
        title={
          pending
            ? `Promote ${pending.name} → ${pending.to}${pending.force ? ' (force)' : ''}`
            : 'Confirm'
        }
        description={
          pending?.force
            ? 'Project→user is an explicit operator decision. Confirm to proceed.'
            : 'Promotion is gated by quality score. Confirm to proceed.'
        }
        onConfirm={async () => {
          if (pending) {
            await promoteMutation.mutateAsync({
              name: pending.name,
              to: pending.to,
              force: pending.force,
            });
            setPending(null);
          }
        }}
      />
    </div>
  );
}
