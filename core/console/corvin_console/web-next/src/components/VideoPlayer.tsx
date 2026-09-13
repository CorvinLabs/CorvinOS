/**
 * Video Player Component — HTML5 <video> with SRT captions
 * Plays MP4 videos from Video Producer plugin
 * Supports: play/pause, seek, volume, download, metadata
 */

import React, { useRef, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Download, Play, Pause, Volume2, VolumeX } from "lucide-react";

interface VideoPlayerProps {
  videoPath: string;           // URL to MP4 file
  srtPath?: string;            // URL to SRT captions
  title?: string;
  metadata?: {
    duration_seconds?: number;
    resolution?: string;
    size_mb?: number;
  };
  onDownload?: () => void;
}

export function VideoPlayer({
  videoPath,
  srtPath,
  title,
  metadata,
  onDownload,
}: VideoPlayerProps) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [volume, setVolume] = useState(1);
  const [isMuted, setIsMuted] = useState(false);

  const handlePlayPause = () => {
    if (videoRef.current) {
      if (isPlaying) {
        videoRef.current.pause();
      } else {
        videoRef.current.play();
      }
      setIsPlaying(!isPlaying);
    }
  };

  const handleTimeUpdate = () => {
    if (videoRef.current) {
      setCurrentTime(videoRef.current.currentTime);
    }
  };

  const handleLoadedMetadata = () => {
    if (videoRef.current) {
      setDuration(videoRef.current.duration);
    }
  };

  const handleSeek = (e: React.ChangeEvent<HTMLInputElement>) => {
    const time = parseFloat(e.target.value);
    setCurrentTime(time);
    if (videoRef.current) {
      videoRef.current.currentTime = time;
    }
  };

  const handleVolumeChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const vol = parseFloat(e.target.value);
    setVolume(vol);
    if (videoRef.current) {
      videoRef.current.volume = vol;
    }
    if (vol > 0) {
      setIsMuted(false);
    }
  };

  const toggleMute = () => {
    if (videoRef.current) {
      if (isMuted) {
        videoRef.current.volume = volume;
        setIsMuted(false);
      } else {
        videoRef.current.volume = 0;
        setIsMuted(true);
      }
    }
  };

  const formatTime = (seconds: number): string => {
    if (!seconds || isNaN(seconds)) return "0:00";
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = Math.floor(seconds % 60);
    return h > 0
      ? `${h}:${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`
      : `${m}:${s.toString().padStart(2, "0")}`;
  };

  return (
    <div className="space-y-4">
      {/* Title */}
      {title && <h2 className="text-xl font-semibold">{title}</h2>}

      {/* Video Player */}
      <div className="bg-black rounded-lg overflow-hidden">
        <video
          ref={videoRef}
          className="w-full max-h-96 bg-black"
          onTimeUpdate={handleTimeUpdate}
          onLoadedMetadata={handleLoadedMetadata}
          onPlay={() => setIsPlaying(true)}
          onPause={() => setIsPlaying(false)}
        >
          <source src={videoPath} type="video/mp4" />
          {srtPath && <track kind="subtitles" src={srtPath} srcLang="en" />}
          Your browser does not support HTML5 video.
        </video>
      </div>

      {/* Controls */}
      <div className="space-y-3 bg-muted p-4 rounded-lg">
        {/* Play/Pause + Progress */}
        <div className="flex gap-3 items-center">
          <Button
            size="sm"
            onClick={handlePlayPause}
            className="gap-2"
          >
            {isPlaying ? (
              <Pause className="h-4 w-4" />
            ) : (
              <Play className="h-4 w-4" />
            )}
            {isPlaying ? "Pause" : "Play"}
          </Button>

          <div className="flex-1">
            <input
              type="range"
              min="0"
              max={duration || 0}
              value={currentTime}
              onChange={handleSeek}
              className="w-full"
              title="Seek"
            />
          </div>

          <span className="text-xs text-muted-foreground whitespace-nowrap">
            {formatTime(currentTime)} / {formatTime(duration)}
          </span>
        </div>

        {/* Volume + Download */}
        <div className="flex gap-3 items-center justify-between">
          <div className="flex gap-2 items-center">
            <Button
              size="sm"
              variant="outline"
              onClick={toggleMute}
              className="gap-2"
            >
              {isMuted ? (
                <VolumeX className="h-4 w-4" />
              ) : (
                <Volume2 className="h-4 w-4" />
              )}
            </Button>

            <input
              type="range"
              min="0"
              max="1"
              step="0.1"
              value={volume}
              onChange={handleVolumeChange}
              className="w-20"
              title="Volume"
            />
          </div>

          {onDownload && (
            <Button
              size="sm"
              variant="outline"
              onClick={onDownload}
              className="gap-2"
            >
              <Download className="h-4 w-4" />
              Download
            </Button>
          )}
        </div>
      </div>

      {/* Metadata */}
      {metadata && (
        <div className="grid grid-cols-3 gap-4 text-sm">
          {metadata.duration_seconds && (
            <div>
              <p className="text-xs text-muted-foreground">Duration</p>
              <p className="font-medium">
                {formatTime(metadata.duration_seconds)}
              </p>
            </div>
          )}
          {metadata.resolution && (
            <div>
              <p className="text-xs text-muted-foreground">Resolution</p>
              <p className="font-medium">{metadata.resolution}</p>
            </div>
          )}
          {metadata.size_mb && (
            <div>
              <p className="text-xs text-muted-foreground">Size</p>
              <p className="font-medium">{metadata.size_mb.toFixed(1)} MB</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default VideoPlayer;
