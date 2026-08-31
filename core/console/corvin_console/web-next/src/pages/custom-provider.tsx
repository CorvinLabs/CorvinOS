import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  ChevronRight,
  ChevronLeft,
  ExternalLink,
  Zap,
  CheckCircle2,
  AlertCircle,
  Database,
  Loader2,
  Lock,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";
import { api, ApiError, getLicenseInfo, listRAGProviders } from "@/lib/api";
import { useAuth } from "@/lib/auth";

type Step = "basic" | "api" | "mapping" | "compliance";

interface FormState {
  provider_id: string;
  name: string;
  description: string;
  author: string;
  version: string;
  endpoint: string;
  method: "GET" | "POST";
  timeout_ms: number;
  auth_type: "bearer-token" | "api-key" | "basic" | "oauth2";
  auth_token_env_var: string;
  query_format_sample: string;
  content_path: string;
  score_path: string;
  metadata_path: string;
  source_url_path: string;
  capabilities: string[];
  data_classification: "PUBLIC" | "INTERNAL" | "CONFIDENTIAL" | "SECRET";
  compliance_zone: "EU" | "US" | "APAC" | "HYBRID";
}

const CAPABILITIES = [
  "keyword-search",
  "semantic-search",
  "filtering-by-metadata",
  "time-range-queries",
  "faceted-search",
];

const STEPS: { id: Step; label: string; description: string }[] = [
  { id: "basic", label: "Basic Info", description: "Provider name and version" },
  { id: "api", label: "API Config", description: "Endpoint and authentication" },
  { id: "mapping", label: "Response Mapping", description: "Extract fields from API" },
  { id: "compliance", label: "Compliance", description: "Capabilities and zone" },
];

