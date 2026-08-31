import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import {
  BarChart3,
  ChevronLeft,
  ChevronRight,
  Download,
  FileText,
  Image,
  Maximize2,
  Pin,
  X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

// ── Shared type ────────────────────────────────────────────────────────────

export interface MediaItem {
  media_id: string;
  node_id?: string;         // workflow context
  stage_id?: string;        // compute context
  pipeline_id?: string;     // compute context
  filename: string;
  mime_type: string;
  label: string | null;
  size_bytes?: number;
  src: string;              // serving URL
  thumbnail_src: string | null;
  width?: number;
  height?: number;
  ts: number;
}

// ── Helpers ────────────────────────────────────────────────────────────────

function formatBytes(n: number | undefined): string {
  if (n == null) return "";
  const units = ["B", "KiB", "MiB", "GiB"];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(v >= 100 || i === 0 ? 0 : 1)} ${units[i]}`;
}

function MediaIcon({ mimeType, className }: { mimeType: string; className?: string }) {
  if (mimeType.startsWith("image/")) {
    return <BarChart3 className={cn("h-4 w-4 text-muted-foreground", className)} />;
  }
  if (mimeType === "application/pdf") {
    return <FileText className={cn("h-4 w-4 text-muted-foreground", className)} />;
  }
  return <Image className={cn("h-4 w-4 text-muted-foreground", className)} />;
}

function contextBadge(item: MediaItem): string | null {
  if (item.node_id) return item.node_id;
  if (item.stage_id) return item.stage_id;
  return null;
}

// ── ImageCard ──────────────────────────────────────────────────────────────

export function ImageCard({
  item,
  onLightbox,
  onPin,
}: {
  item: MediaItem;
  onLightbox?: (item: MediaItem) => void;
  onPin?: (item: MediaItem) => void;
}) {
  const badge = contextBadge(item);
  const thumb = item.thumbnail_src ?? item.src;
  const displayLabel = item.label ?? item.filename;

  return (
    <div className="rounded-lg border bg-card text-card-foreground shadow-sm flex flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2 border-b">
        <MediaIcon mimeType={item.mime_type} />
        <span className="flex-1 text-sm font-medium truncate" title={displayLabel}>
          {displayLabel}
        </span>
        {badge && (
          <Badge variant="secondary" className="text-xs shrink-0">
            {badge}
          </Badge>
        )}
      </div>

      {/* Thumbnail */}
      <div
        className="relative group cursor-pointer bg-muted/30 flex items-center justify-center"
        style={{ minHeight: "8rem" }}
        onClick={() => onLightbox?.(item)}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") onLightbox?.(item); }}
        aria-label={`Open ${displayLabel} in lightbox`}
      >
        <img
          src={thumb}
          alt={displayLabel}
          loading="lazy"
          className="max-h-48 w-full object-contain"
        />
        <div className="absolute inset-0 bg-black/40 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center">
          <Maximize2 className="h-6 w-6 text-white" />
        </div>
      </div>

      {/* Footer */}
      <div className="flex items-center gap-2 px-3 py-2 border-t">
        <a
          href={item.src}
          download={item.filename}
          className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
          onClick={(e) => e.stopPropagation()}
        >
          <Download className="h-3.5 w-3.5" />
          Download
        </a>
        {onPin && (
          <Button
            variant="ghost"
            size="sm"
            className="ml-auto h-7 px-2 text-xs"
            onClick={() => onPin(item)}
          >
            <Pin className="h-3.5 w-3.5 mr-1" />
            Anheften
          </Button>
        )}
      </div>
    </div>
  );
}

// ── MediaGallery ───────────────────────────────────────────────────────────

export function MediaGallery({
  items,
  onPin,
  zipUrl: zipUrlProp,
}: {
  items: MediaItem[];
  onPin?: (item: MediaItem) => void;
  zipUrl?: string;
}) {
  const [lightboxIndex, setLightboxIndex] = useState<number | null>(null);

  const handleLightbox = useCallback((item: MediaItem) => {
    const idx = items.findIndex((i) => i.media_id === item.media_id);
    setLightboxIndex(idx >= 0 ? idx : 0);
  }, [items]);

  if (items.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
        <Image className="h-10 w-10 mb-3 opacity-30" />
        <p className="text-sm">No media available</p>
      </div>
    );
  }

  // Prefer explicit zipUrl prop; fall back to deriving from first item's src
  const zipUrl = zipUrlProp ?? (items.length > 0
    ? items[0].src.replace(/\/[^/]+$/, "/media.zip")
    : null);

  return (
    <>
      <div className="flex items-center justify-end mb-3">
        {zipUrl && (
          <a
            href={zipUrl}
            download
            className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground border rounded-md px-2.5 py-1.5 transition-colors"
          >
            <Download className="h-3.5 w-3.5" />
            Download all (.zip)
          </a>
        )}
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
        {items.map((item) => (
          <ImageCard
            key={item.media_id}
            item={item}
            onLightbox={handleLightbox}
            onPin={onPin}
          />
        ))}
      </div>

      {lightboxIndex !== null && (
        <LightboxViewer
          items={items}
          initialIndex={lightboxIndex}
          onClose={() => setLightboxIndex(null)}
          onPin={onPin}
        />
      )}
    </>
  );
}

// ── LightboxViewer ─────────────────────────────────────────────────────────

export function LightboxViewer({
  items,
  initialIndex,
  onClose,
  onPin,
}: {
  items: MediaItem[];
  initialIndex: number;
  onClose: () => void;
  onPin?: (item: MediaItem) => void;
}) {
  const [index, setIndex] = useState(initialIndex);
  const item = items[index];

  const prev = useCallback(() => setIndex((i) => (i > 0 ? i - 1 : items.length - 1)), [items.length]);
  const next = useCallback(() => setIndex((i) => (i < items.length - 1 ? i + 1 : 0)), [items.length]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
      else if (e.key === "ArrowLeft") prev();
      else if (e.key === "ArrowRight") next();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose, prev, next]);

  if (!item) return null;

  const displayLabel = item.label ?? item.filename;
  const sizeStr = formatBytes(item.size_bytes);

  return createPortal(
    <div
      className="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex flex-col"
      onClick={onClose}
    >
      {/* Top bar */}
      <div
        className="flex items-center gap-3 px-4 py-3 bg-black/60 text-white"
        onClick={(e) => e.stopPropagation()}
      >
        <span className="flex-1 text-sm font-medium truncate">{displayLabel}</span>
        <span className="text-xs text-white/60 shrink-0">
          {index + 1} / {items.length}
        </span>
        <button
          onClick={onClose}
          className="p-1.5 rounded hover:bg-white/10 transition-colors"
          aria-label="Close lightbox"
        >
          <X className="h-5 w-5" />
        </button>
      </div>

      {/* Center image + nav arrows */}
      <div
        className="flex-1 flex items-center justify-center relative"
        onClick={(e) => e.stopPropagation()}
      >
        {items.length > 1 && (
          <button
            onClick={prev}
            className="absolute left-3 p-2 rounded-full bg-black/40 hover:bg-black/60 text-white transition-colors z-10"
            aria-label="Previous image"
          >
            <ChevronLeft className="h-6 w-6" />
          </button>
        )}

        <img
          key={item.media_id}
          src={item.src}
          alt={displayLabel}
          className="max-h-[85vh] max-w-[90vw] object-contain"
        />

        {items.length > 1 && (
          <button
            onClick={next}
            className="absolute right-3 p-2 rounded-full bg-black/40 hover:bg-black/60 text-white transition-colors z-10"
            aria-label="Next image"
          >
            <ChevronRight className="h-6 w-6" />
          </button>
        )}
      </div>

      {/* Bottom bar */}
      <div
        className="flex items-center gap-3 px-4 py-3 bg-black/60 text-white"
        onClick={(e) => e.stopPropagation()}
      >
        <Badge variant="secondary" className="text-xs font-mono shrink-0">
          {item.mime_type}
        </Badge>
        {sizeStr && (
          <span className="text-xs text-white/60">{sizeStr}</span>
        )}
        <div className="flex-1" />
        <a
          href={item.src}
          download={item.filename}
          className="flex items-center gap-1.5 text-xs text-white/80 hover:text-white px-3 py-1.5 rounded border border-white/20 hover:border-white/40 transition-colors"
        >
          <Download className="h-3.5 w-3.5" />
          Download
        </a>
        {onPin && (
          <Button
            variant="ghost"
            size="sm"
            className="h-8 px-3 text-xs text-white/80 hover:text-white hover:bg-white/10"
            onClick={() => onPin(item)}
          >
            <Pin className="h-3.5 w-3.5 mr-1.5" />
            Anheften
          </Button>
        )}
      </div>
    </div>,
    document.body,
  );
}
