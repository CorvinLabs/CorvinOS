/**
 * API Keys page — BYOK (Bring-Your-Own-Key) management.
 *
 * ADR-0047: plaintext key values are encrypted in the browser with the
 * instance's RSA public key (Web Crypto API, RSA-OAEP-SHA256) before
 * they leave the client.  The management plane and this console layer
 * NEVER see plaintext values.
 *
 * Supports:
 * - Well-known system keys (anthropic_api_key, openai_api_key, …)
 * - Custom keys: custom_<slug> (slug: [a-z0-9_-], ≤32 chars)
 *   Injected as env-vars into forge tool sandboxes via meta.secrets.
 */
import * as React from "react";
import {
  AlertCircle,
  CheckCircle2,
  Eye,
  EyeOff,
  KeyRound,
  Loader2,
  Plus,
  RefreshCw,
  Terminal,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Label } from "@/components/ui/label";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";

// ── Types ─────────────────────────────────────────────────────────────────

interface PubkeyResponse {
  tenant_id: string;
  pubkey_pem: string;
  algorithm: string;
  key_size: number;
  ts: number;
}

interface SecretMeta {
  key_name: string;
  present: boolean | null;
  algorithm: string;
}

interface SecretsListResponse {
  tenant_id: string;
  agent_reachable: boolean;
  keys: SecretMeta[];
  ts: number;
}

interface RotateResult {
  key_name: string;
  last4?: string;
  rotated_at?: number;
}

// ── Web Crypto helpers ────────────────────────────────────────────────────

async function importRsaPublicKey(pemText: string): Promise<CryptoKey> {
  const lines = pemText.trim().split("\n");
  const base64 = lines.filter((l) => !l.startsWith("-----")).join("");
  const binary = Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
  return crypto.subtle.importKey(
    "spki",
    binary.buffer,
    { name: "RSA-OAEP", hash: "SHA-256" },
    false,
    ["encrypt"],
  );
}

async function encryptWithRsaOaep(pubkey: CryptoKey, plaintext: string): Promise<string> {
  const encoder = new TextEncoder();
  const data = encoder.encode(plaintext);
  const ciphertext = await crypto.subtle.encrypt({ name: "RSA-OAEP" }, pubkey, data);
  return btoa(String.fromCharCode(...new Uint8Array(ciphertext)));
}

async function postSecret(keyName: string, value: string, pubkey: CryptoKey, csrf: string): Promise<RotateResult> {
  const ciphertext = await encryptWithRsaOaep(pubkey, value.trim());
  return api<RotateResult>(`/byok/secrets/${keyName}`, {
    method: "POST",
    csrf,
    body: { ciphertext, algorithm: "RSA-OAEP-SHA256" },
  });
}

// ── Helpers ───────────────────────────────────────────────────────────────

function isValidCustomSlug(slug: string): boolean {
  return /^[a-z0-9_-]{1,32}$/.test(slug);
}

function toEnvVar(keyName: string): string {
  return keyName.toUpperCase().replace(/-/g, "_");
}

// ── Well-known key definitions ────────────────────────────────────────────

const KNOWN_KEYS: Array<{ name: string; label: string; hint: string }> = [
  {
    name: "anthropic_api_key",
    label: "Anthropic API Key",
    hint: "sk-ant-… — required for Claude models",
  },
  {
    name: "openai_api_key",
    label: "OpenAI API Key",
    hint: "sk-… — optional; required for OpenAI models and STT/TTS",
  },
  {
    name: "stt_openai_api_key",
    label: "OpenAI STT Key",
    hint: "sk-… — optional; overrides openai_api_key for speech-to-text only",
  },
  {
    name: "stt_local_whisper_api_key",
    label: "Local Whisper Key",
    hint: "Reserved for future local-provider authentication",
  },
  {
    name: "openrouter_api_key",
    label: "OpenRouter API Key",
    hint: "sk-or-… — lets engines that support the OpenRouter provider (Engines page) route through it",
  },
  {
    name: "ollama_api_key",
    label: "Ollama API Key",
    hint: "Only needed for Ollama Cloud — a local Ollama server needs no key, just Ollama running",
  },
];

// ── KeyCard — row used for both system and custom keys ────────────────────

interface KeyCardProps {
  keyName: string;
  label: string;
  hint?: string;
  present?: boolean | null;
  pubkey: CryptoKey | null;
  csrf: string;
  onRotated: () => void;
}

