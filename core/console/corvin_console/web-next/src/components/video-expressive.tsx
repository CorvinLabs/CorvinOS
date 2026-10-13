/**
 * Video Producer — the expressive-scene tabs of the studio (ADR-2249): Code, Canvas, Assets.
 *
 *   Code    the selected video's highlighted-code scenes (rendered template `code`, read from the
 *           job's cue log) as their real slides, and how to ask for one;
 *   Canvas  the selected video's canvas scenes, the canvas signals the job recorded
 *           (canvas_clean, canvas_authoring) and how often a canvas walked the degrade ladder;
 *   Assets  this workspace's uploaded images (GET/POST/DELETE /video/assets): upload a PNG with a
 *           caption, an optional source and hotspots placed by clicking the picture. A `still`
 *           scene can show an asset; the storyboard only ever reads the caption and hotspot names.
 *
 * Nothing is invented: a video without such a scene says so, a missing report says so, and an
 * asset is exactly what the store returned.
 */
import { useEffect, useId, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Globe, Loader2, Trash2, Upload, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api, ApiError } from "@/lib/api/client";
import {
  ASSET_ACCEPT, assetImageUrl, CAPTURE_VIEWPORTS, CAPTURE_WAIT_S, canvasAuthoring, captureAsset, captureBlockedReason,
  clickToPoint, fmtAssetBytes, nextSpotName, sceneSlideUrl, scenesWithTemplate, importPenpotBoard, updateAsset,
  uploadAsset, uploadProblem, type Asset, type AssetHotspot, type AssetList, type CaptureSettings, type CaptureViewport,
} from "@/lib/api/video-assets";
import { signalRows, type ProductionReport } from "@/lib/video-quality-signals";

type Meta = Record<string, unknown> | null | undefined;

function SceneSlides({ jobId, indexes, kind }: { jobId: string; indexes: number[]; kind: string }) {
  return (
    <ul className="grid grid-cols-1 md:grid-cols-2 gap-3" data-testid={`${kind}-scenes`}>
      {indexes.map((i) => (
        <li key={i} className="rounded-lg border border-border p-2" data-testid={`${kind}-scene-${i}`}>
          <div className="aspect-video rounded-md overflow-hidden bg-muted/40">
            <img src={sceneSlideUrl(jobId, i)} alt={`Scene ${i}`} className="w-full h-full object-contain" loading="lazy" />
          </div>
          <div className="text-xs text-muted-foreground mt-1">Scene {i} · last frame of the slide</div>
        </li>
      ))}
    </ul>
  );
}

export function CodeTab({ jobId, metadata }: { jobId: string; metadata: Meta }) {
  const idx = scenesWithTemplate(metadata, "code");
  return (
    <div className="space-y-3" data-testid="code-tab">
      <p className="text-sm text-muted-foreground">
        A code scene shows source text exactly as written: tokens coloured, lines revealed as the narration reaches them,
        a diff marks added and removed lines, and annotations point at lines. Code is never executed. To get one, ask for
        it in the task, for example "walk through this function line by line", and paste or attach the code.
      </p>
      {metadata == null ? <p className="text-sm text-muted-foreground" data-testid="code-unmeasured">Scene templates are read once the video is measured.</p>
        : idx.length === 0 ? <p className="text-sm text-muted-foreground" data-testid="code-none">This video has no code scene.</p>
        : <SceneSlides jobId={jobId} indexes={idx} kind="code" />}
    </div>
  );
}

