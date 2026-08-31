/**
 * Data Sources page — ADR-0106 DSI v1.
 *
 * Two sections:
 *   1. Database Connections — real SQL/cloud/file/streaming sources via a
 *      guided wizard. Credentials are encrypted in-browser (RSA-OAEP-SHA256)
 *      and stored in the vault; plaintext never appears in manifests or logs.
 *   2. HTTP Data Bridges — custom HTTP servers implementing the /ping /schema
 *      /query protocol (existing feature, unchanged).
 */
import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft, CheckCircle2, ChevronRight, Database,
  Globe, HardDrive, Info, Loader2, Lock, Plus, RefreshCw,
  ShieldCheck, Trash2, XCircle, Zap, Eye, EyeOff,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useAuth } from "@/lib/auth";
import {
  getDataSource,
  getDataSourceAudit,
  getLicenseInfo,
  listDataSources,
  registerDataSource,
  testDataSource,
  unregisterDataSource,
  type DSIAuditEvent,
  type DSIConnection,
  type DSIPingResult,
  type LicenseInfo,
  listHttpAdapters,
  registerHttpAdapter,
  removeHttpAdapter,
  pingHttpAdapter,
  type HttpAdapter,
} from "@/lib/api";
import { isAdapterAllowed } from "@/components/license-gate";

// ── BYOK encryption (RSA-OAEP-SHA256, same pattern as api-keys page) ─────

async function importRsaPublicKey(pem: string): Promise<CryptoKey> {
  const b64 = pem.replace(/-----[^-]+-----/g, "").replace(/\s/g, "");
  const binary = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
  return crypto.subtle.importKey(
    "spki",
    binary.buffer,
    { name: "RSA-OAEP", hash: "SHA-256" },
    false,
    ["encrypt"],
  );
}

async function encryptSecret(pubkey: CryptoKey, plaintext: string): Promise<string> {
  const data = new TextEncoder().encode(plaintext);
  const ct = await crypto.subtle.encrypt({ name: "RSA-OAEP" }, pubkey, data);
  return btoa(String.fromCharCode(...new Uint8Array(ct)));
}

async function storeVaultSecret(
  keyName: string,
  value: string,
  pubkey: CryptoKey,
  csrf: string,
): Promise<void> {
  const ciphertext = await encryptSecret(pubkey, value.trim());
  const resp = await fetch(`/v1/console/byok/secrets/${keyName}`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
    body: JSON.stringify({ ciphertext, algorithm: "RSA-OAEP-SHA256" }),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error((err as { detail?: string }).detail ?? `Could not store ${keyName} in vault`);
  }
}

// ── Shared helpers ────────────────────────────────────────────────────────

const CLASSIFICATION_COLORS: Record<string, string> = {
  PUBLIC:       "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-300",
  INTERNAL:     "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300",
  CONFIDENTIAL: "bg-orange-100 text-orange-800 dark:bg-orange-900/30 dark:text-orange-300",
  SECRET:       "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-300",
};

function ClassificationBadge({ value }: { value: string }) {
  return (
    <span className={`inline-flex items-center rounded px-2 py-0.5 text-xs font-medium ${CLASSIFICATION_COLORS[value] ?? "bg-muted text-muted-foreground"}`}>
      {value}
    </span>
  );
}

function LocalityIcon({ locality }: { locality: string }) {
  if (locality === "local") return <HardDrive className="h-3.5 w-3.5 text-muted-foreground" />;
  return <Globe className="h-3.5 w-3.5 text-muted-foreground" />;
}

function formatTs(ts: number | null): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString();
}

// ── Database type catalog ─────────────────────────────────────────────────

type FieldType = "text" | "number" | "select";

interface WizardField {
  key: string;
  label: string;
  placeholder?: string;
  type?: FieldType;
  options?: string[];
  defaultValue?: string;
  required?: boolean;
  hint?: string;
  half?: boolean;
}

interface CredentialField {
  envVar: string;
  label: string;
  placeholder?: string;
  isPassword?: boolean;
  hint?: string;
  optional?: boolean;
}

interface DbTypeConfig {
  id: string;
  name: string;
  category: string;
  description: string;
  icon: string;
  defaultLocality: "local" | "any";
  configFields: WizardField[];
  credentials: CredentialField[];
  setupHint?: string;
}

