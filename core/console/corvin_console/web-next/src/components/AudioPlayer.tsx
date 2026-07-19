import React, { useRef, useState, useEffect } from "react";
import { Play, Pause, Volume2, VolumeX } from "lucide-react";
import { cn } from "@/lib/utils";

interface AudioPlayerProps {
  src: string;
  className?: string;
}

export function AudioPlayer({ src, className }: AudioPlayerProps) {
  const audioRef = useRef<HTMLAudioElement>(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [volume, setVolume] = useState(1);
  const [isMuted, setIsMuted] = useState(false);
  const [hasError, setHasError] = useState(false);

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;

    const handlePlay = () => setIsPlaying(true);
    const handlePause = () => setIsPlaying(false);
    const handleTimeUpdate = () => setCurrentTime(audio.currentTime);
    // "durationchange" as well as "loadedmetadata": MediaRecorder webm
    // artifacts report duration === Infinity at loadedmetadata and only emit
    // the real value via a later durationchange.
    const handleDurationChange = () => setDuration(audio.duration);
    // A src that 404s / fails to decode used to fail SILENTLY — the player
    // rendered a dead 0:00/0:00. Surface it as an explicit error state.
    const handleError = () => setHasError(true);

    audio.addEventListener("play", handlePlay);
    audio.addEventListener("pause", handlePause);
    audio.addEventListener("timeupdate", handleTimeUpdate);
    audio.addEventListener("loadedmetadata", handleDurationChange);
    audio.addEventListener("durationchange", handleDurationChange);
    audio.addEventListener("error", handleError);

    return () => {
      audio.removeEventListener("play", handlePlay);
      audio.removeEventListener("pause", handlePause);
      audio.removeEventListener("timeupdate", handleTimeUpdate);
      audio.removeEventListener("loadedmetadata", handleDurationChange);
      audio.removeEventListener("durationchange", handleDurationChange);
      audio.removeEventListener("error", handleError);
      // Ghost-playback fix: removing listeners does NOT stop the media
      // element — audio kept playing after unmount (chat navigation) with
      // no UI left to control it. Stop playback and release the resource.
      audio.pause();
      audio.removeAttribute("src");
      audio.load();
    };
  }, []);

  // Per-source reset. chat.tsx keys ArtifactCard by list index, so React
  // REUSES this component instance when the artifact at an index changes —
  // a stale hasError (or a finished playhead) would otherwise persist onto a
  // new, healthy audio source, showing "Audio unavailable" forever. Reset the
  // per-source state whenever src changes.
  useEffect(() => {
    setHasError(false);
    setIsPlaying(false);
    setCurrentTime(0);
    setDuration(0);
  }, [src]);

  const togglePlay = () => {
    const audio = audioRef.current;
    if (!audio) return;
    if (isPlaying) {
      audio.pause();
    } else {
      // play() rejects on genuinely broken/undecodable sources (→ error state)
      // but ALSO on benign interruptions: AbortError when a pending play() is
      // cut short by pause()/unmount, and NotAllowedError from the autoplay
      // policy. Treating those as fatal permanently replaced a healthy player
      // with "Audio unavailable". Only real media failures set the error state.
      audio.play().catch((err: unknown) => {
        const name = (err as { name?: string })?.name;
        if (name === "AbortError" || name === "NotAllowedError") return;
        setHasError(true);
      });
    }
  };

  const handleSliderChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const newTime = parseFloat(e.target.value);
    setCurrentTime(newTime);
    if (audioRef.current) {
      audioRef.current.currentTime = newTime;
    }
  };

  const handleVolumeChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const newVolume = parseFloat(e.target.value);
    setVolume(newVolume);
    const audio = audioRef.current;
    if (audio) {
      audio.volume = newVolume;
      // Dragging the volume slider up is an explicit unmute gesture — keep
      // the muted flag and the icon in sync with what the user will hear.
      if (newVolume > 0 && audio.muted) {
        audio.muted = false;
        setIsMuted(false);
      }
    }
  };

  const toggleMute = () => {
    const audio = audioRef.current;
    if (!audio) return;
    // Use the element's real `muted` property instead of emulating mute via
    // volume=0 — the emulation desynced: a slider drag restored the volume
    // (audio audible) while the icon still claimed muted.
    audio.muted = !audio.muted;
    setIsMuted(audio.muted);
  };

  const formatTime = (time: number) => {
    if (!Number.isFinite(time) || time <= 0) return "0:00";
    const minutes = Math.floor(time / 60);
    const seconds = Math.floor(time % 60);
    return `${minutes}:${seconds.toString().padStart(2, "0")}`;
  };

  // MediaRecorder webm blobs report duration === Infinity until (if ever) a
  // durationchange delivers the real length — rendering "Infinity:NaN".
  // Until it is finite, show elapsed time only and disable seeking range.
  const hasFiniteDuration = Number.isFinite(duration) && duration > 0;
  const progressPct = hasFiniteDuration ? (currentTime / duration) * 100 : 0;

  if (hasError) {
    return (
      <div
        className={cn(
          "bg-card dark:bg-card px-3 py-3 rounded-lg text-xs italic text-foreground/60",
          className,
        )}
      >
        Audio unavailable
      </div>
    );
  }

  return (
    <div className={cn("bg-card dark:bg-card px-3 py-3 rounded-lg", className)}>
      <audio ref={audioRef} src={src} preload="metadata" />

      <div className="flex items-center gap-3">
        {/* Play button */}
        <button
          onClick={togglePlay}
          className="flex-shrink-0 p-1.5 rounded hover:bg-muted dark:hover:bg-muted/80 text-foreground transition-colors"
          title={isPlaying ? "Pause" : "Play"}
        >
          {isPlaying ? (
            <Pause className="h-4 w-4" />
          ) : (
            <Play className="h-4 w-4" />
          )}
        </button>

        {/* Time display */}
        <div className="text-xs font-mono text-foreground/70 w-12 text-right">
          {formatTime(currentTime)}
        </div>

        {/* Progress slider */}
        <input
          type="range"
          min="0"
          max={hasFiniteDuration ? duration : 0}
          value={currentTime}
          onChange={handleSliderChange}
          className="audio-slider flex-1 h-1 rounded-full cursor-pointer"
          style={{
            background: `linear-gradient(to right, hsl(var(--accent)) 0%, hsl(var(--accent)) ${progressPct}%, hsl(var(--muted)) ${progressPct}%, hsl(var(--muted)) 100%)`,
          }}
        />

        {/* Duration display — elapsed-only until the real duration is known */}
        {hasFiniteDuration && (
          <div className="text-xs font-mono text-foreground/70 w-12">
            {formatTime(duration)}
          </div>
        )}

        {/* Volume control */}
        <button
          onClick={toggleMute}
          className="flex-shrink-0 p-1.5 rounded hover:bg-muted dark:hover:bg-muted/80 text-foreground transition-colors"
          title={isMuted ? "Unmute" : "Mute"}
        >
          {isMuted ? (
            <VolumeX className="h-4 w-4" />
          ) : (
            <Volume2 className="h-4 w-4" />
          )}
        </button>

        {/* Volume slider */}
        <input
          type="range"
          min="0"
          max="1"
          step="0.1"
          value={isMuted ? 0 : volume}
          onChange={handleVolumeChange}
          className="audio-slider w-16 h-1 rounded-full cursor-pointer"
          style={{
            background: `linear-gradient(to right, hsl(var(--accent)) 0%, hsl(var(--accent)) ${
              (isMuted ? 0 : volume) * 100
            }%, hsl(var(--muted)) ${(isMuted ? 0 : volume) * 100}%, hsl(var(--muted)) 100%)`,
          }}
        />
      </div>
    </div>
  );
}
