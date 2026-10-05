/**
 * Push-to-talk voice input — extracted from pages/chat.tsx (ChatPane),
 * which had this built for the main session chat only, so GroupConversation
 * and PeerConversation can share the exact same mic behaviour.
 *
 * Primary path: the browser's Web Speech API (Chrome/Edge, no server
 * round-trip). Fallback: MediaRecorder → POST /v1/console/voice/transcribe
 * (Firefox, or whenever the Web Speech API errors out).
 *
 * Hold Space (≥400 ms) anywhere on the page to start recording, release to
 * stop; a short tap still types a normal space. Elements with a native
 * Space role (buttons, links, <select>, contentEditable — see
 * `isNativeSpaceTarget`) are never intercepted, matching the original
 * ChatPane behaviour exactly.
 *
 * Multiple composers on one page: the listeners are attached on `window`
 * but torn down on unmount via the effect's cleanup. On the unified chat
 * page (ADR-2218) only ONE of session/group/peer conversation is ever
 * mounted at a time (pages/chat.tsx renders them as alternatives, never
 * together), so only the currently-visible composer's hook instance is
 * ever listening — there is no global-hotkey race between composers
 * because they're never mounted simultaneously. `disabled` (pass
 * `streaming`/`busy`) additionally gates both the push-to-talk hold and a
 * manual start/stop call, so a caller never needs its own guard on top.
 */
import * as React from "react";
import { ApiError, transcribeAudio } from "@/lib/api";

// ── Web Speech API minimal surface (not in lib.dom.d.ts) ───────────────────
interface SpeechRecognitionResultLike {
  isFinal: boolean;
  0?: { transcript: string };
}
interface SpeechRecognitionEventLike {
  resultIndex: number;
  results: ArrayLike<SpeechRecognitionResultLike>;
}
interface SpeechRecognitionErrorLike {
  error?: string;
}
interface SpeechRecognitionLike {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onerror: ((event: SpeechRecognitionErrorLike) => void) | null;
  onend: (() => void) | null;
  start(): void;
  stop(): void;
}
type SpeechRecognitionCtor = new () => SpeechRecognitionLike;

function pickMime(): string {
  const candidates = [
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/ogg;codecs=opus",
    "audio/mp4",
  ];
  for (const c of candidates) {
    if (typeof MediaRecorder !== "undefined" && MediaRecorder.isTypeSupported(c)) return c;
  }
  return "";
}

export interface UseVoiceInputOptions {
  /** Current composer text value (read before/appended during STT). */
  value: string;
  /** Setter for the composer text. */
  onChange: (v: string) => void;
  csrf: string;
  /** Disables push-to-talk + the manual start/stop (e.g. while streaming). */
  disabled?: boolean;
  onError?: (message: string) => void;
  /** Fired on a 403 from the MediaRecorder→STT fallback (stale CSRF token). */
  onAuthExpired?: () => void;
}

export interface UseVoiceInputResult {
  recording: boolean;
  startRecording: () => Promise<void>;
  stopRecording: () => void;
}

