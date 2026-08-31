/**
 * ComputeNarrativeDialog — Experiment summary card with voice playback.
 *
 * Lazy-generates an LLM narrative for a finished compute run via
 * GET /compute/runs/{run_id}/narrative and streams TTS audio from
 * GET /compute/runs/{run_id}/voice.
 *
 * Integrates into RunJobCard in compute.tsx.
 */
import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Volume2, VolumeX, RotateCcw, ChevronDown, ChevronUp, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { getComputeNarrative, computeRunVoiceUrl, type ComputeNarrative } from "@/lib/api";
import { cn } from "@/lib/utils";

interface Props {
  runId: string;
  /** Only show for complete / non-running runs */
  enabled: boolean;
  locale?: string;
}

export function ComputeNarrativeDialog({ runId, enabled, locale = "de" }: Props) {
  const qc = useQueryClient();
  const [open, setOpen] = React.useState(false);
  const [playing, setPlaying] = React.useState(false);
  const [audioError, setAudioError] = React.useState(false);
  const [regenerating, setRegenerating] = React.useState(false);
  const audioRef = React.useRef<HTMLAudioElement>(null);

  const narrativeQ = useQuery<ComputeNarrative, Error>({
    queryKey: ["compute-narrative", runId, locale],
    queryFn: ({ signal }) => getComputeNarrative(runId, { locale, signal }),
    enabled: open && enabled,
    staleTime: Infinity,  // narratives don't change unless explicitly refreshed
    retry: false,
  });

  const togglePlay = () => {
    const el = audioRef.current;
    if (!el) return;
    if (playing) {
      el.pause();
      setPlaying(false);
    } else {
      setAudioError(false);
      el.src = computeRunVoiceUrl(runId);
      el.load();
      el.play().catch(() => setAudioError(true));
    }
  };

  const handleRegenerate = async () => {
    if (regenerating) return;
    // Stop playback before regenerating
    const el = audioRef.current;
    if (el && playing) { el.pause(); setPlaying(false); }
    setAudioError(false);
    setRegenerating(true);
    try {
      // Force-regenerate narrative text via the backend (also deletes stale audio cache).
      const data = await getComputeNarrative(runId, { locale, force: true });
      qc.setQueryData(["compute-narrative", runId, locale], data);
    } catch {
      // Drop the stale cache so the next open triggers a fresh fetch.
      qc.removeQueries({ queryKey: ["compute-narrative", runId, locale] });
    } finally {
      setRegenerating(false);
    }
  };

  if (!enabled) return null;

  return (
    <div className="mt-4 rounded-md border border-border bg-card overflow-hidden">
      {/* Header — always visible */}
      <button
        className="w-full px-4 py-2.5 flex items-center justify-between gap-3 hover:bg-muted/20 transition-colors text-left"
        onClick={() => setOpen((v) => !v)}
      >
        <div className="flex items-center gap-2">
          <span className="text-base leading-none">🔬</span>
          <span className="text-sm font-medium">Experiment Summary</span>
          {(narrativeQ.isFetching || regenerating) && (
            <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" />
          )}
        </div>
        {open
          ? <ChevronUp className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
          : <ChevronDown className="h-3.5 w-3.5 text-muted-foreground shrink-0" />}
      </button>

      {/* Body — shown when open */}
      {open && (
        <div className="border-t border-border px-4 py-3 space-y-3">
          {/* Narrative text */}
          {narrativeQ.isLoading && (
            <div className="flex items-center gap-2 text-xs text-muted-foreground py-1">
              <Loader2 className="h-3 w-3 animate-spin" />
              Generating summary…
            </div>
          )}
          {narrativeQ.isError && (
            <p className="text-xs text-destructive">
              Could not generate summary — is the{" "}
              <code className="font-mono">claude</code> CLI available?
            </p>
          )}
          {narrativeQ.data && (
            <p className="text-sm text-foreground leading-relaxed whitespace-pre-wrap">
              {narrativeQ.data.text}
            </p>
          )}

          {/* Controls */}
          <div className="flex items-center gap-2 pt-1">
            {/* Play / Stop */}
            <Button
              variant="outline"
              size="sm"
              className="h-7 px-3 text-xs gap-1.5"
              onClick={togglePlay}
              disabled={!narrativeQ.data || narrativeQ.isLoading}
            >
              {playing
                ? <><VolumeX className="h-3.5 w-3.5" />Stop</>
                : <><Volume2 className="h-3.5 w-3.5" />Listen</>}
            </Button>

            {/* Regenerate */}
            <Button
              variant="ghost"
              size="sm"
              className="h-7 px-2 text-xs gap-1.5 text-muted-foreground"
              onClick={handleRegenerate}
              disabled={narrativeQ.isFetching || regenerating}
              title="Re-generate summary"
            >
              <RotateCcw className={cn("h-3 w-3", (narrativeQ.isFetching || regenerating) && "animate-spin")} />
              Neu
            </Button>

            {/* Model + locale badge */}
            {narrativeQ.data && (
              <span className="ml-auto text-[10px] text-muted-foreground font-mono">
                {narrativeQ.data.model.replace("claude-", "").replace("-20251001", "")}
                {" · "}
                {narrativeQ.data.locale}
              </span>
            )}
          </div>

          {audioError && (
            <p className="text-xs text-amber-600 dark:text-amber-400">
              TTS playback failed — check voice provider config.
            </p>
          )}

          {/* Hidden audio element */}
          <audio
            ref={audioRef}
            onEnded={() => setPlaying(false)}
            onPause={() => setPlaying(false)}
            onPlay={() => setPlaying(true)}
            onError={() => { setPlaying(false); setAudioError(true); }}
            className="hidden"
          />
        </div>
      )}
    </div>
  );
}
