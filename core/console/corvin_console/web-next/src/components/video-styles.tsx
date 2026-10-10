/**
 * Video styles in the Video Producer panel: the composer's style chip, the import-and-edit dialog, and the
 * "Styles" list. The server owns every style (routes/video_producer_api.py `/video/styles*`); this file only
 * edits a draft it was handed and sends the whole draft back. Nothing is invented: no styles, no previews.
 */
import { useEffect, useRef, useState } from "react";
import { AlertCircle, Check, ChevronDown, Loader2, Palette, Trash2, Upload } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { api, ApiError } from "@/lib/api/client";
import {
  BUILTIN_STYLE_ID, FONT_FAMILIES, MIN_DIM_CONTRAST, MIN_HIGHLIGHT_CONTRAST, MIN_MUTED_CONTRAST, MIN_TEXT_CONTRAST, contrastRatio, dimmedText, importStyleDeck, isHex, removeLogo, setBrand, setColour,
  setFamily, setName, setTopLevel,
  type ColourKey, type Draft, type FontRole, type StyleList, type StylePreview, type StyleSummary, type Theme,
} from "@/lib/api/video-styles";

const BASE = "/video";
const msg = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);
const SWATCHES: Array<[ColourKey, string]> = [
  ["accent", "Accent"], ["accent_hi", "Accent highlight"], ["bg", "Background"], ["bg_card", "Card background"], ["text", "Text"], ["text_muted", "Muted text"],
];
const FONT_ROLES: Array<[FontRole, string]> = [["heading_family", "Headings"], ["body_family", "Body"], ["mono_family", "Code"]];

// ── Composer chip ────────────────────────────────────────────────────────────