export function CanvasTab({ jobId, metadata, report }: { jobId: string; metadata: Meta; report: ProductionReport | undefined }) {
  const idx = scenesWithTemplate(metadata, "canvas");
  const authoring = canvasAuthoring(metadata);
  const sigs = report?.state === "present" ? report.signals.filter((s) => s.id === "canvas_clean" || s.id === "canvas_authoring") : [];
  const rows = sigs.length ? signalRows({ ...report!, signals: sigs }) : [];
  const ladder = report?.degradations_by_kind?.canvas_degraded ?? 0;
  return (
    <div className="space-y-3" data-testid="canvas-tab">
      <p className="text-sm text-muted-foreground">
        A canvas is drawn with a pen while the narration speaks: shapes, arrows, braces and handwritten labels from a fixed
        vocabulary, placed on a grid. A canvas that collides after layout is repaired or simplified, never shipped silently.
        Ask for a sketch, a metaphor or a whiteboard that builds up over several scenes.
      </p>
      {rows.length > 0 && (
        <ul className="space-y-1 text-sm" data-testid="canvas-signals">
          {rows.map((r) => (
            <li key={r.id} data-testid={`canvas-signal-${r.id}`}><span className="font-medium">{r.label}</span>: {r.value} · {r.verdictText} · {r.basis}</li>
          ))}
        </ul>
      )}
      {authoring && (
        <p className="text-sm" data-testid="canvas-authoring">
          The storyboard model wrote {authoring.total} canvas{authoring.total === 1 ? "" : "es"}: {authoring.first_try} valid on the first try, {authoring.after_repair} after the repair round.
        </p>
      )}
      {ladder > 0 && <p className="text-sm" data-testid="canvas-ladder">{ladder} canvas step{ladder === 1 ? "" : "s"} walked the degrade ladder (see the Quality tab).</p>}
      {metadata == null ? <p className="text-sm text-muted-foreground" data-testid="canvas-unmeasured">Scene templates are read once the video is measured.</p>
        : idx.length === 0 ? <p className="text-sm text-muted-foreground" data-testid="canvas-none">This video has no canvas scene.</p>
        : <SceneSlides jobId={jobId} indexes={idx} kind="canvas" />}
    </div>
  );
}

const BASE = "/video";

