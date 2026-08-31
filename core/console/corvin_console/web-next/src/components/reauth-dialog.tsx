import * as React from "react";
import { Loader2, ShieldAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

interface ReauthDialogProps {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  title?: string;
  description?: string;
  /** Called on confirmation. Must throw on failure; dialog stays open. */
  onConfirm: (token: string) => Promise<void>;
}

/**
 * Confirmation prompt for sensitive mutations (Persona-Edit, Promote,
 * Memory-Edit, Settings-Edit). Token re-auth was removed with static
 * atlr_* auth — the backend verify_reauth stub accepts any session.
 * This dialog now acts as a deliberate "are you sure?" gate only.
 */
export function ReauthDialog({
  open,
  onOpenChange,
  title = "Confirm sensitive change",
  description = "This action cannot be undone. Confirm to proceed.",
  onConfirm,
}: ReauthDialogProps) {
  const [error, setError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);

  React.useEffect(() => {
    if (!open) {
      setError(null);
      setBusy(false);
    }
  }, [open]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await onConfirm("");
      onOpenChange(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Action failed.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(v) => !busy && onOpenChange(v)}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <ShieldAlert className="h-5 w-5 text-accent" />
            {title}
          </DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="space-y-3">
          {error && (
            <p className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
              {error}
            </p>
          )}
          <DialogFooter>
            <Button
              type="button"
              variant="ghost"
              onClick={() => onOpenChange(false)}
              disabled={busy}
            >
              Cancel
            </Button>
            <Button type="submit" variant="accent" disabled={busy}>
              {busy ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" /> Working…
                </>
              ) : (
                "Confirm"
              )}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
