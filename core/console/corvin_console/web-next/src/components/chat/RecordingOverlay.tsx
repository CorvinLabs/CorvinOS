/**
 * "Recording — release Space to send" banner — extracted from
 * pages/chat.tsx (ChatPane) for reuse by GroupConversation.tsx and
 * PeerConversation.tsx (see hooks/use-voice-input.ts for the recording
 * state it reflects).
 */
import * as React from "react";

export function RecordingOverlay({ onStop }: { onStop: () => void }) {
  return (
    <div className="pointer-events-none sticky top-0 z-10 -mx-4 -mt-5 mb-5 flex justify-center px-4 pt-3 md:-mx-6 md:px-6">
      <div className="pointer-events-auto flex items-center gap-3 rounded-full border border-destructive/40 bg-destructive/10 px-4 py-2 text-sm shadow-lg backdrop-blur">
        <span className="relative flex h-2.5 w-2.5">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-destructive opacity-75" />
          <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-destructive" />
        </span>
        <span className="font-medium text-destructive">
          Recording — release <kbd className="rounded bg-background/60 px-1.5 py-0.5 font-mono text-[11px]">Space</kbd> to send
        </span>
        <button
          onClick={onStop}
          className="rounded-full bg-destructive px-2 py-0.5 text-[11px] font-medium text-destructive-foreground hover:bg-destructive/90"
        >
          Stop
        </button>
      </div>
    </div>
  );
}