export function AssetsTab({ csrf }: { csrf: string }) {
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ["video", "assets"], queryFn: ({ signal }) => api<AssetList>(`${BASE}/assets`, { signal }), retry: false });
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [caption, setCaption] = useState("");
  const [source, setSource] = useState("");
  const [spots, setSpots] = useState<AssetHotspot[]>([]);
  const [confirm, setConfirm] = useState<string | null>(null);
  const [showCaptureDialog, setShowCaptureDialog] = useState(false);
  const [editing, setEditing] = useState<Asset | null>(null);
  const settings = useQuery({ queryKey: ["video", "settings"], queryFn: ({ signal }) => api<CaptureSettings>(`${BASE}/settings`, { signal }), retry: false });
  const captureBlocked = settings.isError ? "Website capture is not available on this installation." : captureBlockedReason(settings.data);
  const picker = useRef<HTMLInputElement>(null);
  const imgRef = useRef<HTMLImageElement>(null);

  const reset = () => { if (preview) URL.revokeObjectURL(preview); setFile(null); setPreview(null); setCaption(""); setSource(""); setSpots([]); };
  const upload = useMutation({
    mutationFn: () => uploadAsset(file!, { caption, source, hotspots: spots }, csrf),
    onSuccess: () => { reset(); void qc.invalidateQueries({ queryKey: ["video", "assets"] }); },
  });
  const penpotPicker = useRef<HTMLInputElement>(null);
  const penpot = useMutation({
    mutationFn: (f: File) => importPenpotBoard(f, csrf),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ["video", "assets"] }); },
  });
  const del = useMutation({
    mutationFn: (id: string) => api(`${BASE}/assets/${encodeURIComponent(id)}`, { method: "DELETE", csrf }),
    onSuccess: () => { setConfirm(null); void qc.invalidateQueries({ queryKey: ["video", "assets"] }); },
  });

  if (list.isError) {
    return <p className="text-sm text-muted-foreground" data-testid="assets-unavailable">Uploaded images are not available on this installation.</p>;
  }
  const limits = list.data?.limits;
  const assets = list.data?.assets ?? [];
  const full = limits ? assets.length >= limits.max_assets : false;
  const problem = uploadProblem(file, caption, spots, limits);
  const err = (e: unknown, fallback: string) => (e instanceof ApiError && e.message ? e.message : fallback);

  return (
    <div className="space-y-4" data-testid="assets-tab">
      <p className="text-sm text-muted-foreground">
        Images you upload can be shown in a video as a still scene with callouts. The storyboard never sees the picture,
        only your caption and the names of the hotspots you place. Images are re-encoded: no metadata survives.
      </p>
      <div className="rounded-lg border border-border p-3 space-y-3" data-testid="asset-form">
        <div className="flex items-center gap-2 flex-wrap">
          <Button type="button" variant="outline" size="sm" disabled={!csrf || full} onClick={() => picker.current?.click()} data-testid="asset-pick">
            <Upload className="w-3.5 h-3.5 mr-1" />Choose PNG
          </Button>
          <input ref={picker} type="file" accept={ASSET_ACCEPT} className="hidden" data-testid="asset-input" aria-label="PNG image to upload"
            onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) { if (preview) URL.revokeObjectURL(preview); setFile(f); setSpots([]); setPreview(URL.createObjectURL(f)); upload.reset(); } }} />
          <Button type="button" variant="outline" size="sm" disabled={!csrf || full || penpot.isPending} onClick={() => penpotPicker.current?.click()} data-testid="penpot-pick">
            {penpot.isPending ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> : <Upload className="w-3.5 h-3.5 mr-1" />}Import Penpot board (SVG)
          </Button>
          <input ref={penpotPicker} type="file" accept=".svg,image/svg+xml" className="hidden" data-testid="penpot-input" aria-label="Penpot board exported as SVG"
            onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) { penpot.reset(); penpot.mutate(f); } }} />
          <Button type="button" variant="outline" size="sm" disabled={!csrf || full || !!captureBlocked} title={captureBlocked ?? undefined}
            onClick={() => setShowCaptureDialog(true)} data-testid="capture-pick">
            <Globe className="w-3.5 h-3.5 mr-1" />Add website screenshot
          </Button>
          <span className="text-xs text-muted-foreground" data-testid="asset-count">
            {limits ? `${assets.length} of ${limits.max_assets} images` : list.isLoading ? "loading…" : ""}
          </span>
          {full && <span className="text-xs text-amber-600" data-testid="asset-full">The limit is reached. Delete an image to upload another.</span>}
        </div>
        {captureBlocked && !settings.isLoading && <p className="text-xs text-muted-foreground" data-testid="capture-blocked">{captureBlocked}</p>}
        {file && (
          <>
            <p className="text-xs text-amber-700 dark:text-amber-400" data-testid="asset-third-party">
              A screenshot or photo can contain other people's data. Upload only what you may show in a video.
            </p>
            {preview && (
              <div className="relative inline-block max-w-full">
                <img ref={imgRef} src={preview} alt="Image to upload: click to place a hotspot" data-testid="asset-preview"
                  className="max-h-72 max-w-full rounded-md border border-border cursor-crosshair"
                  onClick={(e) => {
                    if (limits && spots.length >= limits.max_hotspots) return;
                    const p = clickToPoint(e.currentTarget.getBoundingClientRect(), e.clientX, e.clientY);
                    if (p) setSpots((s) => [...s, { name: nextSpotName(s), x: p.x, y: p.y, description: "" }]);
                  }} />
                {spots.map((s) => (
                  <span key={s.name} className="absolute w-3 h-3 -ml-1.5 -mt-1.5 rounded-full border-2 border-white bg-[var(--viz-role-os)] pointer-events-none"
                    style={{ left: `${s.x * 100}%`, top: `${s.y * 100}%` }} />
                ))}
              </div>
            )}
            <p className="text-xs text-muted-foreground">Click the picture to place a hotspot a callout can point at{limits ? ` (at most ${limits.max_hotspots})` : ""}.</p>
            {spots.map((s, k) => (
              <div key={k} className="flex gap-2 items-center" data-testid={`asset-spot-${k}`}>
                <Input className="w-40" value={s.name} aria-label="Hotspot name" onChange={(e) => setSpots((all) => all.map((x, j) => (j === k ? { ...x, name: e.target.value } : x)))} />
                <Input value={s.description} placeholder="What is here (optional)" aria-label="Hotspot description" onChange={(e) => setSpots((all) => all.map((x, j) => (j === k ? { ...x, description: e.target.value } : x)))} />
                <span className="text-xs text-muted-foreground tabular-nums whitespace-nowrap">{Math.round(s.x * 100)} %, {Math.round(s.y * 100)} %</span>
                <Button type="button" variant="ghost" size="sm" aria-label={`Remove ${s.name}`} onClick={() => setSpots((all) => all.filter((_, j) => j !== k))}><X className="w-3.5 h-3.5" /></Button>
              </div>
            ))}
            <label className="block text-sm"><span className="text-xs text-muted-foreground">Caption (required, what the storyboard reads about the image)</span>
              <Input value={caption} maxLength={limits?.max_caption} onChange={(e) => setCaption(e.target.value)} data-testid="asset-caption" /></label>
            <label className="block text-sm"><span className="text-xs text-muted-foreground">Source (optional; only scheme, host and path are kept)</span>
              <Input value={source} onChange={(e) => setSource(e.target.value)} data-testid="asset-source" /></label>
            <div className="flex items-center gap-2">
              <Button type="button" size="sm" disabled={!!problem || upload.isPending || !csrf} onClick={() => upload.mutate()} data-testid="asset-upload">
                {upload.isPending ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> : <Upload className="w-3.5 h-3.5 mr-1" />}Upload
              </Button>
              <Button type="button" variant="ghost" size="sm" onClick={reset} disabled={upload.isPending}>Discard</Button>
              {problem && <span className="text-xs text-muted-foreground" data-testid="asset-problem">{problem}</span>}
            </div>
          </>
        )}
        {penpot.isError && <p className="text-xs text-destructive" role="alert" data-testid="penpot-error">{err(penpot.error, "The Penpot board could not be imported.")}</p>}
        {penpot.isSuccess && (
          <p className="text-xs text-muted-foreground" data-testid="penpot-done">
            Board imported as an image{penpot.data.notes.length ? ` (not imported: ${penpot.data.notes.join(", ")})` : ""}. Components, constraints and animations are not part of an SVG export.
          </p>
        )}
        {upload.isError && <p className="text-xs text-destructive" role="alert" data-testid="asset-upload-error">{err(upload.error, "The image could not be uploaded.")}</p>}
      </div>
      {assets.length === 0 && !list.isLoading ? <p className="text-sm text-muted-foreground" data-testid="assets-empty">No images uploaded yet.</p> : (
        <ul className="grid grid-cols-2 md:grid-cols-3 gap-3" data-testid="assets-list">
          {assets.map((a) => (
            <li key={a.id} className="rounded-lg border border-border p-2 text-sm" data-testid={`asset-${a.id}`}>
              <div className="aspect-video rounded-md overflow-hidden bg-muted/40"><img src={assetImageUrl(a.id)} alt={a.caption} className="w-full h-full object-contain" loading="lazy" /></div>
              <div className="font-medium mt-1 break-words">{a.caption}</div>
              <div className="text-xs text-muted-foreground">
                <code>{a.id}</code> · {a.width}×{a.height} · {fmtAssetBytes(a.bytes)} · {a.hotspots.length} hotspot{a.hotspots.length === 1 ? "" : "s"}
                {a.hotspots.length ? `: ${a.hotspots.map((h) => h.name).join(", ")}` : ""}
              </div>
              {a.source && <div className="text-xs text-muted-foreground break-all">Source: {a.source}</div>}
              {confirm === a.id ? (
                <div className="flex gap-2 mt-2" role="alertdialog" aria-label="Confirm deletion">
                  <Button type="button" variant="destructive" size="sm" disabled={del.isPending || !csrf} onClick={() => del.mutate(a.id)} data-testid={`asset-delete-confirm-${a.id}`}>Delete</Button>
                  <Button type="button" variant="ghost" size="sm" onClick={() => setConfirm(null)}>Keep</Button>
                </div>
              ) : (
                <div className="flex gap-1 mt-1">
                  <Button type="button" variant="ghost" size="sm" className="h-7 px-2" disabled={!csrf} onClick={() => setEditing(a)} data-testid={`asset-edit-${a.id}`}>
                    Edit hotspots
                  </Button>
                  <Button type="button" variant="ghost" size="sm" className="h-7 px-2" disabled={!csrf} onClick={() => { del.reset(); setConfirm(a.id); }} data-testid={`asset-delete-${a.id}`}>
                    <Trash2 className="w-3 h-3 mr-1" />Delete
                  </Button>
                </div>
              )}
              {del.isError && confirm === a.id && <p className="text-xs text-destructive" role="alert">{err(del.error, "The image could not be deleted.")}</p>}
            </li>
          ))}
        </ul>
      )}
      {(showCaptureDialog || editing) && (
        <CaptureDialog csrf={csrf} maxCaption={limits?.max_caption} maxSpots={limits?.max_hotspots} existing={editing ?? undefined}
          onClose={() => { setShowCaptureDialog(false); setEditing(null); }}
          onChanged={() => void qc.invalidateQueries({ queryKey: ["video", "assets"] })} />
      )}
    </div>
  );
}


