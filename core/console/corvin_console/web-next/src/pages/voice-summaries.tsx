import * as React from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Headphones, Loader2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { listVoiceSummaries, type VoiceSummary } from "@/lib/api";

function formatWhen(epochSeconds: number | null): string {
  if (!epochSeconds) return "";
  return new Date(epochSeconds * 1000).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

function SummaryRow({ item }: { item: VoiceSummary }) {
  return (
    <Card>
      <CardContent className="flex flex-col gap-3 py-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <Link
            to={`/app/chat/${item.sid}`}
            className="min-w-0 truncate font-medium hover:underline"
            title="Open this chat"
          >
            {item.title}
          </Link>
          <div className="flex shrink-0 items-center gap-2 text-xs text-muted-foreground">
            {item.lang && (
              <Badge variant="outline" className="font-mono uppercase">
                {item.lang}
              </Badge>
            )}
            <span>{formatWhen(item.created_at)}</span>
          </div>
        </div>
        {item.text && (
          <p className="text-sm text-muted-foreground">{item.text}</p>
        )}
        {/* Native controls: seek, play/pause, volume — no custom player needed
            for a single per-chat recap. Range requests are supported by the
            underlying file route (FileResponse), so scrubbing works. */}
        <audio controls preload="none" className="w-full" src={item.audio_url}>
          Your browser does not support inline audio playback.
        </audio>
      </CardContent>
    </Card>
  );
}

export function VoiceSummariesPage() {
  const q = useQuery({
    queryKey: ["voice-summaries"],
    queryFn: ({ signal }) => listVoiceSummaries(signal),
    // A chat's summary regenerates in the background as the conversation
    // continues; refresh often enough that a newly-generated one shows up
    // without a manual reload, cheaply enough not to matter at rest.
    refetchInterval: 30_000,
  });

  return (
    <div className="mx-auto w-full max-w-3xl space-y-6 p-6">
      <div className="flex items-center gap-3">
        <Headphones className="h-6 w-6 text-muted-foreground" />
        <div>
          <h1 className="text-xl font-semibold">Voice Summaries</h1>
          <p className="text-sm text-muted-foreground">
            A spoken recap of each chat, generated automatically as the conversation
            progresses — independent of whether that chat is open. Listen back anytime,
            in any session.
          </p>
        </div>
      </div>

      {q.isLoading && (
        <div className="flex items-center justify-center py-12 text-muted-foreground">
          <Loader2 className="h-5 w-5 animate-spin" />
        </div>
      )}

      {q.isError && (
        <Card>
          <CardContent className="flex items-center gap-2 py-6 text-sm text-destructive">
            <AlertTriangle className="h-4 w-4" />
            Could not load voice summaries.
          </CardContent>
        </Card>
      )}

      {q.data && q.data.summaries.length === 0 && (
        <Card>
          <CardContent className="py-10 text-center text-sm text-muted-foreground">
            No voice summaries yet. A chat gets one automatically once it has had a few
            exchanges — come back after a longer conversation.
          </CardContent>
        </Card>
      )}

      {q.data && q.data.summaries.length > 0 && (
        <div className="space-y-3">
          {q.data.summaries.map((item) => (
            <SummaryRow key={item.sid} item={item} />
          ))}
        </div>
      )}
    </div>
  );
}
