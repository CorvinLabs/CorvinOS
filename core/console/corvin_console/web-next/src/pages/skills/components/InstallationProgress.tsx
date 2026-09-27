/**
 * Installation status banner for the skill manager tabs.
 *
 * Shows the state of the one in-flight install/uninstall request. It used to
 * poll /v1/skills/status (served by no router), discard the answer, and draw
 * a progress bar from a percentage nothing ever set — there is no progress
 * endpoint, so this shows "in progress" until the response arrives.
 */

import { X, AlertCircle, Loader2, CheckCircle2 } from 'lucide-react';
import { useSkillManager } from '../SkillManagerContext';

export function InstallationProgress() {
  const { installation, clearInstallation } = useSkillManager();
  if (installation.status === 'idle') return null;

  const busy = installation.status === 'installing' || installation.status === 'uninstalling';
  const text =
    installation.status === 'installing'
      ? `Installing ${installation.skillId}…`
      : installation.status === 'uninstalling'
        ? `Uninstalling ${installation.skillId}…`
        : installation.status === 'complete'
          ? installation.message || `${installation.skillId}: done`
          : `${installation.skillId}: ${installation.message || 'failed'}`;

  return (
    <div className="border-b bg-muted/50 p-4" data-testid="installation-status">
      <div className="max-w-4xl mx-auto flex items-start justify-between gap-3">
        <div className="flex items-center gap-2 text-sm">
          {busy ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : installation.status === 'complete' ? (
            <CheckCircle2 className="h-4 w-4 text-green-600" />
          ) : (
            <AlertCircle className="h-4 w-4 text-destructive" />
          )}
          <span className={installation.status === 'error' ? 'text-destructive' : ''}>{text}</span>
        </div>
        {!busy && (
          <button onClick={clearInstallation} className="p-1 hover:bg-muted rounded" title="Close">
            <X className="h-4 w-4" />
          </button>
        )}
      </div>
    </div>
  );
}
