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
import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Globe, Loader2, Trash2, Upload, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api, ApiError } from "@/lib/api/client";
import {
  ASSET_ACCEPT, assetImageUrl, canvasAuthoring, captureAsset, clickToPoint, fmtAssetBytes, nextSpotName, sceneSlideUrl,
  scenesWithTemplate, importPenpotBoard, uploadAsset, uploadProblem, type AssetHotspot, type AssetList,
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
  const [captureUrl, setCaptureUrl] = useState("");
  const [captureCaption, setCaptureCaption] = useState("");
  const [captureViewport, setCaptureViewport] = useState("desktop");
  const [captureSpots, setCaptureSpots] = useState<AssetHotspot[]>([]);
  const [capturePreview, setCapturePreview] = useState<string | null>(null);
  const [showCaptureDialog, setShowCaptureDialog] = useState(false);
  const picker = useRef<HTMLInputElement>(null);
  const imgRef = useRef<HTMLImageElement>(null);
  const captureImgRef = useRef<HTMLImageElement>(null);

  const reset = () => { if (preview) URL.revokeObjectURL(preview); setFile(null); setPreview(null); setCaption(""); setSource(""); setSpots([]); };
  const resetCapture = () => { if (capturePreview) URL.revokeObjectURL(capturePreview); setCaptureUrl(""); setCaptureCaption(""); setCaptureSpots([]); setCapturePreview(null); setShowCaptureDialog(false); };
  const upload = useMutation({
    mutationFn: () => uploadAsset(file!, { caption, source, hotspots: spots }, csrf),
    onSuccess: () => { reset(); void qc.invalidateQueries({ queryKey: ["video", "assets"] }); },
  });
  const capture = useMutation({
    mutationFn: () => captureAsset({ url: captureUrl, caption: captureCaption, hotspots: captureSpots, viewport: captureViewport }, csrf),
    onSuccess: () => { resetCapture(); void qc.invalidateQueries({ queryKey: ["video", "assets"] }); },
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
          <Button type="button" variant="outline" size="sm" disabled={!csrf || full || capture.isPending} onClick={() => setShowCaptureDialog(true)} data-testid="capture-pick">
            {capture.isPending ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> : <Globe className="w-3.5 h-3.5 mr-1" />}Capture from website
          </Button>
          <span className="text-xs text-muted-foreground" data-testid="asset-count">
            {limits ? `${assets.length} of ${limits.max_assets} images` : list.isLoading ? "loading…" : ""}
          </span>
          {full && <span className="text-xs text-amber-600" data-testid="asset-full">The limit is reached. Delete an image to upload another.</span>}
        </div>
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
                <Button type="button" variant="ghost" size="sm" className="mt-1 h-7 px-2" disabled={!csrf} onClick={() => { del.reset(); setConfirm(a.id); }} data-testid={`asset-delete-${a.id}`}>
                  <Trash2 className="w-3 h-3 mr-1" />Delete
                </Button>
              )}
              {del.isError && confirm === a.id && <p className="text-xs text-destructive" role="alert">{err(del.error, "The image could not be deleted.")}</p>}
            </li>
          ))}
        </ul>
      )}
      {showCaptureDialog && (
        <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center p-4" data-testid="capture-dialog-overlay" onClick={() => !capture.isPending && resetCapture()}>
          <div className="bg-background rounded-lg border border-border p-4 max-w-sm w-full space-y-3" onClick={(e) => e.stopPropagation()} data-testid="capture-dialog">
            <h3 className="font-semibold">Capture website screenshot</h3>
            <label className="block text-sm">
              <span className="text-xs text-muted-foreground">Website URL (must match the egress policy)</span>
              <Input
                value={captureUrl}
                placeholder="https://example.com"
                onChange={(e) => setCaptureUrl(e.target.value)}
                disabled={capture.isPending}
                data-testid="capture-url"
              />
            </label>
            <label className="block text-sm">
              <span className="text-xs text-muted-foreground">Viewport size</span>
              <select
                value={captureViewport}
                onChange={(e) => setCaptureViewport(e.target.value)}
                disabled={capture.isPending}
                className="w-full px-2 py-1.5 border border-border rounded-md bg-background text-sm"
                data-testid="capture-viewport"
              >
                <option value="desktop">Desktop (1920×1080)</option>
                <option value="mobile">Mobile (375×667)</option>
              </select>
            </label>
            <label className="block text-sm">
              <span className="text-xs text-muted-foreground">Caption (required)</span>
              <Input
                value={captureCaption}
                maxLength={limits?.max_caption}
                onChange={(e) => setCaptureCaption(e.target.value)}
                placeholder="What the storyboard reads about this screenshot"
                disabled={capture.isPending}
                data-testid="capture-caption"
              />
            </label>
            <div className="flex items-center gap-2">
              <Button
                type="button"
                size="sm"
                disabled={!captureUrl.trim() || !captureCaption.trim() || capture.isPending || !csrf}
                onClick={() => capture.mutate()}
                data-testid="capture-submit"
              >
                {capture.isPending ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> : <Globe className="w-3.5 h-3.5 mr-1" />}
                Capture
              </Button>
              <Button type="button" variant="ghost" size="sm" onClick={resetCapture} disabled={capture.isPending}>Cancel</Button>
            </div>
            {capture.isError && <p className="text-xs text-destructive" role="alert" data-testid="capture-error">{err(capture.error, "The website could not be captured.")}</p>}
          </div>
        </div>
      )}
    </div>
  );
}
