/**
 * Playback Tab — HTML5 video player + transcript
 */

import React from "react";
import { useQuery } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { Progress } from "@/components/ui/progress";

interface Job {
  id: string;
  task: string;
  status: string;
  percent: number;
  current_step?: string | null;
  current_scene?: number | null;
  total_scenes?: number | null;
  error_message?: string | null;
}

function parseSrt(text: string): Array<{ start: string; text: string }> {
  return text
    .split(/\n\s*\n/)
    .map((b) => b.trim().split("\n"))
    .filter((l) => l.length >= 3)
    .map((l) => ({
      start: l[1].split("-->")[0].trim().replace(/,\d+$/, ""),
      text: l.slice(2).join(" "),
    }));
}

interface PlaybackTabProps {
  job: Job;
}

const PlaybackTab: React.FC<PlaybackTabProps> = ({ job }) => {
  const captions = useQuery({
    queryKey: ["video", "captions", job.id],
    queryFn: ({ signal }) =>
      fetch(`/v1/console/video/videos/${job.id}/captions`, { signal }).then((r) =>
        r.json() as Promise<{ content: string }>
      ),
    retry: false,
    enabled: job.status === "complete",
  });

  if (job.status !== "complete") {
    return (
      <div className="space-y-3">
        <div className="flex justify-between text-xs text-muted-foreground">
          <span>{job.current_step || "Working…"}</span>
          <span>{job.percent ?? 0}%</span>
        </div>
        <Progress value={job.percent ?? 0} />
        {job.total_scenes ? (
          <p className="text-xs text-muted-foreground">
            Scene {job.current_scene ?? 0} of {job.total_scenes}
          </p>
        ) : null}
        {job.error_message && (
          <div className="text-sm rounded-md border border-destructive/30 bg-destructive/10 p-3 text-destructive">
            {job.error_message}
          </div>
        )}
      </div>
    );
  }

  const cues = captions.data ? parseSrt(captions.data.content) : [];

  return (
    <div className="space-y-4">
      {/* Video Player */}
      <div className="rounded-lg overflow-hidden bg-black aspect-video flex items-center justify-center">
        <video
          src={`/v1/console/video/videos/${job.id}/download`}
          controls
          className="w-full h-full"
          data-testid="video-player"
        />
      </div>

      {/* Download Button */}
      <button
        onClick={() => {
          window.location.href = `/v1/console/video/videos/${job.id}/download`;
        }}
        className="inline-block px-4 py-2 rounded-md bg-accent text-accent-foreground hover:bg-accent/90"
      >
        ⬇️ Download video
      </button>

      {/* Transcript */}
      <div>
        <div className="text-sm font-semibold mb-2">
          Transcript <span className="text-muted-foreground font-normal">· {captions.isError ? "no captions" : `${cues.length} cues`}</span>
        </div>
        <ol className="max-h-56 overflow-y-auto space-y-1 text-sm" data-testid="transcript">
          {cues.map((c, i) => (
            <li key={i} className="grid grid-cols-[5rem_1fr] gap-2">
              <span className="font-mono text-xs text-muted-foreground">{c.start}</span>
              <span>{c.text}</span>
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
};

export default PlaybackTab;