const DB_CATALOG: DbTypeConfig[] = [
  {
    id: "postgresql",
    name: "PostgreSQL",
    category: "SQL Database",
    description: "Relational database with advanced SQL support",
    icon: "🐘",
    defaultLocality: "any",
    configFields: [
      { key: "host",     label: "Host",     placeholder: "localhost", required: true, half: true },
      { key: "port",     label: "Port",     placeholder: "5432",      type: "number", defaultValue: "5432", half: true },
      { key: "database", label: "Database", placeholder: "mydb",      required: true, half: true },
      { key: "schema",   label: "Schema",   placeholder: "public",    defaultValue: "public", half: true },
      { key: "ssl_mode", label: "SSL mode", type: "select",
        options: ["disable", "allow", "prefer", "require", "verify-full"], defaultValue: "require" },
    ],
    credentials: [
      { envVar: "PGUSER",     label: "Username", placeholder: "postgres" },
      { envVar: "PGPASSWORD", label: "Password", placeholder: "••••••", isPassword: true },
    ],
    setupHint: "Credentials are encrypted in your browser and stored in the vault — they never appear in the connection manifest or logs.",
  },
  {
    id: "mysql",
    name: "MySQL / MariaDB",
    category: "SQL Database",
    description: "MySQL and MariaDB relational databases",
    icon: "🐬",
    defaultLocality: "any",
    configFields: [
      { key: "host",     label: "Host",     placeholder: "localhost", required: true, half: true },
      { key: "port",     label: "Port",     placeholder: "3306",      type: "number", defaultValue: "3306", half: true },
      { key: "database", label: "Database", placeholder: "mydb",      required: true },
      { key: "ssl",      label: "Use SSL",  type: "select", options: ["true", "false"], defaultValue: "true" },
    ],
    credentials: [
      { envVar: "MYSQL_USER",     label: "Username", placeholder: "root" },
      { envVar: "MYSQL_PASSWORD", label: "Password", placeholder: "••••••", isPassword: true },
    ],
  },
  {
    id: "local_file",
    name: "Local File",
    category: "File",
    description: "CSV or Parquet files on the local filesystem — no cloud egress",
    icon: "📂",
    defaultLocality: "local",
    configFields: [
      { key: "path",   label: "File or directory path", placeholder: "/home/user/data/export.csv", required: true,
        hint: "Absolute path to a .csv or .parquet file, or a directory of files." },
      { key: "format", label: "Format", type: "select", options: ["auto", "csv", "parquet"], defaultValue: "auto" },
    ],
    credentials: [],
    setupHint: "No credentials needed. The file must be readable by the CorvinOS process.",
  },
  {
    id: "s3_csv",
    name: "Amazon S3 (CSV)",
    category: "Cloud Storage",
    description: "CSV files stored in an Amazon S3 bucket",
    icon: "☁️",
    defaultLocality: "any",
    configFields: [
      { key: "bucket", label: "Bucket",     placeholder: "my-data-bucket", required: true, half: true },
      { key: "prefix", label: "Key prefix", placeholder: "data/2024/",                     half: true },
      { key: "region", label: "AWS Region", placeholder: "eu-west-1", defaultValue: "eu-west-1" },
    ],
    credentials: [
      { envVar: "AWS_ACCESS_KEY_ID",     label: "Access Key ID",     placeholder: "AKIA…" },
      { envVar: "AWS_SECRET_ACCESS_KEY", label: "Secret Access Key", placeholder: "••••••", isPassword: true },
    ],
  },
  {
    id: "s3_parquet",
    name: "Amazon S3 (Parquet)",
    category: "Cloud Storage",
    description: "Parquet files stored in an Amazon S3 bucket",
    icon: "☁️",
    defaultLocality: "any",
    configFields: [
      { key: "bucket", label: "Bucket",     placeholder: "my-data-bucket",  required: true, half: true },
      { key: "prefix", label: "Key prefix", placeholder: "data/year=2024/",                 half: true },
      { key: "region", label: "AWS Region", placeholder: "eu-west-1", defaultValue: "eu-west-1" },
    ],
    credentials: [
      { envVar: "AWS_ACCESS_KEY_ID",     label: "Access Key ID",     placeholder: "AKIA…" },
      { envVar: "AWS_SECRET_ACCESS_KEY", label: "Secret Access Key", placeholder: "••••••", isPassword: true },
    ],
  },
  {
    id: "bigquery",
    name: "Google BigQuery",
    category: "Cloud Storage",
    description: "Google's serverless, highly scalable data warehouse",
    icon: "🔷",
    defaultLocality: "any",
    configFields: [
      { key: "project",  label: "GCP Project ID", placeholder: "my-project-12345", required: true, half: true },
      { key: "dataset",  label: "Dataset",         placeholder: "my_dataset",                       half: true },
      { key: "location", label: "Location",         placeholder: "EU", defaultValue: "EU" },
    ],
    credentials: [
      { envVar: "GOOGLE_APPLICATION_CREDENTIALS",
        label: "Service account JSON path",
        placeholder: "/home/user/.config/gcp-sa.json",
        hint: "Upload the key file to CorvinOS Files first, then enter the absolute path here." },
    ],
    setupHint: "Upload your GCP service account key JSON to Files (sidebar → Files) before registering.",
  },
  {
    id: "snowflake",
    name: "Snowflake",
    category: "Cloud Storage",
    description: "Snowflake cloud data platform",
    icon: "❄️",
    defaultLocality: "any",
    configFields: [
      { key: "account",   label: "Account identifier", placeholder: "xy12345.eu-west-1", required: true },
      { key: "warehouse", label: "Warehouse",           placeholder: "COMPUTE_WH",        half: true },
      { key: "database",  label: "Database",            placeholder: "MYDB",              half: true },
      { key: "schema",    label: "Schema",              placeholder: "PUBLIC", defaultValue: "PUBLIC" },
    ],
    credentials: [
      { envVar: "SNOWFLAKE_USER",     label: "Username", placeholder: "myuser" },
      { envVar: "SNOWFLAKE_PASSWORD", label: "Password", placeholder: "••••••", isPassword: true },
    ],
  },
  {
    id: "redshift",
    name: "Amazon Redshift",
    category: "Cloud Storage",
    description: "Redshift analytics database — PostgreSQL-compatible",
    icon: "🔴",
    defaultLocality: "any",
    configFields: [
      { key: "host",     label: "Cluster endpoint",
        placeholder: "cluster.abc.eu-west-1.redshift.amazonaws.com", required: true },
      { key: "port",     label: "Port",     placeholder: "5439", type: "number", defaultValue: "5439", half: true },
      { key: "database", label: "Database", placeholder: "dev",                                        half: true },
      { key: "schema",   label: "Schema",   placeholder: "public", defaultValue: "public" },
    ],
    credentials: [
      { envVar: "PGUSER",     label: "Username", placeholder: "awsuser" },
      { envVar: "PGPASSWORD", label: "Password", placeholder: "••••••", isPassword: true },
    ],
  },
  {
    id: "gcs_parquet",
    name: "Google Cloud Storage",
    category: "Cloud Storage",
    description: "Parquet files on Google Cloud Storage",
    icon: "🔵",
    defaultLocality: "any",
    configFields: [
      { key: "bucket", label: "Bucket", placeholder: "my-bucket", required: true, half: true },
      { key: "prefix", label: "Prefix", placeholder: "data/",                      half: true },
    ],
    credentials: [
      { envVar: "GOOGLE_APPLICATION_CREDENTIALS",
        label: "Service account JSON path", placeholder: "/home/user/.config/gcp-sa.json" },
    ],
  },
  {
    id: "azure_blob",
    name: "Azure Blob Storage",
    category: "Cloud Storage",
    description: "Files on Azure Blob Storage",
    icon: "🟦",
    defaultLocality: "any",
    configFields: [
      { key: "container", label: "Container",   placeholder: "my-container", required: true, half: true },
      { key: "prefix",    label: "Blob prefix", placeholder: "data/",                        half: true },
    ],
    credentials: [
      { envVar: "AZURE_STORAGE_ACCOUNT", label: "Storage account name", placeholder: "mystorageaccount" },
      { envVar: "AZURE_STORAGE_KEY",     label: "Storage account key",  placeholder: "••••••", isPassword: true },
    ],
  },
  {
    id: "delta_lake",
    name: "Delta Lake",
    category: "Analytics",
    description: "Delta Lake tables on S3 or local storage",
    icon: "📊",
    defaultLocality: "any",
    configFields: [
      { key: "path", label: "Table path (S3 URI or local path)",
        placeholder: "s3://my-bucket/delta-table/  or  /local/path", required: true,
        hint: "For S3 tables fill in AWS credentials below. For local paths, leave credentials empty." },
    ],
    credentials: [
      { envVar: "AWS_ACCESS_KEY_ID",
        label: "Access Key ID (for S3)", placeholder: "AKIA… (leave blank for local)", optional: true },
      { envVar: "AWS_SECRET_ACCESS_KEY",
        label: "Secret Access Key (for S3)", placeholder: "••••••", isPassword: true, optional: true },
    ],
  },
  {
    id: "kafka_batch",
    name: "Apache Kafka (batch)",
    category: "Streaming",
    description: "Consume Kafka topics in batch mode for compute analysis",
    icon: "📡",
    defaultLocality: "any",
    configFields: [
      { key: "bootstrap_servers", label: "Bootstrap servers",    placeholder: "kafka:9092,kafka2:9092", required: true },
      { key: "topic",             label: "Topic",                placeholder: "my.events.v1",          required: true, half: true },
      { key: "group_id",          label: "Consumer group",       placeholder: "corvin-compute",        half: true },
      { key: "max_messages",      label: "Max messages / batch", placeholder: "10000", type: "number", defaultValue: "10000" },
    ],
    credentials: [
      { envVar: "KAFKA_SASL_USERNAME", label: "SASL username (if auth enabled)", placeholder: "myuser", optional: true },
      { envVar: "KAFKA_SASL_PASSWORD", label: "SASL password", placeholder: "••••••", isPassword: true, optional: true },
    ],
  },
  {
    id: "http_rest",
    name: "REST API / HTTP",
    category: "API",
    description: "Any HTTP endpoint returning JSON, CSV, or NDJSON data",
    icon: "🌐",
    defaultLocality: "any",
    configFields: [
      { key: "base_url",        label: "Base URL",        placeholder: "https://api.example.com/v1", required: true },
      { key: "endpoint",        label: "Endpoint path",   placeholder: "/records",                   half: true },
      { key: "method",          label: "Method",          type: "select", options: ["GET", "POST"],  defaultValue: "GET", half: true },
      { key: "response_format", label: "Response format", type: "select",
        options: ["json", "csv", "ndjson"], defaultValue: "json" },
    ],
    credentials: [
      { envVar: "HTTP_API_KEY", label: "API key / Bearer token", placeholder: "••••••",
        isPassword: true, optional: true, hint: "Sent as: Authorization: Bearer <value>" },
    ],
  },
];

