import * as React from "react";
import { useAuth } from "@/lib/auth";
import { Mic, MicOff, Volume2, VolumeX } from "lucide-react";
import {
  browserCreateSession, browserClose, browserNavigate, browserObserve,
  browserClick, browserFill, browserScroll, browserActions, browserConfirm,
  browserPause, browserAgent, browserAgentStop,
  transcribeAudio, ttsBlob,
  type BrowserObservation, type BrowserAction, type BrowserPending,
} from "@/lib/api";

/**
 * Browser Automation live view (ADR-0182 M3/M4). The user watches the agent-driven
 * browser as a live image, sees every action in real time, approves/declines
 * sensitive actions, and can pause / take over.
 *
 * Voice integration:
 *  - Agent steps are spoken aloud (opt-in, toggle top-right).
 *  - Pending confirmations trigger a voice prompt + auto-listen for "yes"/"no".
 *  - Task input supports hold-Space PTT (400 ms threshold).
 */
export function BrowserPage() {
  const { session } = useAuth();
  const csrf = session?.csrf_token ?? "";

  // Always-fresh CSRF ref — useCallback/useEffect closures read this
  // instead of capturing the value at creation time, so token refreshes
  // never leave a stale credential in the TTS/STT queue.
  const csrfRef = React.useRef(csrf);
  React.useEffect(() => { csrfRef.current = csrf; }, [csrf]);

  const [sid, setSid] = React.useState<string | null>(null);
  const [url, setUrl] = React.useState("https://example.com");
  const [obs, setObs] = React.useState<BrowserObservation | null>(null);
  const [actions, setActions] = React.useState<BrowserAction[]>([]);
  const [pending, setPending] = React.useState<BrowserPending[]>([]);
  const [paused, setPaused] = React.useState(false);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [task, setTask] = React.useState("");
  const [frameOk, setFrameOk] = React.useState(false);
  const sinceRef = React.useRef(0);
  const frameRef = React.useRef<HTMLImageElement | null>(null);

  // ── Voice state ──────────────────────────────────────────────────────────
  const [voiceOut, setVoiceOut] = React.useState(true);
  const [recording, setRecording] = React.useState(false);
  const [voiceStatus, setVoiceStatus] = React.useState<string | null>(null); // e.g. "Listening…"
  const audioRef = React.useRef<HTMLAudioElement | null>(null);
  const ttsQueueRef = React.useRef<Promise<void>>(Promise.resolve());
  const lastSpokenSeqRef = React.useRef(-1); // action index of last spoken step
  const lastPendingIdRef = React.useRef<string | null>(null); // pending ID we already asked about

  const lang = "de"; // matches the session language

  // Attach to an already-running session passed via ?sid=... (e.g. the deep
  // link chat's `/browser <task>` command sends: "open Browser in the
  // sidebar" used to mean "click Browser-Session starten", which creates a
  // BRAND NEW, disconnected, never-navigated session — the user then always
  // saw a blank about:blank tab because they were never looking at the
  // session the chat command actually started. Reading `sid` off the URL
  // and setting it directly (skipping browserCreateSession) makes the
  // existing frame/actions polling effect below attach to that real,
  // already-running session instead.
  React.useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const deepLinkSid = params.get("sid");
    if (deepLinkSid && !sid) {
      setSid(deepLinkSid);
      sinceRef.current = 0;
      setActions([]);
      setFrameOk(false);
      lastSpokenSeqRef.current = -1;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Speak text through TTS (queued — never overlaps).
  // Reads csrfRef.current at call-time so token refreshes are always picked up.
  const speak = React.useCallback((text: string) => {
    const token = csrfRef.current;
    if (!voiceOut || !token) return;
    ttsQueueRef.current = ttsQueueRef.current.then(async () => {
      try {
        const blob = await ttsBlob(text, lang, token);
        const objUrl = URL.createObjectURL(blob);
        await new Promise<void>((res) => {
          const a = new Audio(objUrl);
          audioRef.current = a;
          a.onended = () => { URL.revokeObjectURL(objUrl); res(); };
          a.onerror = () => { URL.revokeObjectURL(objUrl); res(); };
          void a.play();
        });
      } catch { /* TTS failure is non-fatal */ }
    });
  }, [voiceOut, lang]); // csrfRef is a stable ref — no need in deps

  const stopSpeaking = React.useCallback(() => {
    if (audioRef.current) { audioRef.current.pause(); audioRef.current = null; }
    // Reset queue so next speak() starts immediately.
    ttsQueueRef.current = Promise.resolve();
  }, []);

  // Record audio and return the transcribed text (or null on error).
  // Reads csrfRef.current at call-time.
  const recordAndTranscribe = React.useCallback(async (): Promise<string | null> => {
    try {
      setRecording(true);
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const chunks: Blob[] = [];
      const rec = new MediaRecorder(stream, { mimeType: "audio/webm" });
      await new Promise<void>((res) => {
        rec.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
        rec.onstop = () => res();
        rec.start();
        setTimeout(() => rec.stop(), 4000); // max 4 s listen window
      });
      stream.getTracks().forEach((t) => t.stop());
      const blob = new Blob(chunks, { type: "audio/webm" });
      const result = await transcribeAudio(blob, csrfRef.current);
      return result.text?.trim() ?? null;
    } catch {
      return null;
    } finally {
      setRecording(false);
    }
  }, []); // csrfRef is a stable ref — no deps needed

  // ── Auto-TTS: agent steps ────────────────────────────────────────────────
  // Speak whenever a new agent_step enters the log (most recent one wins).
  React.useEffect(() => {
    if (!voiceOut || actions.length === 0) return;
    for (let i = actions.length - 1; i > lastSpokenSeqRef.current; i--) {
      const a = actions[i];
      if (a.action === "agent_step" && a.plan) {
        lastSpokenSeqRef.current = i;
        speak(String(a.plan));
        return;
      }
      if (a.action === "agent_done" || a.action === "agent_error") {
        lastSpokenSeqRef.current = i;
        const msg = a.action === "agent_done"
          ? "Fertig."
          : `Fehler: ${(a as Record<string, unknown>)["error"] ?? "unbekannt"}`;
        speak(msg);
        return;
      }
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [actions, voiceOut]);

  // ── Auto-TTS: confirmations ──────────────────────────────────────────────
  // When a new pending confirmation appears, speak it aloud — but do NOT
  // auto-arm the microphone afterward. A sensitive action (buy/delete/log in)
  // must never be approvable by ambient noise or an unrelated "ja"/"yes" said
  // in the room during an auto-opened listening window; the user must
  // deliberately press the "Sprechen" mic button (or the Ja/Nein buttons)
  // below to respond.
  React.useEffect(() => {
    if (!voiceOut || pending.length === 0) return;
    const first = pending[0];
    if (first.id === lastPendingIdRef.current) return; // already handled
    lastPendingIdRef.current = first.id;

    const question =
      `Der Agent möchte "${first.action}" auf ${first.host} ausführen — ${first.name}. ` +
      `Sage "Ja" zum Bestätigen oder "Nein" zum Ablehnen.`;
    stopSpeaking();
    speak(question);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pending, voiceOut]);

  // ── PTT in task input (hold Space ≥ 400 ms) ─────────────────────────────
  const taskInputRef = React.useRef<HTMLInputElement>(null);
  const pttPendingRef = React.useRef(false);
  const recordingRef = React.useRef(false);
  React.useEffect(() => { recordingRef.current = recording; }, [recording]);

  React.useEffect(() => {
    const input = taskInputRef.current;
    if (!input) return;
    let holdTimer: ReturnType<typeof setTimeout> | null = null;

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.code !== "Space" || e.repeat) return;
      if (recordingRef.current) return;
      holdTimer = setTimeout(async () => {
        holdTimer = null;
        // Trim trailing space that landed before the hold threshold.
        setTask((t) => t.endsWith(" ") ? t.slice(0, -1) : t);
        pttPendingRef.current = true;
        setVoiceStatus("Aufnahme…");
        stopSpeaking();
        const text = await recordAndTranscribe();
        pttPendingRef.current = false;
        setVoiceStatus(null);
        if (text) setTask((t) => (t ? t + " " + text : text));
      }, 400);
    };
    const onKeyUp = (e: KeyboardEvent) => {
      if (e.code !== "Space") return;
      if (holdTimer !== null) { clearTimeout(holdTimer); holdTimer = null; }
    };
    input.addEventListener("keydown", onKeyDown);
    input.addEventListener("keyup", onKeyUp);
    return () => {
      input.removeEventListener("keydown", onKeyDown);
      input.removeEventListener("keyup", onKeyUp);
      if (holdTimer !== null) clearTimeout(holdTimer);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recordAndTranscribe, stopSpeaking]);

  // ── Computed helpers ─────────────────────────────────────────────────────

  const lastActionLabel = React.useMemo(() => {
    for (let i = actions.length - 1; i >= 0; i--) {
      const a = actions[i];
      const act = String(a.action ?? "");
      if (act === "agent_step") return `${a.plan ?? ""} — ${a.reason ?? ""}`;
      if (act === "navigate") return `Opening ${a.host ?? ""}…`;
      if (["click", "fill", "observe", "read", "scroll"].includes(act))
        return `${act}${a.host ? " · " + a.host : ""}`;
      if (act === "agent_start") return "Agent is starting…";
    }
    return "";
  }, [actions]);

  const browserActive = React.useMemo(
    () => frameOk || actions.some((a) =>
      ["navigate", "agent_start", "agent_step", "observe", "click", "fill", "read", "scroll"]
        .includes(String(a.action))),
    [frameOk, actions]);

  // ── Actions ──────────────────────────────────────────────────────────────

  const run = async (fn: () => Promise<unknown>) => {
    setError(null); setBusy(true);
    try { await fn(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };

  const start = () => run(async () => {
    const { session: s } = await browserCreateSession(csrfRef.current);
    setSid(s); sinceRef.current = 0; setActions([]); setFrameOk(false);
    lastSpokenSeqRef.current = -1;
    speak("Browser-Session gestartet. Gib eine Aufgabe ein oder navigiere direkt.");
  });

  const stop = () => run(async () => {
    stopSpeaking();
    if (sid) await browserClose(sid, csrfRef.current);
    setSid(null); setObs(null); setActions([]); setPending([]);
  });

  const go = () => sid && run(async () => setObs(await browserNavigate(sid, url, csrfRef.current)));
  const observe = () => sid && run(async () => setObs(await browserObserve(sid, csrfRef.current)));
  const click = (i: number) => sid && run(async () => { await browserClick(sid, i, csrfRef.current); setObs(await browserObserve(sid, csrfRef.current)); });
  const fill = (i: number) => {
    const text = window.prompt("Text to type into element " + i + ":");
    if (text != null && sid) run(() => browserFill(sid, i, text, csrfRef.current));
  };
  const scroll = (d: string) => sid && run(() => browserScroll(sid, d, csrfRef.current));
  const confirm = (id: string, approved: boolean) => sid && run(async () => {
    await browserConfirm(sid, id, approved, csrfRef.current);
    setPending((p) => p.filter((x) => x.id !== id));
  });
  const togglePause = () => sid && run(async () => { await browserPause(sid, !paused, csrfRef.current); setPaused(!paused); });
  const runAgent = () => sid && task.trim() && run(async () => {
    lastSpokenSeqRef.current = actions.length - 1; // don't re-speak old steps
    await browserAgent(sid, task.trim(), csrfRef.current);
  });
  const stopAgent = () => sid && run(() => browserAgentStop(sid, csrfRef.current));

  // Manual voice-record for task (tap mic button).
  const handleMicClick = async () => {
    if (recording) return;
    stopSpeaking();
    setVoiceStatus("Aufnahme…");
    const text = await recordAndTranscribe();
    setVoiceStatus(null);
    if (text) setTask((t) => (t ? t + " " + text : text));
  };

  // Poll the live frame (screencast) + the action log while a session is open.
  React.useEffect(() => {
    if (!sid) return;
    let alive = true;
    const tickFrame = () => {
      if (!alive || !frameRef.current) return;
      frameRef.current.src = `/v1/console/browser/${sid}/frame.jpg?t=${Date.now()}`;
    };
    const tickLog = async () => {
      if (!alive) return;
      try {
        const r = await browserActions(sid, sinceRef.current);
        sinceRef.current = r.next;
        if (r.actions.length) setActions((a) => [...a, ...r.actions].slice(-300));
        setPending(r.pending);
      } catch { /* transient */ }
    };
    const f = window.setInterval(tickFrame, 700);
    const l = window.setInterval(tickLog, 800);
    tickFrame(); tickLog();
    return () => { alive = false; window.clearInterval(f); window.clearInterval(l); };
  }, [sid]);

  // ── Render ───────────────────────────────────────────────────────────────

  return (
    <div className="p-4 space-y-4 max-w-6xl">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-lg font-semibold">Browser</h1>
          <p className="text-xs text-muted-foreground">
            Der Agent steuert einen echten Browser — du siehst jede Aktion live und
            kannst pausieren oder selbst übernehmen. Sensible Aktionen fragen zuerst.
          </p>
        </div>
        {/* Voice-out toggle */}
        <button
          onClick={() => setVoiceOut((v) => !v)}
          title={voiceOut ? "Sprachausgabe deaktivieren" : "Sprachausgabe aktivieren"}
          className={`flex items-center gap-1 rounded px-2 py-1 text-xs border transition-colors
            ${voiceOut
              ? "border-primary/50 bg-primary/10 text-primary"
              : "border-border text-muted-foreground"}`}
        >
          {voiceOut ? <Volume2 className="h-3.5 w-3.5" /> : <VolumeX className="h-3.5 w-3.5" />}
          Voice {voiceOut ? "an" : "aus"}
        </button>
      </div>

      {/* Voice status pill */}
      {(voiceStatus || recording) && (
        <div className="flex items-center gap-2 rounded-full bg-red-500/10 border border-red-400/30 px-3 py-1 text-xs text-red-600 dark:text-red-400 w-fit">
          <span className="h-2 w-2 rounded-full bg-red-500 animate-pulse" />
          {voiceStatus ?? "Aufnahme…"}
        </div>
      )}

      {/* Session controls */}
      {!sid ? (
        <button onClick={start} disabled={busy}
          className="rounded bg-primary text-primary-foreground text-sm px-3 py-1.5">
          Browser-Session starten
        </button>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <input value={url} onChange={(e) => setUrl(e.target.value)}
            className="flex-1 min-w-[240px] rounded border border-border bg-background px-2 py-1 text-sm"
            placeholder="https://…" onKeyDown={(e) => e.key === "Enter" && go()} />
          <button onClick={go} disabled={busy} className="rounded bg-primary text-primary-foreground text-sm px-3 py-1.5">Go</button>
          <button onClick={observe} disabled={busy} className="rounded border border-border text-sm px-2 py-1.5">Beobachten</button>
          <button onClick={() => scroll("down")} disabled={busy} className="rounded border border-border text-sm px-2 py-1.5">Scroll ↓</button>
          <button onClick={togglePause} disabled={busy}
            className={`rounded text-sm px-3 py-1.5 ${paused ? "bg-amber-500 text-white" : "border border-border"}`}>
            {paused ? "Fortsetzen (du hast die Kontrolle)" : "Pausieren / Übernehmen"}
          </button>
          <button onClick={stop} disabled={busy} className="rounded border border-destructive text-destructive text-sm px-2 py-1.5">Schließen</button>
        </div>
      )}

      {error && <p className="text-xs text-destructive bg-destructive/10 rounded px-2 py-1.5">{error}</p>}

      {/* Task input with voice */}
      {sid && (
        <div className="rounded border border-primary/40 bg-primary/5 p-3 space-y-2">
          <p className="text-sm font-medium">Aufgabe für den Browser</p>
          <p className="text-[11px] text-muted-foreground">
            Formuliere die Aufgabe in natürlicher Sprache. Der Agent führt sie Schritt für Schritt aus —
            du siehst alles live. Leertaste gedrückt halten = Sprachaufnahme.
          </p>
          <div className="flex gap-2">
            <div className="relative flex-1">
              <input
                ref={taskInputRef}
                value={task}
                onChange={(e) => setTask(e.target.value)}
                className="w-full rounded border border-border bg-background px-2 py-1 pr-8 text-sm"
                placeholder='z.B. "gehe zu heise.de und lies die Überschrift der ersten Nachricht vor"'
                onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && runAgent()}
              />
              {/* Inline mic button */}
              <button
                onClick={handleMicClick}
                disabled={recording}
                title="Aufgabe per Mikrofon eingeben"
                className={`absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-0.5
                  ${recording ? "text-red-500 animate-pulse" : "text-muted-foreground hover:text-foreground"}`}
              >
                {recording ? <MicOff className="h-3.5 w-3.5" /> : <Mic className="h-3.5 w-3.5" />}
              </button>
            </div>
            <button onClick={runAgent} disabled={busy || !task.trim()}
              className="rounded bg-primary text-primary-foreground text-sm px-3 py-1.5">Ausführen</button>
            <button onClick={stopAgent} disabled={busy}
              className="rounded border border-border text-sm px-2 py-1.5">Stop</button>
          </div>
        </div>
      )}

      {/* Pending confirmations — with voice-answer button */}
      {pending.length > 0 && (
        <div className="rounded border border-amber-500 bg-amber-500/10 p-3 space-y-2">
          <div className="flex items-center justify-between">
            <p className="text-sm font-medium">Sensible Aktion bestätigen</p>
            <span className="text-[11px] text-amber-700 dark:text-amber-400">
              {voiceOut ? "Sprachausgabe aktiv — sage Ja oder Nein" : ""}
            </span>
          </div>
          {pending.map((p) => (
            <div key={p.id} className="flex items-center justify-between gap-3 text-sm">
              <span>
                <span className="font-medium">{p.action}</span>
                {" "}"<span className="italic">{p.name}</span>" auf {p.host}
              </span>
              <span className="flex gap-2 items-center shrink-0">
                {voiceOut && (
                  <button
                    onClick={async () => {
                      stopSpeaking();
                      speak("Sage Ja zum Bestätigen oder Nein zum Ablehnen.");
                      setVoiceStatus("Warte auf Antwort…");
                      const text = await recordAndTranscribe();
                      setVoiceStatus(null);
                      if (!text) return;
                      const lower = text.toLowerCase();
                      const approved = /\b(ja|yes|bestätig|ok|genehmig)\b/.test(lower);
                      confirm(p.id, approved);
                    }}
                    disabled={recording}
                    title="Per Stimme antworten"
                    className="rounded border border-amber-400 text-amber-700 dark:text-amber-300 px-2 py-1 text-xs flex items-center gap-1"
                  >
                    <Mic className="h-3 w-3" /> Sprechen
                  </button>
                )}
                <button onClick={() => confirm(p.id, true)} className="rounded bg-emerald-600 text-white px-2 py-1 text-xs">Ja</button>
                <button onClick={() => confirm(p.id, false)} className="rounded bg-destructive text-white px-2 py-1 text-xs">Nein</button>
              </span>
            </div>
          ))}
        </div>
      )}

      {/* Live view + action log */}
      {sid && (
        <div className="grid grid-cols-1 lg:grid-cols-[2fr_1fr] gap-4">
          {/* Live browser frame */}
          <div className="relative rounded border border-border bg-slate-900 min-h-[420px] flex items-center justify-center overflow-hidden">
            {/* eslint-disable-next-line jsx-a11y/alt-text */}
            <img ref={frameRef} alt="live browser view"
              className={`max-w-full transition-opacity ${frameOk ? "opacity-100" : "opacity-0"}`}
              onLoad={() => setFrameOk(true)} onError={() => setFrameOk(false)} />
            {!frameOk && browserActive && (
              <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 px-6 text-center">
                <div className="h-6 w-6 rounded-full border-2 border-slate-600 border-t-primary animate-spin" />
                <span className="text-sm text-slate-200">{lastActionLabel || "Arbeite…"}</span>
              </div>
            )}
            {!frameOk && !browserActive && (
              <div className="absolute inset-0 flex flex-col items-center justify-center gap-1.5 px-8 text-center">
                <span className="text-sm text-slate-300">Kein Browser offen</span>
                <span className="text-[11px] text-slate-500">
                  URL eingeben und Go drücken, oder Aufgabe formulieren.
                  Der Browser öffnet sich erst bei der ersten Aktion.
                </span>
              </div>
            )}
          </div>

          {/* Element list + action log */}
          <div className="space-y-3">
            {obs && (
              <div className="rounded border border-border p-2">
                <p className="text-xs font-medium mb-1 truncate">{obs.title} — {obs.marks.length} Elemente</p>
                <div className="max-h-48 overflow-auto text-xs space-y-0.5">
                  {obs.marks.map((m) => (
                    <div key={m.index} className="flex items-center justify-between gap-2">
                      <span className="truncate">[{m.index}] {m.role}: {m.name}</span>
                      <span className="flex gap-1 shrink-0">
                        <button onClick={() => click(m.index)} className="text-primary hover:underline">klick</button>
                        {(m.role === "textbox" || m.role === "combobox") &&
                          <button onClick={() => fill(m.index)} className="text-primary hover:underline">ausfüllen</button>}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}
            <div className="rounded border border-border p-2">
              <p className="text-xs font-medium mb-1">Aktionsprotokoll</p>
              <div className="max-h-64 overflow-auto text-[11px] font-mono space-y-0.5">
                {actions.slice().reverse().map((a, i) => (
                  <div key={i}
                    className={`${a.action === "agent_step" ? "text-foreground" : "text-muted-foreground"}`}>
                    {a.action === "agent_step"
                      ? `▶ ${a.plan ?? a.action}`
                      : `${a.action}${a.host ? " · " + a.host : ""}${a.name ? " · " + a.name : ""}${a.ok === false ? " · ✗" : ""}`}
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