function KeyCard({ keyName, label, hint, present, pubkey, csrf, onRotated }: KeyCardProps) {
  const [value, setValue] = React.useState("");
  const [showValue, setShowValue] = React.useState(false);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [success, setSuccess] = React.useState<RotateResult | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!pubkey) { setError("Instance public key not loaded"); return; }
    if (!value.trim()) { setError("Please enter a value"); return; }
    setLoading(true); setError(null); setSuccess(null);
    try {
      const result = await postSecret(keyName, value, pubkey, csrf);
      setSuccess(result);
      onRotated();
      setValue("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  }

  return (
    <Card className="p-4">
      <form onSubmit={handleSubmit} className="space-y-3">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <KeyRound className="h-4 w-4 shrink-0 text-muted-foreground" />
              <span className="text-sm font-medium">{label}</span>
              <Badge variant="outline" className="font-mono text-[10px]">
                {keyName}
              </Badge>
              {present === true && !success && (
                <Badge variant="outline" className="text-[10px] text-emerald-600 border-emerald-300">
                  set
                </Badge>
              )}
              {present === false && (
                <Badge variant="outline" className="text-[10px] text-amber-600 border-amber-300">
                  not set
                </Badge>
              )}
            </div>
            {hint && <p className="mt-0.5 text-xs text-muted-foreground">{hint}</p>}
          </div>
          {success && (
            <div className="flex shrink-0 items-center gap-1 text-xs text-emerald-600 dark:text-emerald-400">
              <CheckCircle2 className="h-3.5 w-3.5" />
              <span>···{success.last4}</span>
            </div>
          )}
        </div>

        <div className="flex gap-2">
          <div className="relative flex-1">
            <Input
              type={showValue ? "text" : "password"}
              placeholder={`Paste ${label}…`}
              value={value}
              onChange={(e) => setValue(e.target.value)}
              autoComplete="new-password"
              autoCorrect="off"
              spellCheck={false}
              data-lpignore="true"
              data-1p-ignore
              data-bwignore
              className="pr-9 font-mono text-sm"
            />
            <button
              type="button"
              onClick={() => setShowValue((v) => !v)}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
              aria-label={showValue ? "Hide" : "Show"}
            >
              {showValue ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
            </button>
          </div>
          <Button type="submit" disabled={loading || !pubkey || !value.trim()} size="sm">
            {loading ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <RefreshCw className="h-4 w-4" />
            )}
            <span className="ml-1.5">{loading ? "Saving…" : present ? "Rotate" : "Save"}</span>
          </Button>
        </div>

        {error && (
          <div className="flex items-center gap-1.5 text-xs text-destructive">
            <AlertCircle className="h-3.5 w-3.5 shrink-0" />
            {error}
          </div>
        )}
        {success && !error && (
          <p className="text-xs text-emerald-600 dark:text-emerald-400">
            Saved — last 4: <span className="font-mono">···{success.last4}</span>
            {success.rotated_at && (
              <> · {new Date(success.rotated_at * 1000).toLocaleString()}</>
            )}
          </p>
        )}
      </form>
    </Card>
  );
}

// ── AddCustomKeyForm ──────────────────────────────────────────────────────

interface AddCustomKeyFormProps {
  pubkey: CryptoKey | null;
  csrf: string;
  onAdded: () => void;
}

function AddCustomKeyForm({ pubkey, csrf, onAdded }: AddCustomKeyFormProps) {
  const [slug, setSlug] = React.useState("");
  const [value, setValue] = React.useState("");
  const [showValue, setShowValue] = React.useState(false);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [success, setSuccess] = React.useState<RotateResult | null>(null);

  const trimmedSlug = slug.trim();
  const keyName = trimmedSlug ? `custom_${trimmedSlug}` : "";
  const slugValid = trimmedSlug ? isValidCustomSlug(trimmedSlug) : null;
  const envVar = keyName ? toEnvVar(keyName) : "";

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!pubkey) { setError("Instance public key not loaded"); return; }
    if (!slugValid) { setError("Name must be 1–32 chars: a–z, 0–9, _, -"); return; }
    if (!value.trim()) { setError("Please enter a value"); return; }
    setLoading(true); setError(null); setSuccess(null);
    try {
      const result = await postSecret(keyName, value, pubkey, csrf);
      setSuccess(result);
      onAdded();
      setSlug("");
      setValue("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  }

  return (
    <Card className="border-dashed p-4">
      <form onSubmit={handleSubmit} className="space-y-3">
        <p className="flex items-center gap-2 text-sm font-medium">
          <Plus className="h-4 w-4 text-muted-foreground" />
          Add custom key
        </p>

        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label className="text-xs">Key name</Label>
            <div className="flex overflow-hidden rounded-md border border-input focus-within:ring-1 focus-within:ring-ring">
              <span className="flex h-9 shrink-0 items-center border-r border-input bg-muted/50 px-2.5 text-sm text-muted-foreground">
                custom_
              </span>
              <Input
                value={slug}
                onChange={(e) =>
                  setSlug(e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, ""))
                }
                placeholder="stripe_key"
                maxLength={32}
                autoComplete="off"
                spellCheck={false}
                className="rounded-none border-0 font-mono text-sm shadow-none focus-visible:ring-0"
              />
            </div>
            {trimmedSlug && (
              <p
                className={`text-[11px] ${slugValid ? "text-emerald-600 dark:text-emerald-400" : "text-destructive"}`}
              >
                {slugValid ? `→ stored as ${keyName}` : "Use only: a–z 0–9 _ -"}
              </p>
            )}
          </div>

          <div className="space-y-1.5">
            <Label className="text-xs">Value</Label>
            <div className="relative">
              <Input
                type={showValue ? "text" : "password"}
                placeholder="Paste key value…"
                value={value}
                onChange={(e) => setValue(e.target.value)}
                autoComplete="new-password"
                autoCorrect="off"
                spellCheck={false}
                data-lpignore="true"
                data-1p-ignore
                data-bwignore
                className="pr-9 font-mono text-sm"
              />
              <button
                type="button"
                onClick={() => setShowValue((v) => !v)}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                aria-label={showValue ? "Hide" : "Show"}
              >
                {showValue ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </button>
            </div>
          </div>
        </div>

        {envVar && slugValid && (
          <div className="flex items-start gap-2 rounded-md bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
            <Terminal className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span>
              Use in forge tools:{" "}
              <code className="font-mono text-foreground/80">meta.secrets = ["{envVar}"]</code>
            </span>
          </div>
        )}

        <div className="flex items-center gap-3">
          <Button
            type="submit"
            size="sm"
            disabled={loading || !pubkey || !slugValid || !value.trim()}
          >
            {loading ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Plus className="h-4 w-4" />
            )}
            <span className="ml-1.5">{loading ? "Encrypting…" : "Add key"}</span>
          </Button>
          {success && (
            <span className="flex items-center gap-1 text-xs text-emerald-600 dark:text-emerald-400">
              <CheckCircle2 className="h-3.5 w-3.5" />
              Saved — <span className="font-mono">···{success.last4}</span>
            </span>
          )}
          {error && (
            <span className="flex items-center gap-1 text-xs text-destructive">
              <AlertCircle className="h-3.5 w-3.5 shrink-0" />
              {error}
            </span>
          )}
        </div>
      </form>
    </Card>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────

export function ApiKeysPage() {
  const { session } = useAuth();
  const csrf = session?.csrf_token ?? "";
  const [pubkey, setPubkey] = React.useState<CryptoKey | null>(null);
  const [pubkeyMeta, setPubkeyMeta] = React.useState<PubkeyResponse | null>(null);
  const [secretsMeta, setSecretsMeta] = React.useState<SecretsListResponse | null>(null);
  const [loadError, setLoadError] = React.useState<string | null>(null);
  const [loading, setLoading] = React.useState(true);

  async function loadData(importKey = true) {
    if (importKey) setLoading(true);
    setLoadError(null);
    try {
      const [pkResp, listResp] = await Promise.all([
        api<PubkeyResponse>("/byok/pubkey"),
        api<SecretsListResponse>("/byok/secrets"),
      ]);
      if (importKey) {
        const key = await importRsaPublicKey(pkResp.pubkey_pem);
        setPubkey(key);
        setPubkeyMeta(pkResp);
      }
      setSecretsMeta(listResp);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "Failed to load instance public key");
    } finally {
      if (importKey) setLoading(false);
    }
  }

  React.useEffect(() => { loadData(); }, []);

  function handleRotated() {
    // Refresh present-state badges without re-importing the RSA key
    api<SecretsListResponse>("/byok/secrets")
      .then(setSecretsMeta)
      .catch(() => {});
  }

  const knownPresent = (name: string) =>
    secretsMeta?.keys.find((k) => k.key_name === name)?.present ?? null;

  const customKeys = (secretsMeta?.keys ?? []).filter((k) =>
    k.key_name.startsWith("custom_"),
  );

  // True on a fresh install: no system or custom key has been stored yet.
  const anyKeyStored = (secretsMeta?.keys ?? []).some((k) => k.present === true);

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading instance public key…
      </div>
    );
  }

  return (
    <div className="max-w-2xl space-y-8">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">API Keys</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          BYOK — keys are encrypted in your browser before leaving the page. The server
          stores ciphertext only; plaintext values never leave your instance.
        </p>
      </div>

      {loadError && (
        <div className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/5 p-4 text-sm text-destructive">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
          <div>
            <p className="font-medium">Could not reach the Instance Agent</p>
            <p className="mt-0.5 text-xs opacity-80">{loadError}</p>
            <p className="mt-1 text-xs opacity-60">
              Ensure the agent is running (CORVIN_AGENT_PORT 8766) and accessible.
            </p>
          </div>
        </div>
      )}

      {pubkeyMeta && (
        <div className="flex items-center gap-3 rounded-md bg-muted/50 px-4 py-2.5 text-xs text-muted-foreground">
          <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-emerald-500" />
          <span>
            Instance public key loaded ·{" "}
            <span className="font-mono">{pubkeyMeta.algorithm}</span> ·{" "}
            {pubkeyMeta.key_size}-bit · tenant:{" "}
            <span className="font-mono">{pubkeyMeta.tenant_id}</span>
          </span>
          {secretsMeta && !secretsMeta.agent_reachable && (
            <Badge
              variant="outline"
              className="ml-auto text-[10px] text-amber-600 border-amber-300"
            >
              agent offline
            </Badge>
          )}
        </div>
      )}

      {!loadError && secretsMeta && !anyKeyStored && (
        <div className="rounded-md border border-dashed border-border bg-muted/20 px-4 py-3 text-sm text-muted-foreground">
          No API keys stored yet. Add a provider key (OpenAI, Anthropic, …) below so engines and
          tools can authenticate. Keys are stored in the encrypted vault.
        </div>
      )}

      {/* System keys */}
      <div className="space-y-3">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          System keys
        </h2>
        {KNOWN_KEYS.map((kd) => (
          <KeyCard
            key={kd.name}
            keyName={kd.name}
            label={kd.label}
            hint={kd.hint}
            present={knownPresent(kd.name)}
            pubkey={pubkey}
            csrf={csrf}
            onRotated={handleRotated}
          />
        ))}
      </div>

      {/* Custom keys */}
      <div className="space-y-3">
        <div>
          <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            Custom keys
          </h2>
          <p className="mt-1 text-xs text-muted-foreground">
            Add any external API key — MCP server tokens, third-party services, etc. Custom
            keys are injected as environment variables into forge tool sandboxes when the
            tool declares them in <code className="font-mono">meta.secrets</code>.
          </p>
        </div>

        {customKeys.length > 0 && (
          <div className="space-y-2">
            {customKeys.map((km) => (
              <KeyCard
                key={km.key_name}
                keyName={km.key_name}
                label={km.key_name}
                hint={`Inject in tools via: meta.secrets = ["${toEnvVar(km.key_name)}"]`}
                present={km.present}
                pubkey={pubkey}
                csrf={csrf}
                onRotated={handleRotated}
              />
            ))}
          </div>
        )}

        <AddCustomKeyForm pubkey={pubkey} csrf={csrf} onAdded={handleRotated} />
      </div>

      {/* Security note */}
      <div className="space-y-1 rounded-md border border-border/50 bg-muted/20 px-4 py-3 text-xs text-muted-foreground">
        <p className="font-medium text-foreground/70">Security note</p>
        <p>
          Encryption uses the Web Crypto API (<code>SubtleCrypto.encrypt</code>, RSA-OAEP +
          SHA-256). Your key value is encrypted before leaving the browser — the console
          server and management plane only ever see ciphertext.
        </p>
        <p>
          The RSA private key lives exclusively inside your instance container and is never
          transmitted anywhere.
        </p>
      </div>
    </div>
  );
}
