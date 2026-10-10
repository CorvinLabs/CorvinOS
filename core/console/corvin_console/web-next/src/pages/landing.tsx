import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Hammer, MessageSquare, ShieldCheck, Sparkles, Workflow } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

// PublicLayout temporary fallback (ADR-0561: landing/login pages to be redesigned)
const PublicLayout = ({ children }: { children: React.ReactNode }) => <div>{children}</div>;

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