export default function CustomProviderPage() {
  const { session } = useAuth();

  const { data: licenseInfo } = useQuery({
    queryKey: ["license", "info"],
    queryFn: ({ signal }) => getLicenseInfo(signal),
    staleTime: 60_000,
  });

  const { data: providersData } = useQuery({
    queryKey: ["rag-providers"],
    queryFn: () => listRAGProviders(),
  });

  const providerCount = providersData?.registered_count ?? 0;
  const ragMax =
    typeof licenseInfo?.limits?.rag_providers_max === "number"
      ? (licenseInfo.limits.rag_providers_max as number)
      : null;
  const atRagLimit = ragMax !== null && providerCount >= ragMax;

  const [step, setStep] = useState<Step>("basic");
  const [form, setForm] = useState<FormState>({
    provider_id: "",
    name: "",
    description: "",
    author: "",
    version: "1.0",
    endpoint: "",
    method: "POST",
    timeout_ms: 5000,
    auth_type: "bearer-token",
    auth_token_env_var: "",
    query_format_sample: '{"query": "{query}", "limit": {limit}}',
    content_path: "",
    score_path: "",
    metadata_path: "",
    source_url_path: "",
    capabilities: [],
    data_classification: "INTERNAL",
    compliance_zone: "EU",
  });

  const [testResult, setTestResult] = useState<TestResult | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});

  const testApiMutation = useMutation({
    mutationFn: async (testQuery: string) =>
      api("/custom-provider/test-api", {
        method: "POST",
        csrf: session?.csrf_token,
        body: {
          endpoint: form.endpoint,
          method: form.method,
          auth_type: form.auth_type,
          auth_token: "test-token",
          test_query: testQuery,
          timeout_ms: form.timeout_ms,
        },
      }),
  });

  const createMutation = useMutation({
    mutationFn: async () => {
      try {
        return await api("/custom-provider/create", {
          method: "POST",
          csrf: session?.csrf_token,
          body: form,
        });
      } catch (err) {
        if (err instanceof ApiError && err.status === 402) {
          const detail = (err.detail as Record<string, unknown> | null) ?? {};
          throw new Error(
            String(
              (detail as Record<string, unknown>).msg ??
                "Free tier limit reached. Upgrade to Member plan for unlimited RAG providers."
            )
          );
        }
        throw err;
      }
    },
  });

  const handleNext = async () => {
    setErrors({});

    if (step === "basic") {
      if (!form.provider_id || !form.name) {
        setErrors({ _general: "All fields required" });
        return;
      }
      setStep("api");
    } else if (step === "api") {
      if (!form.endpoint) {
        setErrors({ _general: "Endpoint URL required" });
        return;
      }
      setStep("mapping");
    } else if (step === "mapping") {
      if (!form.content_path || !form.score_path) {
        setErrors({ _general: "Field mappings required" });
        return;
      }
      setStep("compliance");
    } else if (step === "compliance") {
      if (form.capabilities.length === 0) {
        setErrors({ _general: "Select at least one capability" });
        return;
      }
      try {
        const result = await createMutation.mutateAsync() as Record<string, unknown>;
        if (result.status === "created") {
          alert(`✅ Provider created: ${result.provider_id}`);
          setStep("basic");
          setForm({ ...form, provider_id: "", name: "", description: "", endpoint: "" });
        } else {
          setErrors({ _general: String(result.error ?? "Failed to create provider") });
        }
      } catch (err: unknown) {
        setErrors({
          _general: err instanceof Error ? err.message : "Failed to create provider",
        });
      }
    }
  };

  const handleBack = () => {
    const steps: Step[] = ["basic", "api", "mapping", "compliance"];
    const idx = steps.indexOf(step);
    if (idx > 0) setStep(steps[idx - 1]);
  };

  const updateForm = (key: keyof FormState, value: FormState[keyof FormState]) => {
    setForm({ ...form, [key]: value });
  };

  const currentStepIndex = STEPS.findIndex((s) => s.id === step);

  return (
    <div className="space-y-6 p-6">
      {/* Header */}
      <div className="flex items-center gap-3">
        <Database className="h-8 w-8 text-blue-600 dark:text-blue-400" />
        <div>
          <h1 className="text-3xl font-bold">Create Custom RAG Provider</h1>
          <p className="text-sm text-muted-foreground mt-1">Connect your API in 4 simple steps</p>
        </div>
      </div>

      {/* License limit banner */}
      {atRagLimit && (
        <div
          data-testid="custom-provider-limit-banner"
          className="flex items-start gap-3 rounded-lg border border-amber-500/30 bg-amber-500/5 px-4 py-3"
        >
          <Lock className="h-4 w-4 mt-0.5 text-amber-600 shrink-0" />
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium text-amber-700 dark:text-amber-400">
              RAG provider limit reached ({providerCount}/{ragMax})
            </p>
            <p className="text-xs text-amber-600 dark:text-amber-500 mt-0.5">
              The Free tier allows at most {ragMax} RAG provider. Creating a new provider will fail until you upgrade.{" "}
              <a
                href="https://corvin-labs.com/pricing"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 underline hover:no-underline"
              >
                Upgrade to Member
                <ExternalLink className="h-3 w-3" />
              </a>
            </p>
          </div>
        </div>
      )}

      {/* Progress Bar */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <span className="text-xs font-medium text-muted-foreground">
            Step {currentStepIndex + 1} of {STEPS.length}
          </span>
          <span className="text-xs font-medium text-muted-foreground">{STEPS[currentStepIndex].description}</span>
        </div>
        <div className="flex gap-2">
          {STEPS.map((s, idx) => (
            <div
              key={s.id}
              className={cn(
                "flex-1 h-2 rounded-full transition-all",
                idx <= currentStepIndex
                  ? "bg-blue-600 dark:bg-blue-500"
                  : "bg-muted dark:bg-muted"
              )}
            />
          ))}
        </div>
      </div>

      {/* Content Card */}
      <Card className="border border-muted dark:border-muted">
        <CardHeader>
          <CardTitle>{STEPS[currentStepIndex].label}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-6">
          {errors._general && (
            <div className="flex items-center gap-2 p-3 rounded-lg bg-red-500/10 border border-red-500/30 dark:bg-red-500/5">
              <AlertCircle className="h-4 w-4 text-red-600 dark:text-red-400" />
              <span className="text-sm text-red-600 dark:text-red-400">{errors._general}</span>
            </div>
          )}

          {step === "basic" && <Step1Basic form={form} updateForm={updateForm} errors={errors} />}
          {step === "api" && (
            <Step2API
              form={form}
              updateForm={updateForm}
              onTest={() => setTestResult(null)}
              testResult={testResult}
              isLoading={testApiMutation.isPending}
            />
          )}
          {step === "mapping" && <Step3Mapping form={form} updateForm={updateForm} testResult={testResult} />}
          {step === "compliance" && <Step4Compliance form={form} updateForm={updateForm} />}
        </CardContent>
      </Card>

      {/* Navigation */}
      <div className="flex justify-between gap-3">
        <Button
          onClick={handleBack}
          disabled={step === "basic"}
          variant="outline"
          className="gap-2"
        >
          <ChevronLeft className="h-4 w-4" />
          Back
        </Button>

        <Button
          onClick={handleNext}
          disabled={createMutation.isPending || (step === "compliance" && atRagLimit)}
          className="gap-2"
          title={
            step === "compliance" && atRagLimit
              ? `Free tier limit reached (${providerCount}/${ragMax})`
              : undefined
          }
        >
          {step === "compliance" ? (
            atRagLimit ? (
              <>
                <Lock className="h-4 w-4" />
                Limit Reached — Upgrade Required
              </>
            ) : (
              <>
                <Zap className="h-4 w-4" />
                {createMutation.isPending ? "Creating..." : "Create Provider"}
              </>
            )
          ) : (
            <>
              Next
              <ChevronRight className="h-4 w-4" />
            </>
          )}
        </Button>
      </div>
    </div>
  );
}

