import React, { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { CheckCircle, Clock, FileText, Pause, PlayCircle, RotateCcw } from 'lucide-react';
import type { CanaryAction, CanaryView } from '@/lib/api/autonomous-forge';
import { getCandidate } from '@/lib/api/autonomous-forge';
import { isActive } from './autonomous-forge-encoding';

interface OperatorControlsProps {
  canary: CanaryView;
  busy: boolean;
  onAction: (action: CanaryAction) => Promise<void>;
}

const CONFIRM: Partial<Record<CanaryAction, { title: string; body: string }>> = {
  approve: {
    title: 'Approve the candidate?',
    body: 'The candidate becomes the live skill for every chat. Its ratings from this canary become the skill\'s ratings. The previous version is kept and can be restored with Rollback.',
  },
  rollback: {
    title: 'Roll back?',
    body: 'A running canary stops serving the candidate. An approved canary restores the previous version and its ratings.',
  },
  defer: {
    title: 'Defer the candidate?',
    body: 'The canary ends without a rollout; every chat gets the live skill again.',
  },
};

export const OperatorControls: React.FC<OperatorControlsProps> = ({ canary, busy, onAction }) => {
  const [confirm, setConfirm] = useState<CanaryAction | null>(null);
  const [bodies, setBodies] = useState<{ live_body: string; candidate_body: string } | null>(null);
  const [bodiesOpen, setBodiesOpen] = useState(false);
  const active = isActive(canary.status);

  const run = async (action: CanaryAction) => {
    if (CONFIRM[action] && confirm !== action) {
      setConfirm(action);
      return;
    }
    setConfirm(null);
    await onAction(action);
  };

  const showBodies = async () => {
    setBodiesOpen(true);
    if (!bodies) setBodies(await getCandidate(canary.skill_id));
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Decision</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        <Button variant="outline" className="w-full justify-start" onClick={showBodies}>
          <FileText className="w-4 h-4 mr-2" /> Compare live and candidate
        </Button>
        {active && (
          <>
            <Button className="w-full justify-start" disabled={busy} onClick={() => run('approve')}>
              <CheckCircle className="w-4 h-4 mr-2" /> Approve candidate
            </Button>
            {canary.status === 'paused' ? (
              <Button variant="outline" className="w-full justify-start" disabled={busy} onClick={() => run('resume')}>
                <PlayCircle className="w-4 h-4 mr-2" /> Resume canary
              </Button>
            ) : (
              <Button variant="outline" className="w-full justify-start" disabled={busy} onClick={() => run('pause')}>
                <Pause className="w-4 h-4 mr-2" /> Pause canary
              </Button>
            )}
            <Button variant="outline" className="w-full justify-start" disabled={busy} onClick={() => run('defer')}>
              <Clock className="w-4 h-4 mr-2" /> Defer
            </Button>
          </>
        )}
        {(active || canary.status === 'approved') && (
          <Button variant="destructive" className="w-full justify-start" disabled={busy} onClick={() => run('rollback')}>
            <RotateCcw className="w-4 h-4 mr-2" /> Roll back
          </Button>
        )}
        {!active && canary.status !== 'approved' && (
          <p className="text-sm text-muted-foreground">This canary is finished.</p>
        )}
      </CardContent>

      <Dialog open={confirm !== null} onOpenChange={(o) => !o && setConfirm(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{confirm ? CONFIRM[confirm]?.title : ''}</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">{confirm ? CONFIRM[confirm]?.body : ''}</p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirm(null)}>Cancel</Button>
            <Button disabled={busy} onClick={() => confirm && run(confirm)}>Confirm</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={bodiesOpen} onOpenChange={setBodiesOpen}>
        <DialogContent className="max-w-5xl">
          <DialogHeader>
            <DialogTitle className="font-mono text-base">{canary.skill_id}</DialogTitle>
          </DialogHeader>
          <div className="grid gap-4 md:grid-cols-2">
            {(['live_body', 'candidate_body'] as const).map((k) => (
              <div key={k} className="min-w-0">
                <p className="text-xs font-medium mb-1">{k === 'live_body' ? 'Live (at canary start)' : 'Candidate'}</p>
                <pre className="text-xs whitespace-pre-wrap break-words rounded-md border bg-muted/40 p-3 max-h-[60vh] overflow-y-auto">
                  {bodies ? bodies[k] : 'Loading…'}
                </pre>
              </div>
            ))}
          </div>
        </DialogContent>
      </Dialog>
    </Card>
  );
};

export default OperatorControls;