// ── "Add website screenshot": capture, then caption and hotspots on the stored picture ─────────────

/**
 * Step 1 asks for the address, the viewport and a caption and captures the page once (POST /video/capture).
 * Step 2 shows the STORED picture: click it to place hotspots, edit the caption, then save (PATCH) - or
 * delete the screenshot. The caption is always the user's: nothing is ever read off the page.
 */
export function CaptureDialog({ csrf, initialUrl = "", existing, maxCaption, maxSpots = 8, onClose, onChanged, onCaptured, onDeleted }: {
  csrf: string; initialUrl?: string; existing?: Asset; maxCaption?: number; maxSpots?: number;
  onClose: () => void; onChanged: () => void; onCaptured?: (asset: Asset) => void; onDeleted?: (id: string) => void;
}) {
  const titleId = useId();
  const [url, setUrl] = useState(initialUrl);
  const [viewport, setViewport] = useState<CaptureViewport>("desktop");
  const [caption, setCaption] = useState(existing?.caption ?? "");
  const [asset, setAsset] = useState<Asset | null>(existing ?? null);
  const [spots, setSpots] = useState<AssetHotspot[]>(existing?.hotspots ?? []);
  const [elapsed, setElapsed] = useState(0);
  const [warnUnsaved, setWarnUnsaved] = useState(false);
  const first = useRef<HTMLInputElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const dirty = !!asset && (caption !== asset.caption || JSON.stringify(spots) !== JSON.stringify(asset.hotspots));
  const err = (e: unknown, fallback: string) => (e instanceof ApiError && e.message ? e.message : fallback);

  const capture = useMutation({
    mutationFn: () => captureAsset({ url: url.trim(), caption, viewport }, csrf),
    onSuccess: (a) => { setAsset(a); setSpots(a.hotspots); setCaption(a.caption); onChanged(); onCaptured?.(a); },
  });
  const save = useMutation({
    mutationFn: () => updateAsset(asset!.id, { caption, hotspots: spots }, csrf),
    onSuccess: () => { onChanged(); onClose(); },
  });
  const discard = useMutation({
    mutationFn: () => api(`${BASE}/assets/${encodeURIComponent(asset!.id)}`, { method: "DELETE", csrf }),
    onSuccess: () => { onDeleted?.(asset!.id); onChanged(); onClose(); },
  });
  const busy = capture.isPending || save.isPending || discard.isPending;

  useEffect(() => {
    const back = document.activeElement as HTMLElement | null;  // focus returns where it came from
    (first.current ?? dialogRef.current)?.focus();
    return () => { back?.focus?.(); };
  }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || busy) return;
      if (dirty) { setWarnUnsaved(true); return; }  // never drop placed hotspots silently
      onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, dirty, onClose]);
  const addSpot = (x: number, y: number) => {
    if (spots.length >= maxSpots) return;
    setSpots((s) => [...s, { name: nextSpotName(s), x, y, description: "" }]);
    setWarnUnsaved(false);
  };
  const pct = (v: string) => Math.min(1, Math.max(0, Math.round((parseFloat(v) || 0) * 10) / 1000));
  useEffect(() => {
    if (!capture.isPending) { setElapsed(0); return; }
    const t0 = Date.now();
    const id = window.setInterval(() => setElapsed(Math.round((Date.now() - t0) / 1000)), 1000);
    return () => window.clearInterval(id);
  }, [capture.isPending]);

  const names = spots.map((s) => s.name);
  const spotProblem = names.some((n) => !/^[a-z0-9_]{2,32}$/.test(n)) ? "Hotspot names: 2-32 characters of a-z, 0-9 and _."
    : new Set(names).size !== names.length ? "Two hotspots have the same name." : null;

  return (
    <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center p-4" data-testid="capture-overlay">
      <div role="dialog" aria-modal="true" aria-labelledby={titleId} data-testid="capture-dialog" ref={dialogRef} tabIndex={-1}
        className="bg-background rounded-lg border border-border p-4 w-full max-w-2xl max-h-[90vh] overflow-y-auto space-y-3">
        <h3 id={titleId} className="font-semibold">{existing ? "Edit hotspots" : asset ? "Place hotspots on the screenshot" : "Add website screenshot"}</h3>
        {!asset ? (
          <>
            <p className="text-xs text-muted-foreground">
              The console opens the page once and stores what it shows as an image. A caption you write is all the storyboard
              reads about it; nothing is read off the page. A page may contain other people&apos;s data: capture only what you may show.
            </p>
            <label className="block text-sm"><span className="text-xs text-muted-foreground">Address</span>
              <Input ref={first} value={url} placeholder="https://example.com/pricing" onChange={(e) => setUrl(e.target.value)} disabled={busy} data-testid="capture-url" /></label>
            <label className="block text-sm"><span className="text-xs text-muted-foreground">Viewport</span>
              <select value={viewport} onChange={(e) => setViewport(e.target.value as CaptureViewport)} disabled={busy} data-testid="capture-viewport"
                className="w-full mt-1 px-2 py-1.5 border border-border rounded-md bg-background text-sm">
                {(Object.keys(CAPTURE_VIEWPORTS) as CaptureViewport[]).map((v) => <option key={v} value={v}>{CAPTURE_VIEWPORTS[v]}</option>)}
              </select></label>
            <label className="block text-sm"><span className="text-xs text-muted-foreground">Caption (required: what the storyboard reads about the screenshot)</span>
              <Input value={caption} maxLength={maxCaption} onChange={(e) => setCaption(e.target.value)} disabled={busy} data-testid="capture-caption"
                placeholder="The pricing page with the three plans" /></label>
            <div className="flex items-center gap-2 flex-wrap">
              <Button type="button" size="sm" disabled={!url.trim() || !caption.trim() || busy || !csrf} onClick={() => capture.mutate()} data-testid="capture-submit">
                {capture.isPending ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> : <Globe className="w-3.5 h-3.5 mr-1" />}Capture
              </Button>
              <Button type="button" variant="ghost" size="sm" onClick={onClose} disabled={busy}>Cancel</Button>
              {capture.isPending && (
                <span className="text-xs text-muted-foreground" role="status" data-testid="capture-wait">
                  Capturing… {elapsed} s. This takes up to about {CAPTURE_WAIT_S} s; an address that cannot be captured answers after the same time.
                </span>
              )}
            </div>
            {capture.isError && <p className="text-xs text-destructive" role="alert" data-testid="capture-error">{err(capture.error, "The website could not be captured.")}</p>}
          </>
        ) : (
          <>
            <p className="text-xs text-muted-foreground">
              Stored as <code>{asset.id}</code> ({asset.width}×{asset.height}){asset.source ? ` from ${asset.source}` : ""}. Click the picture to place a
              hotspot a callout can point at (at most {maxSpots}), or add one and type its position.
            </p>
            <div className="relative inline-block max-w-full">
              <img src={assetImageUrl(asset.id)} alt={`Screenshot: ${asset.caption}`} data-testid="capture-preview"
                className="max-h-[50vh] max-w-full rounded-md border border-border cursor-crosshair"
                onClick={(e) => {
                  const pt = clickToPoint(e.currentTarget.getBoundingClientRect(), e.clientX, e.clientY);
                  if (pt) addSpot(pt.x, pt.y);
                }} />
              {spots.map((s) => (
                <span key={s.name} className="absolute w-3 h-3 -ml-1.5 -mt-1.5 rounded-full border-2 border-white bg-[var(--viz-role-os)] pointer-events-none"
                  style={{ left: `${s.x * 100}%`, top: `${s.y * 100}%` }} />
              ))}
            </div>
            {spots.map((s, k) => (
              <div key={k} className="flex gap-2 items-center" data-testid={`capture-spot-${k}`}>
                <Input className="w-40" value={s.name} aria-label="Hotspot name" onChange={(e) => setSpots((all) => all.map((x, j) => (j === k ? { ...x, name: e.target.value } : x)))} />
                <Input value={s.description} placeholder="What is here (optional)" aria-label="Hotspot description" onChange={(e) => setSpots((all) => all.map((x, j) => (j === k ? { ...x, description: e.target.value } : x)))} />
                <Input type="number" min={0} max={100} step={1} className="w-20" aria-label={`${s.name}: percent from the left`} value={Math.round(s.x * 100)}
                  onChange={(e) => setSpots((all) => all.map((x, j) => (j === k ? { ...x, x: pct(e.target.value) } : x)))} data-testid={`capture-spot-x-${k}`} />
                <Input type="number" min={0} max={100} step={1} className="w-20" aria-label={`${s.name}: percent from the top`} value={Math.round(s.y * 100)}
                  onChange={(e) => setSpots((all) => all.map((x, j) => (j === k ? { ...x, y: pct(e.target.value) } : x)))} data-testid={`capture-spot-y-${k}`} />
                <Button type="button" variant="ghost" size="sm" aria-label={`Remove ${s.name}`} onClick={() => setSpots((all) => all.filter((_, j) => j !== k))}><X className="w-3.5 h-3.5" /></Button>
              </div>
            ))}
            <Button type="button" variant="outline" size="sm" disabled={spots.length >= maxSpots} onClick={() => addSpot(0.5, 0.5)} data-testid="capture-add-spot">
              Add hotspot
            </Button>
            <label className="block text-sm"><span className="text-xs text-muted-foreground">Caption</span>
              <Input value={caption} maxLength={maxCaption} onChange={(e) => setCaption(e.target.value)} data-testid="capture-caption-edit" /></label>
            <div className="flex items-center gap-2 flex-wrap">
              <Button type="button" size="sm" disabled={busy || !caption.trim() || !!spotProblem || !csrf} onClick={() => save.mutate()} data-testid="capture-save">
                {save.isPending ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> : null}Save
              </Button>
              <Button type="button" variant="ghost" size="sm" disabled={busy} onClick={onClose} data-testid="capture-close">
                {dirty ? "Close without saving" : "Close"}
              </Button>
              {!existing && (
                <Button type="button" variant="ghost" size="sm" disabled={busy || !csrf} onClick={() => discard.mutate()} data-testid="capture-discard">
                  <Trash2 className="w-3.5 h-3.5 mr-1" />Delete screenshot
                </Button>
              )}
              {spotProblem && <span className="text-xs text-muted-foreground">{spotProblem}</span>}
            </div>
            {warnUnsaved && (
              <p className="text-xs text-amber-600" role="status" data-testid="capture-unsaved">
                You have unsaved changes: save them, or choose Close without saving.
              </p>
            )}
            {(save.isError || discard.isError) && (
              <p className="text-xs text-destructive" role="alert" data-testid="capture-save-error">
                {save.isError ? err(save.error, "The hotspots could not be saved.") : err(discard.error, "The screenshot could not be deleted.")}
              </p>
            )}
          </>
        )}
      </div>
    </div>
  );
}