interface TestResult {
  status: string;
  http_status?: number;
  fields_detected?: string[];
  error?: string;
}

// ── Shared step prop types ─────────────────────────────────

interface StepBaseProps {
  form: FormState;
  updateForm: (key: keyof FormState, value: FormState[keyof FormState]) => void;
}

// ── Step 1: Basic Info ────────────────────────────────────

function Step1Basic({ form, updateForm }: StepBaseProps & { errors?: Record<string, string> }) {
  return (
    <div className="space-y-4">
      <div>
        <Label htmlFor="provider_id">Provider ID</Label>
        <Input
          id="provider_id"
          placeholder="my-custom-api"
          value={form.provider_id}
          onChange={(e) => updateForm("provider_id", e.target.value)}
          className="mt-2"
        />
      </div>

      <div>
        <Label htmlFor="name">Name</Label>
        <Input
          id="name"
          placeholder="My Custom API"
          value={form.name}
          onChange={(e) => updateForm("name", e.target.value)}
          className="mt-2"
        />
      </div>

      <div>
        <Label htmlFor="description">Description</Label>
        <textarea
          id="description"
          placeholder="What does this API search?"
          value={form.description}
          onChange={(e) => updateForm("description", e.target.value)}
          rows={2}
          className="w-full mt-2 px-3 py-2 border border-input rounded-md bg-background dark:bg-muted/40 text-foreground dark:text-foreground placeholder:text-muted-foreground dark:placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-blue-500 dark:focus:ring-blue-500"
        />
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div>
          <Label htmlFor="author">Author</Label>
          <Input
            id="author"
            placeholder="Your name/team"
            value={form.author}
            onChange={(e) => updateForm("author", e.target.value)}
            className="mt-2"
          />
        </div>
        <div>
          <Label htmlFor="version">Version</Label>
          <Input
            id="version"
            placeholder="1.0"
            value={form.version}
            onChange={(e) => updateForm("version", e.target.value)}
            className="mt-2"
          />
        </div>
      </div>
    </div>
  );
}

// ── Step 2: API Configuration ────────────────────────────