const CATEGORIES = [...new Set(DB_CATALOG.map((d) => d.category))];

// ── Step 1: Type picker ───────────────────────────────────────────────────

function TypePicker({
  onSelect,
  licenseInfo,
}: {
  onSelect: (db: DbTypeConfig) => void;
  licenseInfo: LicenseInfo | undefined;
}) {
  return (
    <div className="space-y-5">
      {CATEGORIES.map((cat) => (
        <div key={cat}>
          <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-2">
            {cat}
          </p>
          <div className="grid grid-cols-2 gap-2">
            {DB_CATALOG.filter((d) => d.category === cat).map((db) => {
              const allowed = isAdapterAllowed(licenseInfo, db.id);
              if (allowed) {
                return (
                  <button
                    key={db.id}
                    data-testid={`db-type-${db.id}`}
                    className="flex w-full items-center gap-3 rounded-lg border px-3 py-2.5 text-left hover:bg-accent transition-colors"
                    onClick={() => onSelect(db)}
                    type="button"
                  >
                    <span className="text-xl shrink-0">{db.icon}</span>
                    <div className="min-w-0">
                      <div className="text-sm font-medium leading-none">{db.name}</div>
                      <div className="text-xs text-muted-foreground mt-0.5 line-clamp-1">{db.description}</div>
                    </div>
                    {db.defaultLocality === "local" && (
                      <Badge variant="outline" className="ml-auto shrink-0 text-xs">local</Badge>
                    )}
                  </button>
                );
              }
              // Locked: show tile content normally, add a small pricing link — no centre overlay
              return (
                <div
                  key={db.id}
                  data-testid={`db-type-locked-${db.id}`}
                  className="flex w-full flex-col rounded-lg border px-3 py-2.5 opacity-60 cursor-not-allowed"
                >
                  <div className="flex items-center gap-3">
                    <span className="text-xl shrink-0">{db.icon}</span>
                    <div className="min-w-0 flex-1">
                      <div className="text-sm font-medium leading-none" data-testid={`db-type-${db.id}`}>{db.name}</div>
                      <div className="text-xs text-muted-foreground mt-0.5 line-clamp-1">{db.description}</div>
                    </div>
                  </div>
                  <a
                    href="https://corvin-labs.com/pricing"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="mt-1.5 flex items-center gap-1 text-[11px] text-accent hover:underline self-start pointer-events-auto"
                    onClick={(e) => e.stopPropagation()}
                  >
                    <Lock className="h-2.5 w-2.5" />
                    Member plan required
                  </a>
                </div>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}

// ── Step 2: Connection form ───────────────────────────────────────────────

interface ConnectFormProps {
  db: DbTypeConfig;
  csrf: string;
  onBack: () => void;
  onSuccess: () => void;
}

function ConnectForm({ db, csrf, onBack, onSuccess }: ConnectFormProps) {
  const qc = useQueryClient();

  const [pubkey, setPubkey] = React.useState<CryptoKey | null>(null);
  const [pubkeyError, setPubkeyError] = React.useState<string | null>(null);
  React.useEffect(() => {
    fetch("/v1/console/byok/pubkey", { credentials: "include" })
      .then((r) => r.json())
      .then(async (d: { pubkey_pem: string }) => setPubkey(await importRsaPublicKey(d.pubkey_pem)))
      .catch(() =>
        setPubkeyError("Could not load instance public key — credentials cannot be stored securely.")
      );
  }, []);

  const initConfig = () =>
    Object.fromEntries(db.configFields.map((f) => [f.key, f.defaultValue ?? ""]));

  const [config, setConfig] = React.useState<Record<string, string>>(initConfig);
  const [creds, setCreds] = React.useState<Record<string, string>>({});
  const [showPw, setShowPw] = React.useState<Record<string, boolean>>({});
  const [name, setName] = React.useState("");
  const [classification, setClassification] = React.useState("INTERNAL");
  const [residency, setResidency] = React.useState(
    db.defaultLocality === "local" ? "local" : "any",
  );
  const [description, setDescription] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name) return;
    setError(null);
    setBusy(true);

    try {
      // 1. Encrypt and store each credential in the vault
      const secretNames: string[] = [];
      for (const cred of db.credentials) {
        const val = (creds[cred.envVar] ?? "").trim();
        if (!val && !cred.optional) {
          setError(`${cred.label} is required.`);
          setBusy(false);
          return;
        }
        if (val) {
          if (!pubkey) {
            setError("Instance public key unavailable — cannot encrypt credentials.");
            setBusy(false);
            return;
          }
          await storeVaultSecret(cred.envVar, val, pubkey, csrf);
          secretNames.push(cred.envVar);
        }
      }

      // 2. Build the DSI v1 manifest (no plaintext credentials)
      const cleanConfig: Record<string, string | number | boolean> = {};
      for (const [k, v] of Object.entries(config)) {
        if (v === "") continue;
        const fd = db.configFields.find((f) => f.key === k);
        if (fd?.type === "number" && v) cleanConfig[k] = parseInt(v, 10);
        else if (v === "true") cleanConfig[k] = true;
        else if (v === "false") cleanConfig[k] = false;
        else cleanConfig[k] = v;
      }

      await registerDataSource(
        {
          dsi_version: "1",
          name,
          adapter: db.id,
          config: cleanConfig,
          data_classification: classification,
          data_residency: residency,
          secrets: secretNames,
          description,
          read_only: true,
        },
        csrf,
      );
      qc.invalidateQueries({ queryKey: ["data-sources"] });
      onSuccess();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-5">
      {/* Back link */}
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={onBack}
          className="text-muted-foreground hover:text-foreground flex items-center gap-1 text-sm"
        >
          <ArrowLeft className="h-3.5 w-3.5" /> Back
        </button>
        <span className="text-muted-foreground text-sm">·</span>
        <span className="text-lg">{db.icon}</span>
        <span className="font-semibold text-sm">{db.name}</span>
      </div>

      {/* Connection name */}
      <div>
        <label className="text-xs font-medium mb-1 block">Connection name *</label>
        <Input
          data-testid="conn-name-input"
          placeholder="e.g. prod-analytics (lowercase, hyphens ok)"
          value={name}
          onChange={(e) => setName(e.target.value)}
          pattern="[a-z][a-z0-9_-]{0,63}"
          title="Lowercase letters, digits, hyphens, underscores. Must start with a letter."
          required
        />
      </div>

      {/* Connection-specific fields */}
      {db.configFields.length > 0 && (
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-2">
            Connection
          </p>
          <div className="grid grid-cols-2 gap-3">
            {db.configFields.map((f) => (
              <div key={f.key} className={f.half ? "" : "col-span-2"}>
                <label className="text-xs mb-0.5 block">
                  {f.label}{f.required ? " *" : ""}
                </label>
                {f.type === "select" ? (
                  <select
                    data-testid={`config-${f.key}`}
                    className="w-full rounded-md border bg-background px-3 py-2 text-sm"
                    value={config[f.key] ?? f.defaultValue ?? ""}
                    onChange={(e) => setConfig((p) => ({ ...p, [f.key]: e.target.value }))}
                  >
                    {(f.options ?? []).map((o) => <option key={o} value={o}>{o}</option>)}
                  </select>
                ) : (
                  <Input
                    data-testid={`config-${f.key}`}
                    type={f.type === "number" ? "number" : "text"}
                    placeholder={f.placeholder}
                    value={config[f.key] ?? ""}
                    onChange={(e) => setConfig((p) => ({ ...p, [f.key]: e.target.value }))}
                    required={f.required}
                  />
                )}
                {f.hint && <p className="text-xs text-muted-foreground mt-0.5">{f.hint}</p>}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Credentials */}
      {db.credentials.length > 0 && (
        <div>
          <div className="flex items-center gap-2 mb-2">
            <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Credentials
            </p>
            <span className="flex items-center gap-1 text-xs text-muted-foreground">
              <Lock className="h-3 w-3" />
              encrypted in browser · stored in vault
            </span>
          </div>

          {pubkeyError && (
            <p className="text-xs text-destructive mb-2 flex items-center gap-1">
              <XCircle className="h-3.5 w-3.5" /> {pubkeyError}
            </p>
          )}

          <div className="space-y-2.5 rounded-lg border bg-muted/30 px-3 py-3">
            {db.credentials.map((cred) => (
              <div key={cred.envVar}>
                <div className="flex items-baseline justify-between mb-0.5">
                  <label className="text-xs">
                    {cred.label}{cred.optional ? "" : " *"}
                  </label>
                  <span className="text-xs font-mono text-muted-foreground">
                    vault:{cred.envVar}
                  </span>
                </div>
                <div className="relative">
                  <Input
                    data-testid={`cred-${cred.envVar}`}
                    type={cred.isPassword && !showPw[cred.envVar] ? "password" : "text"}
                    placeholder={cred.placeholder}
                    value={creds[cred.envVar] ?? ""}
                    onChange={(e) => setCreds((p) => ({ ...p, [cred.envVar]: e.target.value }))}
                    required={!cred.optional}
                    className="pr-8"
                  />
                  {cred.isPassword && (
                    <button
                      type="button"
                      className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                      onClick={() => setShowPw((p) => ({ ...p, [cred.envVar]: !p[cred.envVar] }))}
                    >
                      {showPw[cred.envVar]
                        ? <EyeOff className="h-3.5 w-3.5" />
                        : <Eye className="h-3.5 w-3.5" />}
                    </button>
                  )}
                </div>
                {cred.hint && (
                  <p className="text-xs text-muted-foreground mt-0.5">{cred.hint}</p>
                )}
              </div>
            ))}
          </div>

          {db.setupHint && (
            <div className="mt-2 flex items-start gap-1.5 text-xs text-muted-foreground rounded border bg-muted/20 px-3 py-2">
              <ShieldCheck className="h-3.5 w-3.5 shrink-0 mt-0.5 text-green-500" />
              {db.setupHint}
            </div>
          )}
        </div>
      )}

      {/* Classification + residency */}
      <div>
        <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-2">
          Classification
        </p>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="text-xs mb-0.5 block">Data classification *</label>
            <select
              className="w-full rounded-md border bg-background px-3 py-2 text-sm"
              value={classification}
              onChange={(e) => setClassification(e.target.value)}
            >
              {["PUBLIC", "INTERNAL", "CONFIDENTIAL", "SECRET"].map((c) => (
                <option key={c} value={c}>{c}</option>
              ))}
            </select>
            <p className="text-xs text-muted-foreground mt-0.5">
              {classification === "CONFIDENTIAL" || classification === "SECRET"
                ? "Only local engines (Hermes) may access this data."
                : classification === "INTERNAL"
                ? "EU cloud engines permitted."
                : "All engines permitted."}
            </p>
          </div>
          <div>
            <label className="text-xs mb-0.5 block">Data residency</label>
            <select
              className="w-full rounded-md border bg-background px-3 py-2 text-sm"
              value={residency}
              onChange={(e) => setResidency(e.target.value)}
            >
              {["any", "eu", "de", "us", "local"].map((r) => (
                <option key={r} value={r}>{r}</option>
              ))}
            </select>
          </div>
        </div>
      </div>

      {/* Description */}
      <div>
        <label className="text-xs font-medium mb-1 block">Description (optional)</label>
        <Input
          placeholder="One-line description shown in the console"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
      </div>

      {error && (
        <p className="text-sm text-destructive rounded border border-destructive/30 bg-destructive/10 px-3 py-2">
          {error}
        </p>
      )}

      <div className="flex justify-end gap-2 pt-1">
        <Button type="button" variant="outline" onClick={onBack}>Cancel</Button>
        <Button type="submit" data-testid="conn-submit-btn" disabled={busy || !name}>
          {busy && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
          {db.credentials.some((c) => !c.optional && (creds[c.envVar] ?? "").trim())
            ? "Encrypt & Connect"
            : "Connect"}
        </Button>
      </div>
    </form>
  );
}

// ── Connect wizard dialog ─────────────────────────────────────────────────

function ConnectWizard({
  open,
  onClose,
  csrf,
  licenseInfo,
}: {
  open: boolean;
  onClose: () => void;
  csrf: string;
  licenseInfo: LicenseInfo | undefined;
}) {
  const [selected, setSelected] = React.useState<DbTypeConfig | null>(null);

  function handleClose() {
    setSelected(null);
    onClose();
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !v && handleClose()}>
      <DialogContent className="max-w-2xl max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>
            {selected ? `Connect — ${selected.name}` : "Connect a Database"}
          </DialogTitle>
          <DialogDescription>
            {selected
              ? "Fill in the connection details. Credentials are encrypted before leaving your browser."
              : "Choose the type of database or data source to connect to the compute layer."}
          </DialogDescription>
        </DialogHeader>

        {selected ? (
          <ConnectForm
            db={selected}
            csrf={csrf}
            onBack={() => setSelected(null)}
            onSuccess={handleClose}
          />
        ) : (
          <TypePicker onSelect={setSelected} licenseInfo={licenseInfo} />
        )}
      </DialogContent>
    </Dialog>
  );
}

// ── Detail Dialog ─────────────────────────────────────────────────────────

function DetailDialog({
  name,
  csrf,
  open,
  onClose,
}: {
  name: string | null;
  csrf: string;
  open: boolean;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const { data: conn, isLoading } = useQuery<DSIConnection>({
    queryKey: ["data-source", name],
    queryFn: ({ signal }) => getDataSource(name!, signal),
    enabled: open && !!name,
  });
  const { data: auditEvents = [] } = useQuery<DSIAuditEvent[]>({
    queryKey: ["data-source-audit", name],
    queryFn: ({ signal }) => getDataSourceAudit(name!, 20, signal),
    enabled: open && !!name,
  });

  const testMut = useMutation<DSIPingResult>({
    mutationFn: () => testDataSource(name!, csrf),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["data-source", name] }),
  });

  const dbType = conn ? DB_CATALOG.find((d) => d.id === conn.adapter) : null;

  if (!name) return null;

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <span className="text-lg">{dbType?.icon ?? "🗄️"}</span>
            {name}
          </DialogTitle>
          {conn && (
            <DialogDescription>
              {dbType?.name ?? conn.adapter}
              {conn.description ? ` — ${conn.description}` : ""}
            </DialogDescription>
          )}
        </DialogHeader>

        {isLoading && (
          <div className="space-y-2">
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-3/4" />
          </div>
        )}

        {conn && (
          <div className="space-y-4">
            <div className="flex flex-wrap gap-2">
              <ClassificationBadge value={conn.data_classification} />
              {conn.data_residency && conn.data_residency !== "any" && (
                <Badge variant="outline" className="text-xs">
                  <Globe className="mr-1 h-3 w-3" />{conn.data_residency}
                </Badge>
              )}
              {(conn.tags ?? []).map((t) => (
                <Badge key={t} variant="secondary">{t}</Badge>
              ))}
            </div>

            {/* Connectivity test */}
            <div>
              <Button
                size="sm"
                variant="outline"
                onClick={() => testMut.mutate()}
                disabled={testMut.isPending}
              >
                {testMut.isPending
                  ? <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
                  : <Zap className="mr-2 h-3.5 w-3.5" />}
                Test connectivity
              </Button>

              {testMut.data && (
                <div className="mt-2 flex items-center gap-2 text-sm">
                  {testMut.data.ok
                    ? <CheckCircle2 className="h-4 w-4 text-green-500" />
                    : <XCircle className="h-4 w-4 text-destructive" />}
                  <span>
                    {testMut.data.ok ? "Connected" : "Failed"}
                    {testMut.data.latency_ms > 0 && ` · ${testMut.data.latency_ms}ms`}
                  </span>
                  {testMut.data.detail && (
                    <span className="text-muted-foreground text-xs">({testMut.data.detail})</span>
                  )}
                </div>
              )}
            </div>

            {/* Adapter info */}
            {conn.adapter_meta && (
              <div>
                <p className="text-xs font-medium text-muted-foreground mb-1">Adapter</p>
                <div className="rounded-lg border bg-muted/40 px-3 py-2 text-xs space-y-1">
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Locality</span>
                    <span className="flex items-center gap-1">
                      <LocalityIcon locality={conn.adapter_meta.locality} />
                      {conn.adapter_meta.locality}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Network egress</span>
                    <span>{conn.adapter_meta.network_egress}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Formats</span>
                    <span>{(conn.adapter_meta.supported_formats ?? []).join(", ")}</span>
                  </div>
                </div>
              </div>
            )}

            {/* Vault keys */}
            {conn.secrets && conn.secrets.length > 0 && (
              <div>
                <p className="text-xs font-medium text-muted-foreground mb-1">Vault keys</p>
                <div className="flex flex-wrap gap-1.5">
                  {(conn.secrets as string[]).map((s) => (
                    <code key={s} className="text-xs rounded border bg-muted px-2 py-0.5 flex items-center gap-1">
                      <Lock className="h-3 w-3 text-muted-foreground" />
                      {s}
                    </code>
                  ))}
                </div>
                <p className="text-xs text-muted-foreground mt-1">
                  Injected as env vars into the isolated compute worker at runtime.
                </p>
              </div>
            )}

            {/* How to use */}
            <div className="rounded border bg-muted/20 px-3 py-2 text-xs text-muted-foreground flex items-start gap-1.5">
              <Info className="h-3.5 w-3.5 shrink-0 mt-0.5" />
              <span>
                Ask the AI: <em>"Run a compute job on <strong>{name}</strong> to …"</em> or reference it in a workflow step.
              </span>
            </div>

            {/* Audit events */}
            {auditEvents.length > 0 && (
              <div>
                <p className="text-xs font-medium text-muted-foreground mb-1">
                  Recent audit events
                </p>
                <div className="space-y-1 max-h-40 overflow-y-auto">
                  {auditEvents.map((ev, i) => (
                    <div key={i} className="flex items-start justify-between text-xs border rounded px-2 py-1">
                      <span className="font-mono text-muted-foreground">{ev.event_type}</span>
                      <span className="text-muted-foreground">{formatTs(ev.ts)}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

// ── HTTP Data Bridges (unchanged) ─────────────────────────────────────────

function AddHttpBridgeDialog({
  open,
  onClose,
  csrf,
}: {
  open: boolean;
  onClose: () => void;
  csrf: string;
}) {
  const qc = useQueryClient();

  const [adapterId, setAdapterId] = React.useState("");
  const [displayName, setDisplayName] = React.useState("");
  const [baseUrl, setBaseUrl] = React.useState("");
  const [authType, setAuthType] = React.useState<"none" | "bearer" | "api_key">("none");
  const [authEnvVar, setAuthEnvVar] = React.useState("");
  const [description, setDescription] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);

  function resetForm() {
    setAdapterId(""); setDisplayName(""); setBaseUrl("");
    setAuthType("none"); setAuthEnvVar(""); setDescription(""); setError(null);
  }

  const registerMut = useMutation({
    mutationFn: () =>
      registerHttpAdapter(
        adapterId,
        {
          display_name: displayName,
          base_url: baseUrl,
          auth_type: authType,
          auth_env: authType !== "none" ? authEnvVar : undefined,
          description: description || undefined,
        },
        csrf,
      ),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["http-adapters"] }); resetForm(); onClose(); },
    onError: (err: Error) => setError(err.message),
  });

  function handleClose() { resetForm(); onClose(); }

  return (
    <Dialog open={open} onOpenChange={(v) => !v && handleClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Add HTTP Bridge</DialogTitle>
          <DialogDescription>
            Register an HTTP server that implements /ping, /schema, and /query as a CorvinOS data source.
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={(e) => { e.preventDefault(); if (!adapterId || !displayName || !baseUrl) return; registerMut.mutate(); }} className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs font-medium mb-1 block">Adapter ID *</label>
              <Input data-testid="adapter-id-input" placeholder="e.g. my-bridge" value={adapterId} onChange={(e) => setAdapterId(e.target.value)} required />
            </div>
            <div>
              <label className="text-xs font-medium mb-1 block">Display Name *</label>
              <Input data-testid="adapter-name-input" placeholder="e.g. My Data Bridge" value={displayName} onChange={(e) => setDisplayName(e.target.value)} required />
            </div>
          </div>

          <div>
            <label className="text-xs font-medium mb-1 block">Base URL *</label>
            <Input data-testid="adapter-base-url-input" placeholder="http://localhost:8080" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} required />
          </div>

          <div>
            <label className="text-xs font-medium mb-1 block">Auth Type</label>
            <select data-testid="adapter-auth-type-select" className="w-full rounded-md border bg-background px-3 py-2 text-sm" value={authType} onChange={(e) => setAuthType(e.target.value as "none" | "bearer" | "api_key")}>
              <option value="none">None</option>
              <option value="bearer">Bearer Token</option>
              <option value="api_key">API Key</option>
            </select>
          </div>

          {authType !== "none" && (
            <div>
              <label className="text-xs font-medium mb-1 block">Auth Env Var</label>
              <Input data-testid="adapter-auth-env-var-input" placeholder="e.g. MY_BRIDGE_KEY" value={authEnvVar} onChange={(e) => setAuthEnvVar(e.target.value)} />
            </div>
          )}

          <div>
            <label className="text-xs font-medium mb-1 block">Description</label>
            <Input data-testid="adapter-description-input" placeholder="Optional description" value={description} onChange={(e) => setDescription(e.target.value)} />
          </div>

          {error && (
            <p className="text-sm text-destructive rounded border border-destructive/30 bg-destructive/10 px-3 py-2">{error}</p>
          )}

          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="outline" onClick={handleClose}>Cancel</Button>
            <Button type="submit" data-testid="adapter-submit-btn" disabled={registerMut.isPending}>
              {registerMut.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Add Bridge
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function HttpAdapterCard({ adapter, csrf }: { adapter: HttpAdapter; csrf: string }) {
  const qc = useQueryClient();
  const [pingResult, setPingResult] = React.useState<{ ok: boolean; detail?: string } | null>(null);

  const pingMut = useMutation({
    mutationFn: () => pingHttpAdapter(adapter.adapter_id, csrf),
    onSuccess: (data) => setPingResult(data),
    onError: (err: Error) => setPingResult({ ok: false, detail: err.message }),
  });
  const removeMut = useMutation({
    mutationFn: () => removeHttpAdapter(adapter.adapter_id, csrf),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["http-adapters"] }),
  });

  const urlSuffix = adapter.base_url_hash ? adapter.base_url_hash.slice(-8) : "????????";

  return (
    <Card>
      <CardContent className="px-4 py-3">
        <div className="flex items-start gap-3">
          <Globe className="h-5 w-5 shrink-0 text-muted-foreground mt-0.5" />
          <div className="flex-1 min-w-0 space-y-1">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="font-medium text-sm">{adapter.display_name}</span>
              <Badge variant="outline" className="text-xs font-mono">{adapter.adapter_id}</Badge>
              <Badge variant="secondary" className="text-xs">{adapter.auth_type}</Badge>
              <Badge variant="outline" className="text-xs">
                <HardDrive className="mr-1 h-3 w-3" />{adapter.locality ?? "unknown"}
              </Badge>
            </div>
            <p className="text-xs text-muted-foreground font-mono">URL: ****{urlSuffix}</p>
            {pingResult !== null && (
              <div className="flex items-center gap-1.5 text-xs mt-1">
                {pingResult.ok
                  ? <CheckCircle2 className="h-3.5 w-3.5 text-green-500 shrink-0" />
                  : <XCircle className="h-3.5 w-3.5 text-destructive shrink-0" />}
                <span className={pingResult.ok ? "text-green-700 dark:text-green-400" : "text-destructive"}>
                  {pingResult.ok ? "Reachable" : "Error"}
                </span>
                {pingResult.detail && <span className="text-muted-foreground">— {pingResult.detail}</span>}
              </div>
            )}
          </div>
          <div className="flex items-center gap-1 shrink-0">
            <Button variant="outline" size="sm" data-testid={`ping-http-adapter-${adapter.adapter_id}`} onClick={() => pingMut.mutate()} disabled={pingMut.isPending}>
              {pingMut.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Zap className="h-3.5 w-3.5" />}
              <span className="ml-1">Ping</span>
            </Button>
            <Button variant="ghost" size="icon" className="h-8 w-8 text-destructive hover:text-destructive" data-testid={`remove-http-adapter-${adapter.adapter_id}`}
              onClick={() => { if (confirm(`Remove HTTP bridge "${adapter.display_name}"?`)) removeMut.mutate(); }} disabled={removeMut.isPending}>
              {removeMut.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}
            </Button>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function HttpAdaptersSection({ csrf }: { csrf: string }) {
  const [showAddDialog, setShowAddDialog] = React.useState(false);
  const { data, isLoading } = useQuery<{ adapters: HttpAdapter[] }>({
    queryKey: ["http-adapters"],
    queryFn: ({ signal }) => listHttpAdapters(signal),
  });
  const adapters = data?.adapters ?? [];

  return (
    <div className="space-y-4 pt-6 border-t">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold flex items-center gap-2">
            <Globe className="h-5 w-5" />
            HTTP Data Bridges
          </h2>
          <p className="text-sm text-muted-foreground mt-0.5">
            Connect any HTTP server implementing /ping, /schema, /query to CorvinOS as a data source.
          </p>
        </div>
        <Button data-testid="add-http-adapter-btn" onClick={() => setShowAddDialog(true)}>
          <Plus className="mr-2 h-4 w-4" />
          Add HTTP Bridge
        </Button>
      </div>

      {isLoading && (
        <div className="space-y-2">
          {[...Array(2)].map((_, i) => <Skeleton key={i} className="h-20 w-full" />)}
        </div>
      )}
      {!isLoading && adapters.length === 0 && (
        <Card>
          <CardContent className="flex flex-col items-center justify-center py-10 text-center">
            <Globe className="h-7 w-7 text-muted-foreground mb-3" />
            <p className="font-medium text-sm">No HTTP bridges registered yet.</p>
            <p className="text-xs text-muted-foreground mt-1">
              Add an HTTP bridge to connect any compatible server as a data source.
            </p>
          </CardContent>
        </Card>
      )}
      {!isLoading && adapters.length > 0 && (
        <div className="space-y-2">
          {adapters.map((adapter) => (
            <HttpAdapterCard key={adapter.adapter_id} adapter={adapter} csrf={csrf} />
          ))}
        </div>
      )}

      <AddHttpBridgeDialog open={showAddDialog} onClose={() => setShowAddDialog(false)} csrf={csrf} />
    </div>
  );
}

// ── Main Page ─────────────────────────────────────────────────────────────

export function DataSourcesPage() {
  const { session } = useAuth();
  const csrf = (session as { csrf_token?: string } | null)?.csrf_token ?? "";
  const qc = useQueryClient();

  const [showWizard, setShowWizard] = React.useState(false);
  const [detailName, setDetailName] = React.useState<string | null>(null);
  const [search, setSearch] = React.useState("");

  const { data: licenseInfo } = useQuery<LicenseInfo>({
    queryKey: ["license", "info"],
    queryFn: ({ signal }) => getLicenseInfo(signal),
    staleTime: 5 * 60_000,
  });

  const { data: connections = [], isLoading } = useQuery<DSIConnection[]>({
    queryKey: ["data-sources"],
    queryFn: ({ signal }) => listDataSources(signal),
  });

  const deleteMut = useMutation({
    mutationFn: (name: string) => unregisterDataSource(name, csrf),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["data-sources"] });
      if (detailName) setDetailName(null);
    },
  });

  const filtered = connections.filter(
    (c) =>
      c.name.toLowerCase().includes(search.toLowerCase()) ||
      c.adapter.toLowerCase().includes(search.toLowerCase()) ||
      (c.description ?? "").toLowerCase().includes(search.toLowerCase()),
  );

  return (
    <div className="p-6 max-w-5xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Database className="h-6 w-6" />
            Data Sources
          </h1>
          <p className="text-sm text-muted-foreground mt-0.5">
            Connect real databases, cloud storage, and APIs to the AI compute layer.
            Credentials are encrypted end-to-end.
          </p>
        </div>
        <Button onClick={() => setShowWizard(true)} data-testid="connect-database-btn">
          <Plus className="mr-2 h-4 w-4" />
          Connect database
        </Button>
      </div>

      {/* Supported types strip */}
      <div className="flex flex-wrap gap-1.5">
        {DB_CATALOG.map((db) => (
          <button
            key={db.id}
            onClick={() => setShowWizard(true)}
            className="inline-flex items-center gap-1 rounded border px-2 py-0.5 text-xs hover:bg-accent transition-colors"
            title={db.description}
          >
            <span>{db.icon}</span>
            <span className="text-muted-foreground">{db.name}</span>
          </button>
        ))}
      </div>

      {/* Search + refresh */}
      <div className="flex items-center gap-2">
        <Input
          placeholder="Filter by name, adapter, or description…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="max-w-sm"
        />
        <Button variant="ghost" size="icon" onClick={() => qc.invalidateQueries({ queryKey: ["data-sources"] })}>
          <RefreshCw className="h-4 w-4" />
        </Button>
      </div>

      {isLoading && (
        <div className="space-y-2">
          {[...Array(3)].map((_, i) => <Skeleton key={i} className="h-16 w-full" />)}
        </div>
      )}

      {!isLoading && filtered.length === 0 && (
        <Card>
          <CardContent className="flex flex-col items-center justify-center py-12 text-center">
            <Database className="h-8 w-8 text-muted-foreground mb-3" />
            <p className="font-medium">No databases connected</p>
            <p className="text-sm text-muted-foreground mt-1 max-w-sm">
              Connect a PostgreSQL, MySQL, S3, BigQuery, Snowflake, or any supported database so the AI can run compute jobs on your data.
            </p>
            <Button className="mt-4" onClick={() => setShowWizard(true)}>
              <Plus className="mr-2 h-4 w-4" />
              Connect your first database
            </Button>
          </CardContent>
        </Card>
      )}

      {/* Connection list */}
      <div className="space-y-2">
        {filtered.map((conn) => {
          const dbType = DB_CATALOG.find((d) => d.id === conn.adapter);
          return (
            <div
              key={conn.name}
              data-testid={`conn-row-${conn.name}`}
              className="flex items-center gap-4 rounded-lg border bg-card px-4 py-3 hover:bg-accent/40 transition-colors cursor-pointer"
              onClick={() => setDetailName(conn.name)}
            >
              <span className="text-lg shrink-0">{dbType?.icon ?? "🗄️"}</span>

              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="font-medium text-sm">{conn.name}</span>
                  <ClassificationBadge value={conn.data_classification} />
                  <Badge variant="outline" className="text-xs">
                    {dbType?.name ?? conn.adapter}
                  </Badge>
                  {conn.data_residency && conn.data_residency !== "any" && (
                    <Badge variant="secondary" className="text-xs">
                      <Globe className="mr-1 h-3 w-3" />
                      {conn.data_residency}
                    </Badge>
                  )}
                </div>
                {conn.description && (
                  <p className="text-xs text-muted-foreground mt-0.5 truncate">{conn.description}</p>
                )}
                {conn.secrets && (conn.secrets as string[]).length > 0 && (
                  <p className="text-xs text-muted-foreground mt-0.5 flex items-center gap-1">
                    <Lock className="h-3 w-3" />
                    {(conn.secrets as string[]).length} credential{(conn.secrets as string[]).length !== 1 ? "s" : ""} in vault
                  </p>
                )}
              </div>

              <div className="flex items-center gap-2 shrink-0">
                <Button
                  variant="ghost"
                  size="icon"
                  data-testid={`delete-conn-${conn.name}`}
                  className="h-8 w-8 text-destructive hover:text-destructive"
                  onClick={(e) => {
                    e.stopPropagation();
                    if (confirm(`Remove connection "${conn.name}"? Vault credentials are not deleted.`)) {
                      deleteMut.mutate(conn.name);
                    }
                  }}
                >
                  <Trash2 className="h-4 w-4" />
                </Button>
                <ChevronRight className="h-4 w-4 text-muted-foreground" />
              </div>
            </div>
          );
        })}
      </div>

      {/* Dialogs */}
      <ConnectWizard open={showWizard} onClose={() => setShowWizard(false)} csrf={csrf} licenseInfo={licenseInfo} />
      <DetailDialog name={detailName} csrf={csrf} open={!!detailName} onClose={() => setDetailName(null)} />

      {/* HTTP Bridges section */}
      <HttpAdaptersSection csrf={csrf} />
    </div>
  );
}
