import * as React from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Check,
  CheckCircle2,
  Eraser,
  FileText,
  KeyRound,
  Loader2,
  Sparkles,
  User,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { ReauthDialog } from "@/components/reauth-dialog";
import {
  getProfile,
  getVoiceStatus,
  previewProfile,
  putProfile,
  resetProfile,
  testVoice,
  type AudienceFields,
  type IdentityFields,
  type VoiceProviderStatus,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { HelpTooltip } from "@/components/ui/help-tooltip";

const LEVEL_OPTIONS = ["novice", "intermediate", "expert"] as const;
const STYLE_OPTIONS = ["concise", "verbose", "example-driven"] as const;
const TOGGLE_OPTIONS = ["on", "off"] as const;
const TTS_VOICE_OPTIONS = ["alloy", "echo", "fable", "onyx", "nova", "shimmer"] as const;

const TTS_PROVIDER_OPTIONS = [
  {
    value: "auto",
    label: "Auto (OpenAI → edge-tts → Piper)",
    badge: "Cloud/Local",
    badgeVariant: "neutral" as const,
    description: "Uses the best available provider in order.",
    dataResidency: null,
  },
  {
    value: "openai",
    label: "OpenAI TTS-1",
    badge: "Cloud · US",
    badgeVariant: "cloud" as const,
    description: "Best quality. Requires OPENAI_API_KEY. Audio sent to OpenAI (US).",
    dataResidency: "us",
  },
  {
    value: "edge",
    label: "edge-tts (Microsoft Edge)",
    badge: "Cloud · EU-MS",
    badgeVariant: "cloud" as const,
    description: "No API key. Requires internet. Audio sent to Microsoft TTS (pip install edge-tts).",
    dataResidency: "eu-ms",
  },
  {
    value: "piper",
    label: "Piper — Local / Offline",
    badge: "Local · No egress",
    badgeVariant: "local" as const,
    description: "Fully offline. No data leaves the host. Requires piper-tts and model files.",
    dataResidency: "local",
  },
] as const;

type TtsProvider = "auto" | "openai" | "edge" | "piper";
const LANG_OPTIONS = [
  { value: "de", label: "German" },
  { value: "en", label: "English" },
  { value: "es", label: "Español" },
  { value: "fr", label: "Français" },
  { value: "it", label: "Italiano" },
] as const;

const DEBOUNCE_MS = 800;

type SaveStatus = "idle" | "saving" | "saved" | "error";
type ErrorKind = "save" | "reset";
type SaveArgs = { identity: IdentityFields; audience: AudienceFields };

/** Client-side mirror of profile.for_system_prompt() for live preview. */
function computeSystemBlock(id: IdentityFields): string {
  const lines: string[] = [];
  if (id.name) lines.push(`- Name: ${id.name}`);
  if (id.display_language)
    lines.push(
      `- Language: ${id.display_language} (default; still match the user's actual writing language)`,
    );
  if (id.tone) lines.push(`- Tone: ${id.tone}`);
  if (id.timezone) lines.push(`- Timezone: ${id.timezone}`);
  if (id.voice_note_max_sentences)
    lines.push(`- Voice-note summary cap: ${id.voice_note_max_sentences} sentences`);
  if (id.custom_instructions)
    lines.push(`- Custom instructions: ${id.custom_instructions}`);
  if (lines.length === 0) return "";
  return (
    "\n\nAbout the user (always available across every chat / bridge / persona — keep these in mind):\n" +
    lines.join("\n")
  );
}

export function VoicePage() {
  const qc = useQueryClient();
  const { session } = useAuth();

  const profileQ = useQuery({
    queryKey: ["profile"],
    queryFn: ({ signal }) => getProfile(signal),
  });

  const [identity, setIdentity] = React.useState<IdentityFields>({});
  const [audience, setAudience] = React.useState<AudienceFields>({});
  const [dirty, setDirty] = React.useState(false);
  const [saveStatus, setSaveStatus] = React.useState<SaveStatus>("idle");
  const [saveError, setSaveError] = React.useState<string | null>(null);
  const [errorKind, setErrorKind] = React.useState<ErrorKind>("save");
  const [reauthOpen, setReauthOpen] = React.useState(false);
  const savedTimerRef = React.useRef<ReturnType<typeof setTimeout> | null>(null);

  // Seed the local state from the snapshot once it arrives.
  React.useEffect(() => {
    if (profileQ.data) {
      setIdentity({ ...profileQ.data.profile.identity });
      setAudience({ ...profileQ.data.profile.audience });
      setDirty(false);
    }
  }, [profileQ.data]);

  const saveMutation = useMutation({
    mutationFn: async ({ identity: id, audience: aud }: SaveArgs) =>
      putProfile({ identity: id, audience: aud }, session!.csrf_token),
    onSuccess: async () => {
      setSaveStatus("saved");
      setDirty(false);
      if (savedTimerRef.current) clearTimeout(savedTimerRef.current);
      savedTimerRef.current = setTimeout(() => setSaveStatus("idle"), 2000);
      await qc.invalidateQueries({ queryKey: ["profile"] });
    },
    onError: (e: Error) => {
      setErrorKind("save");
      setSaveStatus("error");
      setSaveError(e.message);
      throw e;
    },
  });

  const resetMutation = useMutation({
    mutationFn: async () => resetProfile(session!.csrf_token),
    onSuccess: async () => {
      setSaveStatus("saved");
      if (savedTimerRef.current) clearTimeout(savedTimerRef.current);
      savedTimerRef.current = setTimeout(() => setSaveStatus("idle"), 2000);
      await qc.invalidateQueries({ queryKey: ["profile"] });
    },
    onError: (e: Error) => {
      setErrorKind("reset");
      setSaveStatus("error");
      setSaveError(e.message);
      throw e;
    },
  });

  const voiceTestMutation = useMutation({
    mutationFn: async (voice: string) =>
      testVoice(voice, identity.display_language === "de" ? "de" : "en", session!.csrf_token),
    onSuccess: async (data) => {
      const audio = new Audio(`data:${data.mime_type};base64,${data.audio_base64}`);
      audio.play().catch((e) => console.error("Failed to play audio:", e));
    },
    onError: (e: Error) => {
      console.error("Voice test failed:", e);
      setSaveError(`Voice test failed: ${e.message}`);
    },
  });

  // Auto-save: debounced 800 ms after the last change.
  React.useEffect(() => {
    if (!dirty || !session?.csrf_token) return;
    setSaveError(null);
    const snap = { identity, audience };
    const timer = setTimeout(() => {
      setSaveStatus("saving");
      saveMutation.mutate({ ...snap });
    }, DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [dirty, identity, audience, session?.csrf_token]); // eslint-disable-line react-hooks/exhaustive-deps

  const previewQ = useQuery({
    queryKey: ["profile", "preview", JSON.stringify(audience), session?.csrf_token],
    queryFn: async () => {
      if (!session?.csrf_token) throw new Error("no session");
      const en = await previewProfile(audience, "en", session.csrf_token);
      return { en: en.block };
    },
    enabled: !!session?.csrf_token && !!profileQ.data,
    staleTime: 1_000,
  });

  // Live system prompt block — recomputed locally so it updates as you type.
  const systemBlock = React.useMemo(() => computeSystemBlock(identity), [identity]);

  const updateIdentity = <K extends keyof IdentityFields>(key: K, value: IdentityFields[K]) => {
    setIdentity((s) => ({ ...s, [key]: value }));
    setDirty(true);
    setSaveError(null);
  };
  const updateAudience = <K extends keyof AudienceFields>(key: K, value: AudienceFields[K]) => {
    setAudience((s) => ({ ...s, [key]: value }));
    setDirty(true);
    setSaveError(null);
  };

  // Show skeleton while loading initially OR while React Query is retrying after
  // a transient error (isError=true but isFetching=true means a retry is in flight).
  if (profileQ.isLoading || (profileQ.isError && profileQ.isFetching)) {
    return (
      <div className="mx-auto max-w-5xl space-y-4">
        <Skeleton className="h-10 w-1/3" />
        <Skeleton className="h-64 w-full" />
        <Skeleton className="h-48 w-full" />
      </div>
    );
  }

  if (profileQ.isError || !profileQ.data) {
    return (
      <Card className="border-destructive/40 bg-destructive/5">
        <CardContent className="py-4 text-sm text-destructive">
          Voice profile failed to load: {(profileQ.error as Error | undefined)?.message ?? "unknown"}
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="font-serif text-3xl font-light tracking-tight">Voice</h1>
            <HelpTooltip title="Voice profile" side="right" width="lg">
              Configure how Corvin sounds and behaves when talking to you.
              <br /><br />
              <strong>Identity</strong> — name, tone, timezone, and language injected into the AI's system prompt.
              <br /><br />
              <strong>Audience</strong> — tunes text-to-speech output: expertise level, response style, and preferred language for voice notes.
            </HelpTooltip>
          </div>
          <p className="mt-1 text-sm text-muted-foreground">
            Configure your voice assistant: identity, speech style, and audio preferences.
          </p>
        </div>
        <div className="flex items-center gap-3">
          {/* Auto-save status indicator */}
          <div className="flex min-w-[5rem] items-center justify-end gap-1.5 text-xs">
            {saveStatus === "saving" && (
              <>
                <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" />
                <span className="text-muted-foreground">Saving…</span>
              </>
            )}
            {saveStatus === "saved" && (
              <>
                <Check className="h-3 w-3 text-emerald-500" />
                <span className="text-emerald-600 dark:text-emerald-400">Saved</span>
              </>
            )}
            {saveStatus === "error" && (
              <span className="text-destructive" title={saveError ?? undefined}>
                ⚠ {errorKind === "reset" ? "Reset failed" : "Save failed"}
              </span>
            )}
          </div>
          <Button
            variant="outline"
            disabled={resetMutation.isPending}
            onClick={() => setReauthOpen(true)}
          >
            {resetMutation.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Eraser className="h-4 w-4" />
            )}
            Reset
          </Button>
        </div>
      </div>

      {/* Provider status (ADR-0185 M4) */}
      <VoiceStatusPanel />

      {/* Identity */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <User className="h-4 w-4 text-accent" />
            <CardTitle className="text-base">Identity</CardTitle>
            <HelpTooltip title="Identity" side="right" width="md">
              These fields are injected into Corvin's system prompt so the AI knows who it's talking to.{" "}
              <strong>Name</strong> and <strong>tone</strong> shape the personality.{" "}
              <strong>Display language</strong> sets the default reply and TTS language.
            </HelpTooltip>
          </div>
          <CardDescription>
            Surfaced in the system prompt via{" "}
            <span className="font-mono">profile.for_system_prompt()</span>.
            All changes are auto-saved.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 md:grid-cols-2">
            <Field label="Name">
              <Input
                value={identity.name ?? ""}
                maxLength={120}
                placeholder="How would you like to be addressed?"
                onChange={(e) => updateIdentity("name", e.target.value || null)}
              />
            </Field>
            <Field
              label={
                <span className="flex items-center gap-1.5">
                  Display language
                  <HelpTooltip title="Display language" side="right" width="md">
                    Controls the default reply language and the TTS voice output language.
                    German and English have dedicated TTS blocks; other languages use the
                    English block format but produce output in the chosen language.
                  </HelpTooltip>
                </span>
              }
            >
              <Select
                value={identity.display_language ?? ""}
                onChange={(e) => updateIdentity("display_language", e.target.value || null)}
                placeholder="System default"
              >
                <option value="">System default</option>
                {LANG_OPTIONS.map((l) => (
                  <option key={l.value} value={l.value}>
                    {l.label} ({l.value})
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Tone">
              <Input
                value={identity.tone ?? ""}
                maxLength={80}
                placeholder="e.g. concise, warm, irreverent"
                onChange={(e) => updateIdentity("tone", e.target.value || null)}
              />
            </Field>
            <Field label="Timezone">
              <Input
                value={identity.timezone ?? ""}
                maxLength={60}
                placeholder="e.g. Europe/Berlin"
                onChange={(e) => updateIdentity("timezone", e.target.value || null)}
              />
            </Field>
            <Field label="Default persona">
              <Input
                value={identity.default_persona ?? ""}
                maxLength={60}
                placeholder="e.g. assistant, coder, research"
                onChange={(e) => updateIdentity("default_persona", e.target.value || null)}
              />
            </Field>
            <Field
              label={
                <span className="flex items-center gap-1.5">
                  Voice-note max sentences
                  <HelpTooltip title="Voice-note max sentences" side="right" width="md">
                    Asks the AI to keep voice summaries to this many sentences (1–10).
                    This is advisory — Claude is instructed to respect it but there is
                    no hard character cut-off.
                  </HelpTooltip>
                </span>
              }
            >
              <Input
                type="number"
                min={1}
                max={10}
                placeholder="1–10 sentences"
                value={identity.voice_note_max_sentences ?? ""}
                onChange={(e) =>
                  updateIdentity(
                    "voice_note_max_sentences",
                    e.target.value === "" ? null : Math.max(1, Math.min(10, Number(e.target.value))),
                  )
                }
              />
            </Field>
          </div>

          {/* Custom instructions — full-width */}
          <Field
            label={
              <span className="flex items-center gap-1.5">
                Custom instructions (≤ 500 chars)
                <HelpTooltip title="Custom instructions" side="right" width="md">
                  Free-form text appended to every system prompt as
                  "Custom instructions: …". Use this to steer Claude's behaviour
                  globally across all chats — e.g. "Always reply in bullet points"
                  or "Prefer German for technical terms".
                </HelpTooltip>
              </span>
            }
          >
            <Textarea
              rows={3}
              maxLength={500}
              value={identity.custom_instructions ?? ""}
              placeholder='e.g. "Always structure replies with a summary first." or "Use metric units."'
              onChange={(e) =>
                updateIdentity("custom_instructions", e.target.value || null)
              }
              className="font-sans text-sm"
            />
          </Field>
        </CardContent>
      </Card>

      {/* System prompt preview — live, mirrors for_system_prompt() */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <FileText className="h-4 w-4 text-accent" />
            <CardTitle className="text-base">System prompt block</CardTitle>
            <HelpTooltip title="System prompt block" side="right" width="md">
              This is the exact text injected into every AI system prompt based
              on your Identity settings above. It updates live as you type.
              Empty when no identity fields are filled in.
            </HelpTooltip>
          </div>
          <CardDescription>
            Live preview of what gets prepended to every chat's system prompt.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <pre className="min-h-[4rem] rounded-md border border-border/60 bg-muted/30 px-3 py-2 font-mono text-[11px] leading-relaxed text-muted-foreground whitespace-pre-wrap overflow-x-auto">
            {systemBlock.trim()
              ? systemBlock
              : "— empty (no identity fields set) —"}
          </pre>
        </CardContent>
      </Card>

      {/* Voice audience — the Layer-12 fields */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-accent" />
            <CardTitle className="text-base">Voice style settings</CardTitle>
          </div>
          <CardDescription>
            Tunes how TTS explanations are framed for you. Live TTS block preview on the right.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-6 lg:grid-cols-[1fr_1fr]">
          <div className="space-y-4">
            <Field label="Level">
              <Select
                value={audience.voice_audience_level ?? ""}
                onChange={(e) =>
                  updateAudience(
                    "voice_audience_level",
                    (e.target.value || null) as AudienceFields["voice_audience_level"],
                  )
                }
                placeholder="Not set"
              >
                <option value="">Not set</option>
                {LEVEL_OPTIONS.map((v) => (
                  <option key={v} value={v}>
                    {v}
                  </option>
                ))}
              </Select>
            </Field>
            <Field
              label={
                <span className="flex items-center gap-1.5">
                  Jargon tolerance
                  <span className="text-muted-foreground font-normal">
                    ({audience.voice_audience_jargon ?? "—"}/5)
                  </span>
                </span>
              }
            >
              <div className="space-y-1">
                <input
                  type="range"
                  min={0}
                  max={5}
                  value={audience.voice_audience_jargon ?? 0}
                  onChange={(e) =>
                    updateAudience("voice_audience_jargon", Number(e.target.value))
                  }
                  className="w-full accent-[hsl(var(--accent))]"
                />
                <div className="flex justify-between text-[10px] text-muted-foreground">
                  <span>0 — plain language</span>
                  <span>5 — full technical jargon</span>
                </div>
              </div>
            </Field>
            <Field label="Style">
              <Select
                value={audience.voice_audience_style ?? ""}
                onChange={(e) =>
                  updateAudience(
                    "voice_audience_style",
                    (e.target.value || null) as AudienceFields["voice_audience_style"],
                  )
                }
                placeholder="Not set"
              >
                <option value="">Not set</option>
                {STYLE_OPTIONS.map((v) => (
                  <option key={v} value={v}>
                    {v}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Background (≤ 200 chars)">
              <Textarea
                rows={3}
                maxLength={200}
                value={audience.voice_audience_background ?? ""}
                placeholder="e.g. 10 years Go, new to React; data engineer; learning Rust"
                onChange={(e) =>
                  updateAudience("voice_audience_background", e.target.value || null)
                }
                className="font-sans text-sm"
              />
            </Field>
            <div className="grid grid-cols-2 gap-4">
              <Field label="Metaphors">
                <Select
                  value={audience.voice_audience_metaphors ?? ""}
                  onChange={(e) =>
                    updateAudience(
                      "voice_audience_metaphors",
                      (e.target.value || null) as AudienceFields["voice_audience_metaphors"],
                    )
                  }
                  placeholder="Not set"
                >
                  <option value="">Not set</option>
                  {TOGGLE_OPTIONS.map((v) => (
                    <option key={v} value={v}>
                      {v}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Chat-render">
                <Select
                  value={audience.voice_audience_chat_render ?? ""}
                  onChange={(e) =>
                    updateAudience(
                      "voice_audience_chat_render",
                      (e.target.value || null) as AudienceFields["voice_audience_chat_render"],
                    )
                  }
                  placeholder="Not set"
                >
                  <option value="">Not set</option>
                  {TOGGLE_OPTIONS.map((v) => (
                    <option key={v} value={v}>
                      {v}
                    </option>
                  ))}
                </Select>
              </Field>
            </div>
            <Field label="Domains (comma-separated, max 8)">
              <Input
                value={(audience.voice_audience_domains ?? []).join(", ")}
                placeholder="e.g. backend, react, postgres"
                onChange={(e) => {
                  const parts = e.target.value
                    .split(",")
                    .map((s) => s.trim())
                    .filter(Boolean)
                    .slice(0, 8);
                  updateAudience(
                    "voice_audience_domains",
                    parts.length ? parts : null,
                  );
                }}
              />
            </Field>
            <Field
              label={
                <span className="flex items-center gap-1.5">
                  Learning mode
                  <span className="text-muted-foreground font-normal">
                    ({audience.voice_audience_learning ?? "—"}/3)
                  </span>
                </span>
              }
            >
              <div className="space-y-1">
                <input
                  type="range"
                  min={0}
                  max={3}
                  value={audience.voice_audience_learning ?? 0}
                  onChange={(e) =>
                    updateAudience("voice_audience_learning", Number(e.target.value))
                  }
                  className="w-full accent-[hsl(var(--accent))]"
                />
                <div className="flex justify-between text-[10px] text-muted-foreground">
                  <span>0 — off</span>
                  <span>3 — teach + recap</span>
                </div>
              </div>
            </Field>

            <div className="border-t pt-4">
              <h3 className="text-sm font-semibold mb-4 flex items-center gap-2">
                <Sparkles className="h-4 w-4 text-accent" />
                Text-to-Speech Voice
              </h3>

              {/* TTS Provider */}
              <Field label="TTS Provider">
                {(() => {
                  const currentProvider = (audience.tts_provider ?? "auto") as TtsProvider;
                  const currentOpt = TTS_PROVIDER_OPTIONS.find(o => o.value === currentProvider)
                    ?? TTS_PROVIDER_OPTIONS[0];
                  return (
                    <>
                      <Select
                        value={currentProvider}
                        onChange={(e) => {
                          const v = e.target.value;
                          updateAudience("tts_provider", (v === "auto" || !v) ? null : v as TtsProvider);
                        }}
                      >
                        {TTS_PROVIDER_OPTIONS.map((opt) => (
                          <option key={opt.value} value={opt.value}>
                            {opt.label}
                          </option>
                        ))}
                      </Select>
                      <div className="mt-2 flex items-start gap-2">
                        <span
                          className={[
                            "inline-flex shrink-0 items-center rounded px-1.5 py-0.5 text-[10px] font-medium",
                            currentOpt.badgeVariant === "local"
                              ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300"
                              : currentOpt.badgeVariant === "cloud"
                              ? "bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-300"
                              : "bg-muted text-muted-foreground",
                          ].join(" ")}
                        >
                          {currentOpt.badge}
                        </span>
                        <p className="text-[11px] text-muted-foreground">
                          {currentOpt.description}
                          {currentOpt.value === "piper" && (
                            <>
                              {" "}Place model files in{" "}
                              <code className="font-mono text-[10px]">
                                ~/.config/corvin-voice/piper-models/
                              </code>
                              .
                            </>
                          )}
                        </p>
                      </div>
                    </>
                  );
                })()}
              </Field>

              {/* OpenAI voice selector — only relevant when provider is openai or auto */}
              {(audience.tts_provider ?? "auto") !== "piper" &&
                (audience.tts_provider ?? "auto") !== "edge" && (
                <Field label="OpenAI Voice">
                  <div className="flex gap-2">
                    <div className="flex-1">
                      <Select
                        value={audience.tts_voice ?? "nova"}
                        onChange={(e) =>
                          updateAudience(
                            "tts_voice",
                            (e.target.value || null) as AudienceFields["tts_voice"],
                          )
                        }
                        placeholder="Nova (Default)"
                      >
                        {TTS_VOICE_OPTIONS.map((v) => (
                          <option key={v} value={v}>
                            {v}{v === "nova" ? " (Default)" : ""}
                          </option>
                        ))}
                      </Select>
                    </div>
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={voiceTestMutation.isPending}
                      onClick={() => voiceTestMutation.mutate(audience.tts_voice ?? "nova")}
                      className="whitespace-nowrap"
                    >
                      {voiceTestMutation.isPending ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : (
                        "🔊 Test"
                      )}
                    </Button>
                  </div>
                  <p className="text-[11px] text-muted-foreground mt-2">
                    OpenAI voice used when provider is OpenAI or Auto. Click "Test" to hear a sample.
                  </p>
                </Field>
              )}
            </div>
          </div>

          {/* Live TTS audience block preview */}
          <div className="space-y-4">
            <PreviewBlock
              label="TTS audience block (live preview)"
              text={previewQ.data?.en}
              loading={previewQ.isFetching}
            />
            <p className="text-[11px] text-muted-foreground">
              Prepended to the TTS system prompt before every voice render.
              An empty block means TTS uses defaults. The AI always adapts
              the output language to the user's message language.
            </p>
          </div>
        </CardContent>
      </Card>

      <ReauthDialog
        open={reauthOpen}
        onOpenChange={setReauthOpen}
        title="Confirm reset"
        description="Resets identity and voice-audience fields to defaults. This cannot be undone."
        onConfirm={async () => {
          await resetMutation.mutateAsync();
        }}
      />
    </div>
  );
}

// ── Provider status panel (ADR-0185 M4) ─────────────────────────────────
//
// Shows REAL per-provider readiness (package importable, model file on
// disk, API key configured) — not just "package installed" — so a fresh
// install's "no STT provider available" failure is visible and actionable
// here instead of only surfacing as a raw error in the chat transcript.

const STT_LABELS: Record<string, string> = {
  local: "Local STT (pywhispercpp)",
  openai: "OpenAI Whisper",
};
const TTS_LABELS: Record<string, string> = {
  openai: "OpenAI TTS",
  edge: "edge-tts",
  piper: "Piper (offline)",
};

export function VoiceStatusPanel() {
  const statusQ = useQuery({
    queryKey: ["voice-status"],
    queryFn: ({ signal }) => getVoiceStatus(signal),
    refetchInterval: 30_000,
  });

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <CheckCircle2 className="h-4 w-4 text-accent" />
          <CardTitle className="text-base">Voice provider status</CardTitle>
          <HelpTooltip title="Voice provider status" side="right" width="md">
            Real-time readiness for speech-to-text and text-to-speech engines —
            whether the package is installed, the local model is downloaded, and
            (for cloud providers) an API key is configured.
          </HelpTooltip>
        </div>
        <CardDescription>
          What Corvin will actually use for voice input/output on this install.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {statusQ.isLoading && (
          <div className="space-y-2">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
          </div>
        )}
        {statusQ.isError && (
          <p className="text-sm text-destructive">
            Could not load provider status: {(statusQ.error as Error | undefined)?.message ?? "unknown"}
          </p>
        )}
        {statusQ.data && (
          <>
            <ProviderStatusGroup title="Speech-to-text" labels={STT_LABELS} rows={statusQ.data.stt} />
            <ProviderStatusGroup title="Text-to-speech" labels={TTS_LABELS} rows={statusQ.data.tts} />
          </>
        )}
      </CardContent>
    </Card>
  );
}

function ProviderStatusGroup({
  title,
  labels,
  rows,
}: {
  title: string;
  labels: Record<string, string>;
  rows: Record<string, VoiceProviderStatus>;
}) {
  const names = Object.keys(rows);
  if (names.length === 0) {
    return (
      <div>
        <h3 className="text-xs font-semibold text-muted-foreground mb-1.5">{title}</h3>
        <p className="text-xs text-muted-foreground">Status unavailable.</p>
      </div>
    );
  }
  return (
    <div>
      <h3 className="text-xs font-semibold text-muted-foreground mb-1.5">{title}</h3>
      <div className="space-y-1.5">
        {names.map((name) => (
          <ProviderStatusRow key={name} label={labels[name] ?? name} status={rows[name]} />
        ))}
      </div>
    </div>
  );
}

function ProviderStatusRow({
  label,
  status,
}: {
  label: string;
  status: VoiceProviderStatus;
}) {
  // The one obvious next action per ADR-0185 M4: "Add key" when a key is
  // missing, a concrete CLI hint when a model file hasn't been downloaded
  // yet (model downloads are handled by `corvin-install`, not a live
  // console action — no backend endpoint exists to trigger one on demand).
  const needsKey = status.key_configured === false;
  const needsModel = !status.ready && status.package_installed && status.model_present === false;

  return (
    <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border/60 bg-muted/20 px-3 py-2">
      <div className="flex items-center gap-2 min-w-0">
        {status.ready ? (
          <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-emerald-500" />
        ) : (
          <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-amber-500" />
        )}
        <span className="text-sm font-medium truncate">{label}</span>
        <Badge variant={status.ready ? "ok" : "warn"} className="text-[10px] shrink-0">
          {status.ready ? "ready" : "not configured"}
        </Badge>
      </div>
      <div className="flex items-center gap-2 min-w-0">
        <span className="text-xs text-muted-foreground truncate" title={status.detail}>
          {status.detail}
        </span>
        {needsKey && (
          <Button asChild variant="outline" size="sm" className="h-7 shrink-0 gap-1 text-xs">
            <Link to="/app/api-keys">
              <KeyRound className="h-3 w-3" /> Add key
            </Link>
          </Button>
        )}
        {needsModel && (
          <span className="text-[10px] text-muted-foreground font-mono shrink-0">
            run corvin-install
          </span>
        )}
      </div>
    </div>
  );
}

function Field({
  label,
  children,
}: {
  label: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <Label>{label}</Label>
      {children}
    </div>
  );
}

function PreviewBlock({
  label,
  text,
  loading,
}: {
  label: string;
  text: string | undefined;
  loading: boolean;
}) {
  return (
    <div className="rounded-md border border-border/60 bg-muted/30">
      <div className="flex items-center justify-between border-b border-border/60 px-3 py-2 text-xs">
        <span className="font-medium">{label}</span>
        {loading && <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" />}
      </div>
      <pre className="max-h-[14rem] overflow-y-auto whitespace-pre-wrap px-3 py-2 font-mono text-[11px] leading-relaxed text-muted-foreground">
        {text?.trim() ? text : "— empty (TTS uses defaults) —"}
      </pre>
    </div>
  );
}
