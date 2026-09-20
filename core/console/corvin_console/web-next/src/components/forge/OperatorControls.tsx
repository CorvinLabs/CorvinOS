import React, { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { AlertCircle, CheckCircle, Pause, PlayCircle, RotateCcw } from 'lucide-react';

interface OperatorControlsProps {
  skillId: string;
  currentVersion: string;
  canaryActive: boolean;
  onApprove: () => Promise<void>;
  onDefer: (reason: string) => Promise<void>;
  onPause: () => Promise<void>;
  onResume: () => Promise<void>;
  onRollback: () => Promise<void>;
  disabled?: boolean;
}

type DialogType = 'approve' | 'defer' | 'rollback' | null;

/**
 * OperatorControls: Buttons for operator approval/deferral/rollback
 */
export const OperatorControls: React.FC<OperatorControlsProps> = ({
  skillId,
  currentVersion,
  canaryActive,
  onApprove,
  onDefer,
  onPause,
  onResume,
  onRollback,
  disabled = false,
}) => {
  const [activeDialog, setActiveDialog] = useState<DialogType>(null);
  const [deferReason, setDeferReason] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleApprove = async () => {
    setLoading(true);
    setError(null);
    try {
      await onApprove();
      setActiveDialog(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to approve');
    } finally {
      setLoading(false);
    }
  };

  const handleDefer = async () => {
    setLoading(true);
    setError(null);
    try {
      await onDefer(deferReason);
      setActiveDialog(null);
      setDeferReason('');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to defer');
    } finally {
      setLoading(false);
    }
  };

  const handlePause = async () => {
    setLoading(true);
    setError(null);
    try {
      await onPause();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to pause');
    } finally {
      setLoading(false);
    }
  };

  const handleResume = async () => {
    setLoading(true);
    setError(null);
    try {
      await onResume();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to resume');
    } finally {
      setLoading(false);
    }
  };

  const handleRollback = async () => {
    setLoading(true);
    setError(null);
    try {
      await onRollback();
      setActiveDialog(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to rollback');
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle>Operator Controls</CardTitle>
        </CardHeader>

        <CardContent className="space-y-4">
          {error && (
            <div className="flex items-start gap-2 p-3 bg-red-500/10 text-red-600 dark:text-red-400 rounded-lg border border-red-500/20">
              <AlertCircle className="w-5 h-5 mt-0.5 flex-shrink-0" />
              <div>
                <div className="font-semibold text-sm">Error</div>
                <div className="text-sm">{error}</div>
              </div>
            </div>
          )}

          <div className="space-y-2">
            <p className="text-sm text-muted-foreground">
              Manage the canary deployment of {skillId}@{currentVersion}
            </p>
          </div>

          {/* Approval/Deferral Actions */}
          <div className="grid grid-cols-2 gap-2">
            <Button
              onClick={() => setActiveDialog('approve')}
              disabled={disabled || loading || !canaryActive}
              className="bg-green-600 hover:bg-green-700 text-white"
              size="sm"
            >
              <CheckCircle className="w-4 h-4 mr-2" />
              Approve & Rollout
            </Button>

            <Button
              onClick={() => setActiveDialog('defer')}
              disabled={disabled || loading || !canaryActive}
              variant="outline"
              size="sm"
            >
              Defer & Keep
            </Button>
          </div>

          {/* Pause/Resume and Rollback */}
          <div className="grid grid-cols-2 gap-2 pt-2 border-t">
            {canaryActive ? (
              <Button
                onClick={handlePause}
                disabled={disabled || loading}
                variant="secondary"
                size="sm"
              >
                <Pause className="w-4 h-4 mr-2" />
                Pause
              </Button>
            ) : (
              <Button
                onClick={handleResume}
                disabled={disabled || loading}
                variant="secondary"
                size="sm"
              >
                <PlayCircle className="w-4 h-4 mr-2" />
                Resume
              </Button>
            )}

            <Button
              onClick={() => setActiveDialog('rollback')}
              disabled={disabled || loading}
              variant="destructive"
              size="sm"
            >
              <RotateCcw className="w-4 h-4 mr-2" />
              Emergency Rollback
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Approval Confirmation Dialog */}
      <Dialog open={activeDialog === 'approve'} onOpenChange={(open) => !open && setActiveDialog(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Approve & Rollout to 100%</DialogTitle>
          </DialogHeader>

          <div className="space-y-4">
            <div className="p-3 bg-green-500/10 border border-green-500/20 rounded-lg">
              <p className="text-sm text-muted-foreground">
                This will immediately promote <strong>{skillId}@{currentVersion}</strong> from
                canary (10%) to full production (100%).
              </p>
            </div>

            <div className="space-y-1 text-sm">
              <p className="font-semibold">This action:</p>
              <ul className="list-disc pl-5 text-muted-foreground">
                <li>Routes all new traffic to v{currentVersion}</li>
                <li>Terminates the canary window</li>
                <li>Is <strong>immediately irreversible</strong> (use emergency rollback if issues arise)</li>
              </ul>
            </div>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => setActiveDialog(null)} disabled={loading}>
              Cancel
            </Button>
            <Button
              onClick={handleApprove}
              disabled={loading}
              className="bg-green-600 hover:bg-green-700 text-white"
            >
              {loading ? 'Approving...' : 'Approve'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Defer Dialog */}
      <Dialog open={activeDialog === 'defer'} onOpenChange={(open) => !open && setActiveDialog(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Defer & Keep Previous Version</DialogTitle>
          </DialogHeader>

          <div className="space-y-4">
            <p className="text-sm text-muted-foreground">
              Keep running the previous version. The canary will be stopped and marked as
              deferred.
            </p>

            <div>
              <label className="text-sm font-semibold mb-2 block">Reason (optional)</label>
              <Textarea
                placeholder="Why are you deferring this version? (helps with decision analysis)"
                value={deferReason}
                onChange={(e) => setDeferReason(e.target.value)}
                className="min-h-24"
              />
            </div>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => setActiveDialog(null)} disabled={loading}>
              Cancel
            </Button>
            <Button onClick={handleDefer} disabled={loading} variant="secondary">
              {loading ? 'Deferring...' : 'Defer'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Rollback Confirmation Dialog */}
      <Dialog open={activeDialog === 'rollback'} onOpenChange={(open) => !open && setActiveDialog(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Emergency Rollback</DialogTitle>
          </DialogHeader>

          <div className="space-y-4">
            <div className="p-3 bg-red-500/10 border border-red-500/20 rounded-lg flex gap-2">
              <AlertCircle className="w-5 h-5 text-red-600 dark:text-red-400 flex-shrink-0 mt-0.5" />
              <div className="text-sm text-muted-foreground">
                This immediately reverts to the stable version and disables this canary. Use
                only in emergencies.
              </div>
            </div>

            <div className="space-y-1 text-sm">
              <p className="font-semibold">This action:</p>
              <ul className="list-disc pl-5 text-muted-foreground">
                <li>Instantly halts the canary deployment</li>
                <li>Routes all traffic back to the stable version</li>
                <li>Marks this version as rolled back in audit logs</li>
              </ul>
            </div>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => setActiveDialog(null)} disabled={loading}>
              Cancel
            </Button>
            <Button onClick={handleRollback} disabled={loading} variant="destructive">
              {loading ? 'Rolling back...' : 'Confirm Rollback'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
};

export default OperatorControls;
