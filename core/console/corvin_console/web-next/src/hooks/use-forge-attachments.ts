/** Attachment state for a Forge composer (ADR-2254): upload one by one, show the intake verdict, hand back the usable ids. */
import { useCallback, useMemo, useState } from "react";
import type { ComposerAttachment, ComposerAttachments } from "@/components/forge/ForgeComposer";
import { intakeNote, removeForgeAttachment, uploadForgeAttachment } from "@/lib/api/forge-creator";

export function useForgeAttachments(csrf: string, maxItems = 5) {
  const [items, setItems] = useState<ComposerAttachment[]>([]);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const addFiles = useCallback(async (list: File[]) => {
    setUploading(true);
    setError(null);
    try {
      for (const f of list) {
        const r = await uploadForgeAttachment(f, csrf);
        setItems((cur) => [...cur, { id: r.item_id, name: f.name, size: f.size, verdict: r.verdict, note: intakeNote(r) }]);
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setUploading(false);
    }
  }, [csrf]);

  const remove = useCallback((id: string) => {
    setItems((cur) => cur.filter((f) => f.id !== id));
    void removeForgeAttachment(id, csrf).catch(() => undefined);
  }, [csrf]);

  const controller = useMemo<ComposerAttachments>(
    () => ({ items, addFiles, remove, uploading, error, maxItems }),
    [items, addFiles, remove, uploading, error, maxItems],
  );
  const ids = useMemo(() => items.filter((f) => f.verdict !== "refused").map((f) => f.id), [items]);
  const clear = useCallback(() => setItems([]), []);
  return { controller, ids, clear };
}