export function useVoiceInput(opts: UseVoiceInputOptions): UseVoiceInputResult {
  const { value, onChange, csrf, disabled = false, onError, onAuthExpired } = opts;

  const [recording, setRecording] = React.useState(false);

  const mediaRef = React.useRef<MediaRecorder | null>(null);
  const chunksRef = React.useRef<Blob[]>([]);
  const recognitionRef = React.useRef<SpeechRecognitionLike | null>(null);

  const sttAccumRef = React.useRef("");
  const pttBaseRef = React.useRef("");
  const sttStoppingRef = React.useRef(false);
  const unmountedRef = React.useRef(false);
  const pttPendingRef = React.useRef(false);
  const recordingRef = React.useRef(false);
  const disabledRef = React.useRef(disabled);
  const valueRef = React.useRef(value);
  const onChangeRef = React.useRef(onChange);

  React.useEffect(() => { disabledRef.current = disabled; }, [disabled]);
  React.useEffect(() => { valueRef.current = value; }, [value]);
  React.useEffect(() => { onChangeRef.current = onChange; }, [onChange]);
  React.useEffect(() => {
    recordingRef.current = recording;
    if (recording) pttPendingRef.current = false;
  }, [recording]);

  const startRecording = React.useCallback(async () => {
    const w = window as Window & {
      SpeechRecognition?: SpeechRecognitionCtor;
      webkitSpeechRecognition?: SpeechRecognitionCtor;
    };
    const SpeechRecognitionImpl = w.SpeechRecognition ?? w.webkitSpeechRecognition;

    if (SpeechRecognitionImpl) {
      try {
        pttBaseRef.current = valueRef.current;
        sttAccumRef.current = "";
        sttStoppingRef.current = false;

        const recognition = new SpeechRecognitionImpl();
        recognition.continuous = true;
        recognition.interimResults = false;
        recognition.lang = navigator.language || "de-DE";
        recognition.onresult = (event: SpeechRecognitionEventLike) => {
          for (let i = event.resultIndex; i < event.results.length; i++) {
            const res = event.results[i];
            if (res.isFinal && res[0]?.transcript) {
              sttAccumRef.current += res[0].transcript + " ";
            }
          }
          const base = pttBaseRef.current;
          const acc = sttAccumRef.current.trim();
          onChangeRef.current(base ? `${base} ${acc}` : acc);
        };
        recognition.onerror = (ev: SpeechRecognitionErrorLike) => {
          if (ev?.error === "not-allowed" || ev?.error === "service-not-allowed") {
            sttStoppingRef.current = true;
          }
        };
        recognition.onend = () => {
          if (!sttStoppingRef.current) {
            try { recognition.start(); return; } catch { /* fall through */ }
          }
          recognitionRef.current = null;
          setRecording(false);
        };
        recognition.start();
        recognitionRef.current = recognition;
        setRecording(true);
        return;
      } catch {
        // Web Speech API start failed — fall through to MediaRecorder.
      }
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      chunksRef.current = [];
      const mr = new MediaRecorder(stream, { mimeType: pickMime() });
      mr.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };
      mr.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        if (unmountedRef.current) return;
        const blob = new Blob(chunksRef.current, { type: mr.mimeType || "audio/webm" });
        try {
          const r = await transcribeAudio(blob, csrf);
          onChangeRef.current(valueRef.current ? `${valueRef.current} ${r.text}` : r.text);
        } catch (e) {
          if (e instanceof ApiError && e.status === 403) {
            onAuthExpired?.();
            onError?.("Session expired — please record again.");
          } else {
            onError?.(e instanceof Error ? e.message : "Transcription failed");
          }
        }
      };
      mr.start();
      mediaRef.current = mr;
      setRecording(true);
    } catch (e) {
      onError?.(e instanceof Error ? e.message : "microphone access denied");
    }
  }, [csrf, onAuthExpired, onError]);

  const stopRecording = React.useCallback(() => {
    if (recognitionRef.current) {
      sttStoppingRef.current = true;
      try { recognitionRef.current.stop(); } catch { /* already ended */ }
      recognitionRef.current = null;
    } else {
      mediaRef.current?.stop();
      mediaRef.current = null;
    }
    setRecording(false);
  }, []);

  // Leaving the conversation while recording (the panes are keyed, so a
  // switch unmounts this one) must release the microphone: the recognizer
  // auto-restarts on `onend` until told to stop, and a MediaRecorder keeps
  // its stream tracks — the browser's mic indicator stayed on until the tab
  // closed. Nothing is transcribed into a composer that no longer exists.
  React.useEffect(() => () => {
    unmountedRef.current = true;
    sttStoppingRef.current = true;
    try { recognitionRef.current?.stop(); } catch { /* already ended */ }
    recognitionRef.current = null;
    const mr = mediaRef.current;
    mediaRef.current = null;
    if (mr) {
      try { if (mr.state !== "inactive") mr.stop(); } catch { /* already stopped */ }
      mr.stream.getTracks().forEach((t) => t.stop());
    }
  }, []);

  const startRecRef = React.useRef(startRecording);
  const stopRecRef = React.useRef(stopRecording);
  React.useEffect(() => { startRecRef.current = startRecording; }, [startRecording]);
  React.useEffect(() => { stopRecRef.current = stopRecording; }, [stopRecording]);

  // ── Push-to-talk: hold Space (≥400 ms) anywhere on the page ─────────────
  React.useEffect(() => {
    const isNativeSpaceTarget = (el: EventTarget | null): boolean => {
      if (!(el instanceof HTMLElement)) return false;
      const tag = el.tagName.toLowerCase();
      if (tag === "input" || tag === "select") return true;
      if (el.isContentEditable) return true;
      if (tag === "button" || tag === "a") return true;
      return false;
    };
    const isTextarea = (el: EventTarget | null): boolean =>
      el instanceof HTMLElement && el.tagName.toLowerCase() === "textarea";

    let holdTimer: ReturnType<typeof setTimeout> | null = null;

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.code !== "Space" || e.repeat || e.metaKey || e.ctrlKey || e.altKey) return;
      if (isNativeSpaceTarget(e.target)) return;
      if (recordingRef.current || disabledRef.current) return;

      const inTextarea = isTextarea(e.target);
      if (!inTextarea) e.preventDefault();

      holdTimer = setTimeout(() => {
        holdTimer = null;
        if (recordingRef.current || disabledRef.current) return;
        if (inTextarea) {
          const cur = valueRef.current;
          if (cur.endsWith(" ")) onChangeRef.current(cur.slice(0, -1));
        }
        pttPendingRef.current = true;
        void startRecRef.current();
      }, 400);
    };

    const onKeyUp = (e: KeyboardEvent) => {
      if (e.code !== "Space") return;
      if (holdTimer !== null) {
        clearTimeout(holdTimer);
        holdTimer = null;
        return;
      }
      if (!recordingRef.current && !pttPendingRef.current) return;
      pttPendingRef.current = false;
      e.preventDefault();
      stopRecRef.current();
    };

    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
      if (holdTimer !== null) clearTimeout(holdTimer);
    };
  }, []);

  return { recording, startRecording, stopRecording };
}