export function StyleChip({ list, label, picked, onPick, onImport, importDisabledReason }: {
  list: StyleList | undefined; label: string; picked: string | null;
  onPick: (id: string) => void; onImport: () => void; importDisabledReason: string | null;
}) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => { if (!box.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [open]);
  const items = [{ id: BUILTIN_STYLE_ID, name: list?.builtin.name ?? "CorvinOS" }, ...(list?.styles ?? [])];
  return (
    <div ref={box} className="relative" onKeyDown={(e) => { if (e.key === "Escape") setOpen(false); }}>
      <button type="button" aria-haspopup="listbox" aria-expanded={open} data-testid="style-chip" onClick={() => setOpen((o) => !o)}
        className="px-2.5 py-1 rounded-md inline-flex items-center gap-1 max-w-[16rem] text-muted-foreground hover:text-foreground border border-border">
        <Palette className="w-3 h-3 shrink-0" /><span className="truncate">Style: {label}</span><ChevronDown className="w-3 h-3 shrink-0" />
      </button>
      {open && (
        <div role="listbox" aria-label="Video style" data-testid="style-menu" className="absolute z-20 bottom-full mb-1 left-0 min-w-[14rem] max-w-[20rem] rounded-md border border-border bg-card shadow-lg p-1">
          {items.map((s) => (
            <button key={s.id} type="button" role="option" aria-selected={picked === s.id} data-testid={`style-option-${s.id}`}
              onClick={() => { onPick(s.id); setOpen(false); }}
              className="w-full flex items-center gap-2 text-left text-xs px-2 py-1.5 rounded hover:bg-muted/60">
              <Check className={`w-3 h-3 shrink-0 ${picked === s.id ? "" : "invisible"}`} />
              <span className="truncate">{s.name}</span>
              {list?.default_style_id === s.id && <span className="ml-auto text-muted-foreground">default</span>}
            </button>
          ))}
          <div className="border-t border-border mt-1 pt-1">
            <button type="button" data-testid="style-import" disabled={!!importDisabledReason} title={importDisabledReason ?? undefined}
              onClick={() => { setOpen(false); onImport(); }}
              className="w-full flex items-center gap-2 text-left text-xs px-2 py-1.5 rounded hover:bg-muted/60 disabled:opacity-50 disabled:hover:bg-transparent">
              <Upload className="w-3 h-3 shrink-0" /> Import from PowerPoint…
            </button>
            {importDisabledReason && <p className="px-2 pb-1 text-xs text-muted-foreground">{importDisabledReason}</p>}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Import / edit dialog ─────────────────────────────────────────────────────

function PreviewFrames({ previews, busy, loading }: { previews: StylePreview[]; busy: boolean; loading: boolean }) {
  if (loading) {
    return <div className="grid grid-cols-3 gap-2" data-testid="preview-skeleton">{[0, 1, 2].map((i) => <Skeleton key={i} className="aspect-video w-full" />)}</div>;
  }
  if (previews.length === 0) {
    return <p className="text-sm text-muted-foreground rounded-md border border-dashed border-border p-4" data-testid="previews-empty">Previews are not available on this installation.</p>;
  }
  return (
    <div className={`grid grid-cols-3 gap-2 transition-opacity ${busy ? "opacity-50" : ""}`} aria-busy={busy} data-testid="preview-frames">
      {previews.map((p) => (
        <figure key={`${p.template}-${p.theme}`}>
          <img src={p.data_uri} alt={`${p.template} sample frame`} className="aspect-video w-full rounded-md border border-border object-cover bg-muted/40" />
          <figcaption className="text-xs text-muted-foreground mt-1 capitalize">{p.template}</figcaption>
        </figure>
      ))}
    </div>
  );
}

export function StyleImportDialog({ file, csrf, onClose, onSaved }: {
  file: File; csrf: string; onClose: () => void; onSaved: (style: StyleSummary, setDefault: boolean) => void;
}) {
  const [draft, setDraft] = useState<Draft | null>(null);
  const [previews, setPreviews] = useState<StylePreview[]>([]);
  const [notes, setNotes] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [previewing, setPreviewing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const edited = useRef(false);
  const cardByHand = useRef(false); // once the card colour was chosen here, Background/Text edits stop re-deriving it

  useEffect(() => {
    const ac = new AbortController();
    importStyleDeck(file, csrf, ac.signal)
      .then((r) => { setDraft(r.draft); setPreviews(r.previews ?? []); setNotes(r.notes ?? []); })
      .catch((e) => { if (!ac.signal.aborted) setError(msg(e, "The deck could not be read.")); })
      .finally(() => { if (!ac.signal.aborted) setLoading(false); });
    return () => ac.abort();
  }, [file, csrf]);

  // Debounced re-render after an edit; a newer edit cancels the request in flight.
  useEffect(() => {
    if (!draft || !edited.current) return;
    const ac = new AbortController();
    const t = setTimeout(() => {
      setPreviewing(true); setPreviewError(null);
      api<{ previews: StylePreview[] }>(`${BASE}/styles/preview`, { method: "POST", csrf, body: { draft }, signal: ac.signal })
        .then((r) => { setPreviews(r.previews ?? []); })
        .catch((e) => { if (!ac.signal.aborted) setPreviewError(msg(e, "")); })
        .finally(() => { if (!ac.signal.aborted) setPreviewing(false); });
    }, 600);
    return () => { clearTimeout(t); ac.abort(); };
  }, [draft, csrf]);

  const edit = (fn: (d: Draft) => Draft) => { edited.current = true; setDraft((d) => (d ? fn(d) : d)); };
  const theme: Theme = draft?.default_theme === "light" ? "light" : "dark";
  const tok = draft?.tokens?.[theme] ?? {};
  const name: string = draft?.name ?? "";
  const canSave = !!draft && name.trim().length > 0 && !saving && !loading;

  const save = async (setDefault: boolean) => {
    if (!draft || !canSave) return;
    setSaving(true); setError(null);
    try {
      const r = await api<{ style: StyleSummary }>(`${BASE}/styles`, { method: "POST", csrf, body: { draft: setName(draft, name.trim()), set_default: setDefault } });
      onSaved(r.style, setDefault);
    } catch (e) { setError(msg(e, "The style could not be saved.")); setSaving(false); }
  };

  // [id, label, foreground, background, floor]; the floors mirror the server's validate_style.
  const hints: Array<[string, string, string | undefined, string | undefined, number]> = [
    ["text", "Text on background", tok.text, tok.bg, MIN_TEXT_CONTRAST],
    ["text_muted", "Muted text on background", tok.text_muted, tok.bg, MIN_MUTED_CONTRAST],
    ["card-text", "Text on cards", tok.text, tok.bg_card, MIN_TEXT_CONTRAST],
    ["card-muted", "Muted text on cards", tok.text_muted, tok.bg_card, MIN_MUTED_CONTRAST],
    ["accent", "Accent on background", tok.accent, tok.bg, MIN_MUTED_CONTRAST],
    ["accent_hi", "Accent highlight on background", tok.accent_hi, tok.bg, MIN_HIGHLIGHT_CONTRAST],
    ["dim", "Out-of-focus items on background", isHex(tok.bg) && isHex(tok.text) ? dimmedText(tok.bg, tok.text) : undefined, tok.bg, MIN_DIM_CONTRAST],
  ];

  return (
    <Dialog open onOpenChange={(o) => { if (!o && !saving) onClose(); }}>
      <DialogContent className="max-w-3xl" data-testid="style-dialog">
        <DialogHeader>
          <DialogTitle>Use “{file.name}” as a video style</DialogTitle>
          <DialogDescription>Colours, fonts and logo are read from the deck. Slide text and notes are never used.</DialogDescription>
        </DialogHeader>

        {loading && (
          <div className="space-y-3" data-testid="style-loading" role="status">
            <div className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="w-4 h-4 animate-spin" /> Reading the deck…</div>
            <PreviewFrames previews={[]} busy={false} loading />
          </div>
        )}

        {!loading && !draft && (
          <p className="text-sm text-destructive flex items-start gap-2" role="alert" data-testid="style-error"><AlertCircle className="w-4 h-4 mt-0.5 shrink-0" /> {error}</p>
        )}

        {draft && (
          <div className="space-y-4">
            {(draft.warnings?.length > 0 || notes.length > 0) && (
              <ul className="text-sm rounded-md border border-amber-500/30 bg-amber-500/10 p-3 space-y-1" data-testid="style-warnings">
                {[...(draft.warnings ?? []), ...notes].map((w: string, i: number) => <li key={i}>{w}</li>)}
              </ul>
            )}

            <PreviewFrames previews={previews} busy={previewing} loading={false} />
            {previewError !== null && <p className="text-xs text-muted-foreground" data-testid="preview-error">
              {previewError ? `The previews could not be updated: ${previewError}` : "The previews could not be updated; your edits are kept."}</p>}

            <div className="grid gap-3 sm:grid-cols-2">
              <label className="text-sm"><span className="text-xs text-muted-foreground">Name</span>
                <input value={name} maxLength={60} onChange={(e) => edit((d) => setName(d, e.target.value))} data-testid="style-name" aria-invalid={!name.trim()}
                  className="w-full mt-1 px-3 py-2 rounded-md border border-border bg-background text-sm" /></label>
              <label className="text-sm"><span className="text-xs text-muted-foreground">Wordmark (shown in the footer of every slide)</span>
                <input value={draft.brand?.wordmark ?? ""} maxLength={40} onChange={(e) => edit((d) => setBrand(d, "wordmark", e.target.value))} data-testid="style-wordmark"
                  className="w-full mt-1 px-3 py-2 rounded-md border border-border bg-background text-sm" /></label>
              <label className="text-sm"><span className="text-xs text-muted-foreground">Default theme</span>
                <select value={theme} onChange={(e) => edit((d) => setTopLevel(d, "default_theme", e.target.value))} data-testid="style-theme"
                  className="w-full mt-1 px-3 py-2 rounded-md border border-border bg-background text-sm"><option value="dark">Dark</option><option value="light">Light</option></select>
                <span className="block text-xs text-muted-foreground mt-1" data-testid="theme-help">Videos use this theme throughout.</span></label>
              <label className="text-sm"><span className="text-xs text-muted-foreground">Decoration</span>
                <select value={draft.decor ?? "minimal"} onChange={(e) => edit((d) => setTopLevel(d, "decor", e.target.value))} data-testid="style-decor"
                  className="w-full mt-1 px-3 py-2 rounded-md border border-border bg-background text-sm">
                  <option value="corvin">CorvinOS ornaments</option><option value="minimal">Minimal</option><option value="none">None</option></select></label>
            </div>

            <fieldset className="space-y-2">
              <legend className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Colours ({theme})</legend>
              <div className="grid gap-2 sm:grid-cols-3" data-testid="style-swatches">
                {SWATCHES.map(([key, label]) => {
                  const v = tok[key];
                  return (
                    <label key={key} className="flex items-center gap-2 text-sm rounded-md border border-border p-2">
                      <input type="color" value={isHex(v) ? v : "#000000"} aria-label={`${label} colour`} data-testid={`swatch-${key}`}
                        onChange={(e) => { if (key === "bg_card") cardByHand.current = true; const hex = e.target.value; edit((d) => setColour(d, theme, key, hex, { deriveCard: !cardByHand.current })); }} className="h-7 w-9 shrink-0 cursor-pointer rounded border border-border bg-transparent p-0" />
                      <span className="min-w-0"><span className="block truncate">{label}</span><span className="block text-xs text-muted-foreground font-mono">{isHex(v) ? v : "—"}</span></span>
                    </label>
                  );
                })}
              </div>
              <ul className="text-xs space-y-0.5" data-testid="contrast-hints">
                {hints.map(([id, label, fg, bg, min]) => {
                  if (!isHex(fg) || !isHex(bg)) return null;
                  const r = contrastRatio(fg, bg);
                  const bad = r < min;
                  return <li key={id} data-testid={`contrast-${id}`} className={bad ? "text-amber-700 dark:text-amber-400" : "text-muted-foreground"}>
                    {label}: {r.toFixed(1)}:1{bad ? ` — below ${min}:1, this will be hard to read` : ""}</li>;
                })}
              </ul>
            </fieldset>

            <fieldset className="space-y-2">
              <legend className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Fonts</legend>
              <div className="grid gap-2 sm:grid-cols-3">
                {FONT_ROLES.map(([role, label]) => (
                  <label key={role} className="text-sm"><span className="text-xs text-muted-foreground">{label}</span>
                    <select value={draft.tokens?.typography?.[role] ?? ""} onChange={(e) => edit((d) => setFamily(d, role, e.target.value))} data-testid={`font-${role}`}
                      className="w-full mt-1 px-3 py-2 rounded-md border border-border bg-background text-sm">
                      {FONT_FAMILIES.map((f) => <option key={f} value={f}>{f}</option>)}</select></label>
                ))}
              </div>
              {(draft.fonts?.mapping?.length ?? 0) > 0 && (
                <ul className="text-xs text-muted-foreground space-y-0.5" data-testid="font-mapping">
                  {draft.fonts.mapping.map((m: { from: string; to: string; reason: string }, i: number) => <li key={i}>{m.from} → {m.to}{m.reason ? ` (${m.reason})` : ""}</li>)}
                </ul>
              )}
            </fieldset>

            <fieldset className="space-y-2">
              <legend className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Logo and credit</legend>
              <div className="flex flex-wrap items-center gap-4 text-sm">
                {draft.mark_png_b64 ? (
                  <span className="inline-flex items-center gap-2" data-testid="style-logo">
                    <img src={`data:image/png;base64,${draft.mark_png_b64}`} alt="Logo found in the deck" className="h-10 max-w-[8rem] object-contain rounded border border-border bg-muted/40 p-1" />
                    <Button type="button" variant="outline" size="sm" onClick={() => edit(removeLogo)} data-testid="remove-logo">Remove logo</Button>
                  </span>
                ) : <span className="text-muted-foreground" data-testid="style-no-logo">No logo in this style.</span>}
                <label className="inline-flex items-center gap-2"><input type="checkbox" checked={!!draft.brand?.intro_mark} disabled={!draft.mark_png_b64} data-testid="style-intro-mark"
                  onChange={(e) => edit((d) => setBrand(d, "intro_mark", e.target.checked))} /> Show the logo on the intro</label>
                <label className="inline-flex items-center gap-2"><input type="checkbox" checked={!!draft.brand?.credit} data-testid="style-credit"
                  onChange={(e) => edit((d) => setBrand(d, "credit", e.target.checked))} /> Add “made with CorvinOS” credit</label>
              </div>
            </fieldset>

            {error && <p className="text-sm text-destructive flex items-start gap-2" role="alert" data-testid="style-error"><AlertCircle className="w-4 h-4 mt-0.5 shrink-0" /> {error}</p>}
          </div>
        )}

        <DialogFooter className="gap-2 sm:gap-2">
          <Button type="button" variant="ghost" onClick={onClose} disabled={saving} data-testid="style-cancel">Cancel</Button>
          {draft && <>
            <Button type="button" variant="outline" disabled={!canSave} onClick={() => void save(true)} data-testid="style-save-default">Save and set as default</Button>
            <Button type="button" variant="accent" disabled={!canSave} onClick={() => void save(false)} data-testid="style-save">
              {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : "Save"}</Button>
          </>}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ── "Styles" section of the drawer ───────────────────────────────────────────

function StyleRow({ s, isDefault, csrf, onChanged }: { s: StyleSummary; isDefault: boolean; csrf: string; onChanged: () => void }) {
  const [thumb, setThumb] = useState<string | null>(null);
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const ac = new AbortController();
    api<{ previews: StylePreview[] }>(`${BASE}/styles/${s.id}/preview`, { signal: ac.signal })
      .then((r) => setThumb(r.previews?.[0]?.data_uri ?? null)).catch(() => { /* no thumbnail: the row stays text-only */ });
    return () => ac.abort();
  }, [s.id]);
  const run = async (fn: () => Promise<unknown>, fallback: string) => {
    setBusy(true); setError(null);
    try { await fn(); onChanged(); } catch (e) { setError(msg(e, fallback)); } finally { setBusy(false); setConfirm(false); }
  };
  return (
    <li className="flex items-center gap-3 rounded-lg border border-border p-2" data-testid={`style-row-${s.id}`}>
      <div className="w-24 aspect-video shrink-0 rounded-md overflow-hidden border border-border bg-muted/40">
        {thumb && <img src={thumb} alt="" className="w-full h-full object-cover" />}
      </div>
      {s.mark_data_uri && <img src={s.mark_data_uri} alt={`${s.name} logo`} className="h-8 max-w-[4rem] object-contain" />}
      <div className="min-w-0 flex-1">
        <div className="text-sm font-medium truncate">{s.name}{isDefault && <span className="ml-2 text-xs font-normal text-muted-foreground">default</span>}</div>
        <div className="text-xs text-muted-foreground">From {s.source.kind === "tokens" ? "design tokens" : `a .${s.source.kind} deck`}{s.warnings.length ? ` · ${s.warnings.length} note${s.warnings.length === 1 ? "" : "s"}` : ""}</div>
        {error && <div className="text-xs text-destructive" role="alert">{error}</div>}
      </div>
      {confirm ? (
        <span className="flex items-center gap-1 text-xs">Delete “{s.name}”?
          <Button type="button" variant="destructive" size="sm" disabled={busy} data-testid={`style-delete-confirm-${s.id}`} onClick={() => void run(() => api(`${BASE}/styles/${s.id}`, { method: "DELETE", csrf }), "The style could not be deleted.")}>Delete</Button>
          <Button type="button" variant="ghost" size="sm" disabled={busy} onClick={() => setConfirm(false)}>Keep</Button>
        </span>
      ) : (
        <span className="flex items-center gap-1">
          {!isDefault && <Button type="button" variant="outline" size="sm" disabled={busy || !csrf} data-testid={`style-default-${s.id}`}
            onClick={() => void run(() => api(`${BASE}/styles/default`, { method: "PUT", csrf, body: { style_id: s.id } }), "The default could not be changed.")}>Set as default</Button>}
          {isDefault && <Button type="button" variant="ghost" size="sm" disabled={busy || !csrf} data-testid="style-clear-default"
            onClick={() => void run(() => api(`${BASE}/styles/default`, { method: "PUT", csrf, body: { style_id: null } }), "The default could not be changed.")}>Use CorvinOS by default</Button>}
          <Button type="button" variant="ghost" size="sm" aria-label={`Delete ${s.name}`} disabled={busy || !csrf} data-testid={`style-delete-${s.id}`} onClick={() => setConfirm(true)}><Trash2 className="w-4 h-4" /></Button>
        </span>
      )}
    </li>
  );
}

export function StylesSection({ list, loading, unavailable, csrf, importDisabledReason, onImport, onChanged }: {
  list: StyleList | undefined; loading: boolean; unavailable: boolean; csrf: string;
  importDisabledReason: string | null; onImport: () => void; onChanged: () => void;
}) {
  return (
    <div className="space-y-3" data-testid="styles-section">
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs text-muted-foreground">A style gives your videos your own colours, fonts and logo. Import a PowerPoint deck or template to create one.</p>
        <Button type="button" variant="outline" size="sm" className="shrink-0" disabled={!!importDisabledReason || !csrf || unavailable} title={importDisabledReason ?? undefined} data-testid="styles-import" onClick={onImport}>
          <Upload className="w-4 h-4" /> Import from PowerPoint…</Button>
      </div>
      {importDisabledReason && <p className="text-xs text-muted-foreground" data-testid="styles-quota">{importDisabledReason}</p>}
      {list?.default_style_error && <p className="text-xs text-destructive" role="alert" data-testid="styles-default-error">{list.default_style_error}</p>}
      {unavailable ? <p className="text-sm text-muted-foreground" data-testid="styles-unavailable">Custom styles are not available on this installation.</p>
        : loading ? <Skeleton className="h-16 w-full" />
        : (list?.styles.length ?? 0) === 0 ? <p className="text-sm text-muted-foreground" data-testid="styles-empty">No styles yet. Videos use the built-in CorvinOS look.</p>
        : <ul className="space-y-2" data-testid="styles-list">{list!.styles.map((s) => <StyleRow key={s.id} s={s} isDefault={list!.default_style_id === s.id} csrf={csrf} onChanged={onChanged} />)}</ul>}
    </div>
  );
}