function Step2API({ form, updateForm, onTest, testResult, isLoading }: StepBaseProps & {
  onTest: () => void;
  testResult: TestResult | null;
  isLoading: boolean;
}) {
  return (
    <div className="space-y-4">
      <div>
        <Label htmlFor="endpoint">API Endpoint URL</Label>
        <Input
          id="endpoint"
          type="url"
          placeholder="https://api.example.com/search"
          value={form.endpoint}
          onChange={(e) => updateForm("endpoint", e.target.value)}
          className="mt-2"
        />
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div>
          <Label htmlFor="method">HTTP Method</Label>
          <select
            id="method"
            value={form.method}
            onChange={(e) => updateForm("method", e.target.value)}
            className="w-full mt-2 px-3 py-2 border border-input rounded-md bg-background dark:bg-muted/40 text-foreground dark:text-foreground focus:outline-none focus:ring-2 focus:ring-blue-500 dark:focus:ring-blue-500"
          >
            <option>GET</option>
            <option>POST</option>
          </select>
        </div>
        <div>
          <Label htmlFor="timeout">Timeout (ms)</Label>
          <Input
            id="timeout"
            type="number"
            value={form.timeout_ms}
            onChange={(e) => updateForm("timeout_ms", parseInt(e.target.value))}
            className="mt-2"
          />
        </div>
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div>
          <Label htmlFor="auth_type">Auth Type</Label>
          <select
            id="auth_type"
            value={form.auth_type}
            onChange={(e) => updateForm("auth_type", e.target.value)}
            className="w-full mt-2 px-3 py-2 border border-input rounded-md bg-background dark:bg-muted/40 text-foreground dark:text-foreground focus:outline-none focus:ring-2 focus:ring-blue-500 dark:focus:ring-blue-500"
          >
            <option value="bearer-token">Bearer Token</option>
            <option value="api-key">API Key</option>
            <option value="basic">Basic Auth</option>
          </select>
        </div>
        <div>
          <Label htmlFor="auth_token_env">Token Env Var</Label>
          <Input
            id="auth_token_env"
            placeholder="MY_API_TOKEN"
            value={form.auth_token_env_var}
            onChange={(e) => updateForm("auth_token_env_var", e.target.value)}
            className="mt-2"
          />
        </div>
      </div>

      <div>
        <Label htmlFor="query_template">Query Format Template</Label>
        <textarea
          id="query_template"
          value={form.query_format_sample}
          onChange={(e) => updateForm("query_format_sample", e.target.value)}
          rows={3}
          className="w-full mt-2 px-3 py-2 border border-input rounded-md bg-background dark:bg-muted/40 text-foreground dark:text-foreground font-mono text-sm placeholder:text-muted-foreground dark:placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-blue-500 dark:focus:ring-blue-500"
          placeholder='{"search": "{query}", "results": {limit}}'
        />
        <p className="text-xs text-muted-foreground mt-2">Must contain {"{query}"} and {"{limit}"} placeholders</p>
      </div>

      <Button
        onClick={() => onTest()}
        disabled={isLoading || !form.endpoint}
        variant="outline"
        className="w-full"
      >
        {isLoading ? (
          <>
            <Loader2 className="h-4 w-4 animate-spin mr-2" />
            Testing...
          </>
        ) : (
          <>
            <Zap className="h-4 w-4 mr-2" />
            Test API Connectivity
          </>
        )}
      </Button>

      {testResult && (
        <div
          className={cn(
            "p-4 rounded-lg border",
            testResult.status === "connected"
              ? "bg-emerald-500/10 border-emerald-500/30 dark:bg-emerald-500/5"
              : "bg-red-500/10 border-red-500/30 dark:bg-red-500/5"
          )}
        >
          {testResult.status === "connected" ? (
            <div className="space-y-1">
              <div className="flex items-center gap-2">
                <CheckCircle2 className="h-4 w-4 text-emerald-600 dark:text-emerald-400" />
                <p className="font-medium text-emerald-700 dark:text-emerald-400">Connected!</p>
              </div>
              <p className="text-sm text-emerald-600 dark:text-emerald-400">HTTP {testResult.http_status}</p>
              {(testResult.fields_detected?.length ?? 0) > 0 && (
                <p className="text-sm text-emerald-600 dark:text-emerald-400">
                  <strong>Fields:</strong> {testResult.fields_detected?.join(", ")}
                </p>
              )}
            </div>
          ) : (
            <div className="flex items-center gap-2">
              <AlertCircle className="h-4 w-4 text-red-600 dark:text-red-400" />
              <p className="text-sm text-red-700 dark:text-red-400">{testResult.error}</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Step 3: Response Mapping ─────────────────────────────

function Step3Mapping({ form, updateForm }: StepBaseProps & { testResult?: TestResult | null }) {
  return (
    <div className="space-y-4">
      {form.endpoint && (
        <div className="p-3 rounded-lg bg-blue-500/10 border border-blue-500/30 dark:bg-blue-500/5 dark:border-blue-500/30">
          <p className="text-sm text-blue-700 dark:text-blue-400">
            <strong>Tip:</strong> Use JSONPath like `results[].content`, `data.items[].score`
          </p>
        </div>
      )}

      <div>
        <Label htmlFor="content_path">Content Field Path</Label>
        <Input
          id="content_path"
          placeholder="results[].content"
          value={form.content_path}
          onChange={(e) => updateForm("content_path", e.target.value)}
          className="mt-2"
        />
        <p className="text-xs text-muted-foreground mt-1">Path to the search result text</p>
      </div>

      <div>
        <Label htmlFor="score_path">Score Field Path</Label>
        <Input
          id="score_path"
          placeholder="results[].score"
          value={form.score_path}
          onChange={(e) => updateForm("score_path", e.target.value)}
          className="mt-2"
        />
        <p className="text-xs text-muted-foreground mt-1">Path to the relevance score (0-1)</p>
      </div>

      <div>
        <Label htmlFor="metadata_path">Metadata Path</Label>
        <Input
          id="metadata_path"
          placeholder="results[]"
          value={form.metadata_path}
          onChange={(e) => updateForm("metadata_path", e.target.value)}
          className="mt-2"
        />
        <p className="text-xs text-muted-foreground mt-1">Path to metadata object</p>
      </div>

      <div>
        <Label htmlFor="source_url_path">Source URL Path (Optional)</Label>
        <Input
          id="source_url_path"
          placeholder="results[].url"
          value={form.source_url_path}
          onChange={(e) => updateForm("source_url_path", e.target.value)}
          className="mt-2"
        />
      </div>
    </div>
  );
}

// ── Step 4: Compliance & Capabilities ────────────────────

function Step4Compliance({ form, updateForm }: StepBaseProps) {
  return (
    <div className="space-y-4">
      <div>
        <Label>Capabilities</Label>
        <div className="grid grid-cols-2 gap-3 mt-2">
          {CAPABILITIES.map((cap) => (
            <label key={cap} className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={form.capabilities.includes(cap)}
                onChange={(e) => {
                  if (e.target.checked) {
                    updateForm("capabilities", [...form.capabilities, cap]);
                  } else {
                    updateForm(
                      "capabilities",
                      form.capabilities.filter((c: string) => c !== cap)
                    );
                  }
                }}
                className="w-4 h-4 rounded border-input"
              />
              <span className="text-sm">{cap}</span>
            </label>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div>
          <Label htmlFor="classification">Data Classification</Label>
          <select
            id="classification"
            value={form.data_classification}
            onChange={(e) => updateForm("data_classification", e.target.value)}
            className="w-full mt-2 px-3 py-2 border border-input rounded-md bg-background dark:bg-muted/40 text-foreground dark:text-foreground focus:outline-none focus:ring-2 focus:ring-blue-500 dark:focus:ring-blue-500"
          >
            <option value="PUBLIC">PUBLIC</option>
            <option value="INTERNAL">INTERNAL</option>
            <option value="CONFIDENTIAL">CONFIDENTIAL</option>
            <option value="SECRET">SECRET</option>
          </select>
        </div>
        <div>
          <Label htmlFor="zone">Compliance Zone</Label>
          <select
            id="zone"
            value={form.compliance_zone}
            onChange={(e) => updateForm("compliance_zone", e.target.value)}
            className="w-full mt-2 px-3 py-2 border border-input rounded-md bg-background dark:bg-muted/40 text-foreground dark:text-foreground focus:outline-none focus:ring-2 focus:ring-blue-500 dark:focus:ring-blue-500"
          >
            <option value="EU">EU (GDPR)</option>
            <option value="US">US</option>
            <option value="APAC">Asia-Pacific</option>
            <option value="HYBRID">Hybrid</option>
          </select>
        </div>
      </div>

      <div className="p-3 rounded-lg bg-blue-500/10 border border-blue-500/30 dark:bg-blue-500/5 dark:border-blue-500/30">
        <p className="text-sm text-blue-700 dark:text-blue-400">
          ✅ Your provider will be created with secure defaults (circuit breaker, retries, health checks).
        </p>
      </div>
    </div>
  );
}
