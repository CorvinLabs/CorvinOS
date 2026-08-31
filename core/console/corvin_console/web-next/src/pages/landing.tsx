import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Hammer, MessageSquare, ShieldCheck, Sparkles, Workflow } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { landingPersonas, type LandingPersona } from "@/lib/api";
import { PublicLayout } from "@/components/layout";
import { cn } from "@/lib/utils";

const PILLARS = [
  {
    icon: Sparkles,
    title: "Personas",
    body: "Twelve ready-made AI personalities come pre-installed — from a research assistant to a browser agent. Swap any one out or build your own from scratch.",
  },
  {
    icon: MessageSquare,
    title: "Seven channels",
    body: "Telegram, Discord, Slack, WhatsApp, Email, Signal, Teams — the same AI assistant, on every platform you already use.",
  },
  {
    icon: Hammer,
    title: "Custom tools & skills",
    body: "Generate new tools and skills on the fly, safely sandboxed. Promote the ones that work from temporary to permanent — no code deployment needed.",
  },
  {
    icon: Workflow,
    title: "Smart routing",
    body: "Automatically send conversations to the right AI personality, or pin one per chat. Works with Claude, Codex, and OpenCode.",
  },
  {
    icon: ShieldCheck,
    title: "Compliance by design",
    body: "Tamper-proof activity logs, user consent controls, transparent AI disclosure, and secure key storage. Built for EU AI Act 2026 and GDPR — out of the box.",
  },
];

export function LandingPage() {
  return (
    <PublicLayout>
      <Hero />
      <section className="mx-auto max-w-6xl px-6 pb-16">
        <Pillars />
      </section>
      <section className="mx-auto max-w-6xl px-6 pb-24">
        <PersonaGallery />
      </section>
    </PublicLayout>
  );
}

function Hero() {
  return (
    <div className="corvin-hero relative overflow-hidden">
      <div className="mx-auto flex max-w-5xl flex-col items-start gap-8 px-6 py-20 sm:py-28">
        <h1 className="font-serif text-5xl font-light leading-[1.05] tracking-tight sm:text-6xl">
          Corvin
        </h1>
        <p className="max-w-2xl text-lg text-muted-foreground sm:text-xl">
          Corvin is the workshop where you craft, configure and converse with the agents that
          do real work for you — across every channel you already use, with privacy and audit
          baked into the foundation, not bolted on.
        </p>
        <div className="flex flex-wrap items-center gap-3">
          <Button asChild size="lg" variant="accent">
            <Link to="/login">
              Open the console
              <ArrowRight className="ml-1 h-4 w-4" />
            </Link>
          </Button>
          <Button
            asChild
            size="lg"
            variant="outline"
            className="border-foreground/20 hover:bg-foreground/5"
          >
            <a href="https://github.com/" target="_blank" rel="noreferrer">
              View on GitHub
            </a>
          </Button>
        </div>
      </div>
    </div>
  );
}

function Pillars() {
  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {PILLARS.map((p) => (
        <Card
          key={p.title}
          className="border-border/60 bg-card/60 transition-all hover:border-accent/40 hover:shadow-md"
        >
          <CardHeader>
            <div className="flex items-center gap-2 text-accent">
              <p.icon className="h-5 w-5" />
              <CardTitle className="text-base">{p.title}</CardTitle>
            </div>
          </CardHeader>
          <CardContent>
            <p className="text-sm leading-relaxed text-muted-foreground">{p.body}</p>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

function PersonaGallery() {
  const q = useQuery({
    queryKey: ["landing", "personas"],
    queryFn: ({ signal }) => landingPersonas(signal),
    staleTime: 5 * 60_000,
  });

  return (
    <div>
      <div className="mb-6 flex items-end justify-between gap-4">
        <div>
          <h2 className="font-serif text-3xl font-light tracking-tight">The bundled cast.</h2>
          <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
            Twelve personas come pre-installed. Each can be overridden in your tenant, copied as a
            starting point, or extended with your own MCP servers, skills and tools.
          </p>
        </div>
        <Badge variant="outline" className="hidden sm:inline-flex">
          {q.data ? `${q.data.count} personas` : "loading…"}
        </Badge>
      </div>

      {q.isError && (
        <Card className="border-destructive/40 bg-destructive/5">
          <CardContent className="py-6 text-sm text-destructive">
            The persona gallery is temporarily unavailable. Please try again in a moment.
          </CardContent>
        </Card>
      )}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {q.isLoading &&
          Array.from({ length: 6 }).map((_, i) => (
            <Card key={i} className="h-40">
              <CardContent className="space-y-3 py-6">
                <Skeleton className="h-4 w-1/3" />
                <Skeleton className="h-3 w-full" />
                <Skeleton className="h-3 w-4/5" />
                <Skeleton className="h-3 w-3/5" />
              </CardContent>
            </Card>
          ))}

        {q.data?.personas.map((p) => <PersonaCard key={p.name} persona={p} />)}
      </div>
    </div>
  );
}

function PersonaCard({ persona }: { persona: LandingPersona }) {
  const hue = nameToHue(persona.name);
  return (
    <Card className="group relative overflow-hidden border-border/60 bg-card/60 transition-all hover:-translate-y-0.5 hover:border-accent/40 hover:shadow-lg">
      <div
        aria-hidden
        className="absolute inset-x-0 top-0 h-1"
        style={{ background: `hsl(${hue} 60% 55%)` }}
      />
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <CardTitle className="text-lg capitalize">{persona.name.replace(/-/g, " ")}</CardTitle>
          <Badge variant="outline" className="font-mono text-[10px]">
            {persona.tool_namespace ?? persona.name}
          </Badge>
        </div>
        <CardDescription className="line-clamp-3 min-h-[2.75rem]">
          {persona.description || "No description provided."}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-wrap items-center gap-1.5">
        {persona.forge_enabled && <Badge variant="accent">Forge</Badge>}
        {persona.skill_forge_enabled && <Badge variant="accent">SkillForge</Badge>}
        {persona.ldd_preset && persona.ldd_preset !== "off" && (
          <Badge variant="secondary">LDD · {persona.ldd_preset}</Badge>
        )}
        <span className={cn("ml-auto text-[11px] text-muted-foreground")}>bundle</span>
      </CardContent>
    </Card>
  );
}

function nameToHue(name: string): number {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  return h % 360;
}
