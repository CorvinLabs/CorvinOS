import React, { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { ReauthDialog } from '@/components/reauth-dialog';
import { Zap, Plus, Trash2 } from 'lucide-react';
import { ForgeSkill } from '@/types/forge';
import {
  createManualSkill,
  deleteManualSkill,
  promoteSkill,
  type PromoteTarget,
} from '@/lib/api';
import { useAuth } from '@/lib/auth';

/**
 * SkillForge promotion gates per CLAUDE.md § Layer 7:
 *   task → session     : >= 1 positive grade
 *   session → project  : >= 3 grades, mean >= 0.5
 *   project → user     : force = true required (explicit operator decision)
 *
 * Carried over verbatim from the standalone /app/skills page when it was folded
 * into this tab on 2026-09-20. The page was removed as a duplicate VIEW — both
 * it and this tab list the same 643 records — but it was NOT a duplicate of the
 * ACTIONS: creating, promoting and deleting a skill existed only there, and
 * this tab's "New Skill" button had no onClick handler at all. Removing the
 * page without porting these would have silently dropped three capabilities.
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
}

export default function SkillsTab({
  skills,
  setSkills,
  searchQuery,
  filterStatus,
}: SkillsTabProps) {
  const [_selectedSkill, setSelectedSkill] = useState<string | null>(null);
  const { session } = useAuth();
  const qc = useQueryClient();

  const [isDialogOpen, setIsDialogOpen] = useState(false);
  const [newSkillName, setNewSkillName] = useState('');
  const [newSkillBody, setNewSkillBody] = useState('');
  const [newSkillError, setNewSkillError] = useState<string | null>(null);
  const [reauthOpen, setReauthOpen] = useState(false);
  const [pending, setPending] = useState<{ name: string; to: PromoteTarget; force: boolean } | null>(null);
  const [toast, setToast] = useState<{ kind: 'ok' | 'err'; msg: string } | null>(null);

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

  const createMutation = useMutation({
    mutationFn: async ({ name, body }: { name: string; body: string }) =>
      createManualSkill(name, body, session!.csrf_token),
    onSuccess: async (_d, vars) => {
      setToast({ kind: 'ok', msg: `Created skill "${vars.name}"` });
      setIsDialogOpen(false);
      setNewSkillName('');
      setNewSkillBody('');
      setNewSkillError(null);
      await refresh();
    },
    onError: (e: Error) => setNewSkillError(e.message),
  });

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

  function handleCreateSubmit(e: React.FormEvent) {
    e.preventDefault();
    setNewSkillError(null);
    if (!newSkillName.trim()) {
      setNewSkillError('Skill name is required.');
      return;
    }
    if (!newSkillBody.trim()) {
      setNewSkillError('Skill body is required.');
      return;
    }
    createMutation.mutate({ name: newSkillName.trim(), body: newSkillBody });
  }

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

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center">
        <h2 className="text-xl font-semibold">SkillForge Skills</h2>
        <Button
          size="sm"
          data-testid="new-skill-btn"
          onClick={() => {
            setNewSkillName('');
            setNewSkillBody('');
            setNewSkillError(null);
            setIsDialogOpen(true);
          }}
        >
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

      <Dialog open={isDialogOpen} onOpenChange={setIsDialogOpen}>
        <DialogContent>
          <form onSubmit={handleCreateSubmit}>
            <DialogHeader>
              <DialogTitle>New skill</DialogTitle>
              <DialogDescription>
                A skill is reusable instructions the assistant saves and improves
                over time. It starts at task scope and is promoted from there.
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-3 py-4">
              <div className="space-y-1">
                <Label htmlFor="new-skill-name">Name</Label>
                <Input
                  id="new-skill-name"
                  value={newSkillName}
                  onChange={(e) => setNewSkillName(e.target.value)}
                  placeholder="assistant.my_skill"
                  autoFocus
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="new-skill-body">Body</Label>
                <Textarea
                  id="new-skill-body"
                  value={newSkillBody}
                  onChange={(e) => setNewSkillBody(e.target.value)}
                  placeholder="What the assistant should do, and when."
                  rows={8}
                />
              </div>
              {newSkillError && (
                <p className="text-sm text-destructive">{newSkillError}</p>
              )}
            </div>
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => setIsDialogOpen(false)}
              >
                Cancel
              </Button>
              <Button type="submit" disabled={createMutation.isPending}>
                {createMutation.isPending ? 'Creating…' : 'Create skill'}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

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
