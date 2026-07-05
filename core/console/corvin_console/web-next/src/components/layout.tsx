import * as React from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import {
  Activity,
  AudioLines,
  BookOpen,
  Boxes,
  Building2,
  ChevronDown,
  Cloud,
  Cpu,
  Database,
  FolderOpen,
  Gauge,
  Globe,
  Globe2,
  Hammer,
  KeyRound,
  LayoutDashboard,
  Lock,
  LogOut,
  MessagesSquare,
  Network,
  Package,
  Plug,
  Puzzle,
  Server,
  Settings,
  ShieldCheck,
  Sparkles,
  Users,
  UsersRound,
  Menu,
  Workflow,
  X,
} from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ThemeToggle } from "@/components/theme-toggle";
import { RouteErrorBoundary } from "@/components/error-boundary";
import { ConsoleAssistant } from "@/components/assistant/ConsoleAssistant";
import { useAuth } from "@/lib/auth";
import { useSettingsStream } from "@/hooks/use-settings-stream";
import { getOsEngineSetting, getLicenseInfo } from "@/lib/api";
import { LicenseBadge } from "@/components/license-gate";
import { cn } from "@/lib/utils";

// ── Engine chip — shows the active tenant-default engine in the header ───

const ENGINE_LABELS: Record<string, string> = {
  claude_code: "Claude Code",
  codex_cli:   "Codex",
  opencode:    "OpenCode",
  hermes:      "Hermes",
  copilot:     "Copilot",
};

function EngineChip() {
  const q = useQuery({
    queryKey: ["os-engine-setting"],
    queryFn: ({ signal }) => getOsEngineSetting(signal),
    refetchInterval: 60_000,
    staleTime: 30_000,
    retry: false,
  });

  const engine = q.data?.default_engine ?? "claude_code";
  const label = ENGINE_LABELS[engine] ?? engine;
  const isLocal = engine === "hermes";

  return (
    <Link
      to="/app/engines"
      title="Active AI engine — click to change"
      className={cn(
        "flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-xs transition-colors no-underline",
        "hover:bg-muted cursor-pointer",
        isLocal
          ? "border-emerald-500/30 bg-emerald-500/5 text-emerald-700 dark:text-emerald-400"
          : "border-border bg-muted/30 text-muted-foreground",
      )}
    >
      {isLocal ? <Cpu className="h-3 w-3" /> : <Cloud className="h-3 w-3" />}
      <span className="font-medium">{label}</span>
      {isLocal && (
        <span className="rounded bg-emerald-500/15 px-1 text-[9px] font-semibold uppercase tracking-wide text-emerald-600">
          local
        </span>
      )}
    </Link>
  );
}

// ── Nav data ────────────────────────────────────────────────────────────

interface NavItem {
  to: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  end?: boolean;
}

interface NavGroup {
  id: string;
  label?: string;
  collapsible?: boolean;
  defaultOpen?: boolean;
  items: NavItem[];
}

const NAV_GROUPS: NavGroup[] = [
  {
    id: "primary",
    items: [
      { to: "/app/chat",      label: "Chat",      icon: MessagesSquare },
      { to: "/app/dashboard", label: "Dashboard", icon: LayoutDashboard },
    ],
  },
  {
    id: "messaging",
    label: "Messaging",
    items: [
      { to: "/app/bridges", label: "Channels", icon: Network },
      { to: "/app/voice",   label: "Profile",  icon: AudioLines },
      { to: "/app/people",  label: "People",   icon: Users },
    ],
  },
  {
    id: "intelligence",
    label: "Assistant",
    items: [
      { to: "/app/engines",  label: "AI Engine", icon: Cpu },
      { to: "/app/browser",  label: "Browser",   icon: Globe },
      { to: "/app/personas", label: "Personas",  icon: Sparkles },
      { to: "/app/memory",   label: "Memory",    icon: BookOpen },
      { to: "/app/files",    label: "Files",     icon: FolderOpen },
    ],
  },
  {
    id: "build",
    label: "Build",
    collapsible: true,
    defaultOpen: true,
    items: [
      { to: "/app/workflows",  label: "Workflows",  icon: Workflow },
      { to: "/app/flows",      label: "Pipelines",  icon: Network },
      { to: "/app/forge",      label: "Tools",      icon: Hammer },
      { to: "/app/skills",     label: "Skills",     icon: BookOpen },
      { to: "/app/agents",      label: "Agents",      icon: ShieldCheck },
      { to: "/app/extensions",  label: "Extensions",  icon: Puzzle },
      { to: "/app/mcp-plugins", label: "MCP Plugins", icon: Package },
    ],
  },
  {
    id: "network",
    label: "Network",
    collapsible: true,
    defaultOpen: true,
    items: [
      { to: "/app/agent-hub",  label: "Agent Hub",     icon: Globe2 },
      { to: "/app/space",      label: "CorvinSpace",   icon: Globe },
      { to: "/app/orgs",       label: "Organisations", icon: Building2 },
      { to: "/app/connectors", label: "Connectors",    icon: Plug },
    ],
  },
  {
    id: "knowledge",
    label: "Data",
    collapsible: true,
    defaultOpen: true,
    items: [
      { to: "/app/data-sources",    label: "Databases",     icon: Server },
      { to: "/app/rag",             label: "Knowledge",     icon: Database },
      { to: "/app/rag-hub",         label: "Knowledge Hub", icon: Globe2 },
      { to: "/app/custom-provider", label: "Add Provider",  icon: Plug },
    ],
  },
  {
    id: "system",
    label: "System",
    collapsible: true,
    defaultOpen: false,
    items: [
      { to: "/app/activity",        label: "Activity Feed",       icon: Activity },
      { to: "/app/compute",        label: "Agentic Compute",    icon: Gauge },
      { to: "/app/api-keys",       label: "API Keys",           icon: KeyRound },
      { to: "/app/license",        label: "License",            icon: Lock },
      { to: "/app/compliance",     label: "Audit & Compliance", icon: ShieldCheck },
      { to: "/app/cowork",         label: "Auto-routing",       icon: UsersRound },
      { to: "/app/ldd",            label: "Quality",            icon: Boxes },
      { to: "/app/settings",       label: "Settings",           icon: Settings },
    ],
  },
];

// ── Collapse state persisted in localStorage ────────────────────────────

function useNavCollapse(groupId: string, defaultOpen: boolean) {
  const key = `corvin_nav_open_${groupId}`;
  const [open, setOpen] = React.useState<boolean>(() => {
    try {
      const stored = localStorage.getItem(key);
      return stored !== null ? stored === "true" : defaultOpen;
    } catch {
      return defaultOpen;
    }
  });
  const toggle = React.useCallback(() => {
    setOpen((prev) => {
      const next = !prev;
      try { localStorage.setItem(key, String(next)); } catch { /* ignore */ }
      return next;
    });
  }, [key]);
  return [open, toggle] as const;
}

// ── Single nav link ─────────────────────────────────────────────────────

function NavItemLink({ item, primary }: { item: NavItem; primary?: boolean }) {
  return (
    <NavLink
      to={item.to}
      end={item.end}
      className={({ isActive }) =>
        cn(
          "flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors",
          primary
            ? "text-foreground/80 hover:bg-muted hover:text-foreground"
            : "text-muted-foreground hover:bg-muted hover:text-foreground",
          isActive && "bg-accent/15 font-medium text-foreground",
          primary && isActive && "bg-accent/20",
        )
      }
    >
      <item.icon className={cn("h-4 w-4 shrink-0", primary && "h-[1.05rem] w-[1.05rem]")} />
      {item.label}
    </NavLink>
  );
}

// ── Group section with optional collapse ────────────────────────────────

function NavGroupSection({ group }: { group: NavGroup }) {
  const [open, toggle] = useNavCollapse(group.id, group.defaultOpen ?? true);
  const isPrimary = !group.label;

  if (isPrimary) {
    return (
      <div className="flex flex-col gap-0.5">
        {group.items.map((item) => (
          <NavItemLink key={item.to} item={item} primary />
        ))}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-0.5">
      {group.collapsible ? (
        <button
          onClick={toggle}
          className={cn(
            "flex w-full items-center justify-between px-3 py-1.5",
            "text-[10.5px] font-semibold uppercase tracking-[0.12em] text-muted-foreground/60",
            "hover:text-muted-foreground transition-colors",
          )}
        >
          {group.label}
          <ChevronDown
            className={cn(
              "h-3 w-3 transition-transform duration-200",
              !open && "-rotate-90",
            )}
          />
        </button>
      ) : (
        <div className="px-3 py-1.5 text-[10.5px] font-semibold uppercase tracking-[0.12em] text-muted-foreground/60">
          {group.label}
        </div>
      )}
      {(!group.collapsible || open) && (
        <div className="flex flex-col gap-0.5">
          {group.items.map((item) => (
            <NavItemLink key={item.to} item={item} />
          ))}
        </div>
      )}
    </div>
  );
}

// ── Licence tier badge in sidebar footer ───────────────────────────────

function LicenseTierFooter() {
  const { data } = useQuery({
    queryKey: ["license", "info"],
    queryFn: ({ signal }) => getLicenseInfo(signal),
    staleTime: 5 * 60_000,
    retry: false,
  });
  if (!data) return null;
  return (
    <Link to="/app/license" className="flex items-center justify-between px-3 py-1.5 rounded-md hover:bg-muted/40 transition-colors">
      <span className="text-[11px] text-muted-foreground">Licence</span>
      <LicenseBadge tier={data.tier} />
    </Link>
  );
}

// ── AppLayout ───────────────────────────────────────────────────────────

export function AppLayout() {
  const { session, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [assistantOpen, setAssistantOpen] = React.useState(false);
  const [mobileNavOpen, setMobileNavOpen] = React.useState(false);
  useSettingsStream();

  // Close mobile nav on route change
  React.useEffect(() => { setMobileNavOpen(false); }, [location.pathname]);

  const sidebarContent = (
    <>
      <Link to="/" className="mb-6 flex items-center gap-2.5 px-3">
        <CorvinMark />
        <div className="flex flex-col leading-tight">
          <span className="font-serif text-[1.05rem] font-semibold">Corvin</span>
          <span className="text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
            Operator Console
          </span>
        </div>
      </Link>
      <nav className="flex flex-1 flex-col gap-4 overflow-y-auto">
        {NAV_GROUPS.map((group, i) => (
          <React.Fragment key={group.id}>
            {i > 0 && <div className="mx-3 border-t border-border/60" />}
            <NavGroupSection group={group} />
          </React.Fragment>
        ))}
      </nav>
      <LicenseTierFooter />
      <div className="mt-2 flex items-center justify-between rounded-lg bg-muted/50 px-3 py-2 text-xs">
        <div className="flex flex-col leading-snug">
          <span className="font-medium text-foreground">{session?.tenant_id ?? "—"}</span>
          <span className="text-muted-foreground">{session?.tier ?? "—"}</span>
        </div>
        <Button
          variant="ghost"
          size="icon"
          aria-label="Log out"
          title="Log out"
          className="h-7 w-7 text-muted-foreground hover:text-foreground"
          onClick={async () => { await logout(); navigate("/login", { replace: true }); }}
        >
          <LogOut className="h-3.5 w-3.5" />
        </Button>
      </div>
    </>
  );

  return (
    <>
    {/* Mobile nav overlay */}
    {mobileNavOpen && (
      <div
        className="fixed inset-0 z-40 bg-black/50 md:hidden"
        onClick={() => setMobileNavOpen(false)}
        aria-hidden="true"
      />
    )}
    {/* Mobile slide-in sidebar */}
    <aside
      className={cn(
        "fixed inset-y-0 left-0 z-50 flex w-72 flex-col overflow-hidden border-r border-border bg-card/95 px-3 py-5 backdrop-blur transition-transform duration-200 md:hidden",
        mobileNavOpen ? "translate-x-0" : "-translate-x-full",
      )}
      aria-label="Mobile navigation"
    >
      <button
        className="mb-2 ml-auto flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground hover:text-foreground"
        onClick={() => setMobileNavOpen(false)}
        aria-label="Close menu"
      >
        <X className="h-4 w-4" />
      </button>
      {sidebarContent}
    </aside>

    <div className="grid grid-cols-1 min-h-screen md:grid-cols-[17rem_1fr] bg-background">
      <aside className="sticky top-0 hidden h-screen md:flex flex-col overflow-hidden border-r border-border bg-card/40 px-3 py-5">
        {sidebarContent}
      </aside>

      {/* Content area */}
      <div className="flex min-w-0 flex-col">
        <header className="sticky top-0 z-20 flex h-13 items-center justify-between gap-4 border-b border-border bg-background/80 px-4 backdrop-blur">
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <button
              className="flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground hover:text-foreground md:hidden"
              onClick={() => setMobileNavOpen(true)}
              aria-label="Open menu"
            >
              <Menu className="h-4 w-4" />
            </button>
            {/* Tenant badge — the only honest real-time signal here. A
                hardcoded "Connected" status indicator was removed (it always
                showed green regardless of actual gateway/SSE connectivity);
                no readily-available connectivity hook exists to wire it to. */}
            <Badge variant="outline" className="hidden sm:inline-flex text-[11px]">
              {session?.tenant_id ?? "—"}
            </Badge>
          </div>
          <div className="flex items-center gap-2">
            <EngineChip />
            <button
              onClick={() => setAssistantOpen((v) => !v)}
              aria-label="Corvin Assistant"
              title="Corvin Assistant"
              className={cn(
                "flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs transition-all",
                assistantOpen
                  ? "border-accent/50 bg-accent/10 text-accent"
                  : "border-border bg-muted/30 text-muted-foreground hover:border-accent/30 hover:bg-muted/50 hover:text-foreground",
              )}
            >
              <CorvinMarkSmall className="h-3.5 w-3.5" />
              <span className="hidden sm:inline font-medium">Assistant</span>
            </button>
            <ThemeToggle />
          </div>
        </header>
        <main className="flex-1 animate-fade-in px-6 py-8">
          <RouteErrorBoundary key={location.pathname} label={location.pathname}>
            <Outlet />
          </RouteErrorBoundary>
        </main>
      </div>
    </div>
    <RouteErrorBoundary label="assistant"><ConsoleAssistant open={assistantOpen} onClose={() => setAssistantOpen(false)} /></RouteErrorBoundary>
    </>
  );
}

// ── PublicLayout ────────────────────────────────────────────────────────

export function PublicLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col bg-background">
      <header className="flex items-center justify-between px-8 py-5">
        <Link to="/" className="flex items-center gap-2">
          <CorvinMark />
          <span className="font-serif text-lg font-semibold tracking-tight">Corvin</span>
        </Link>
        <div className="flex items-center gap-2">
          <ThemeToggle />
        </div>
      </header>
      <div className="flex-1">{children}</div>
      <footer className="border-t border-border/60 px-8 py-6 text-xs text-muted-foreground">
        <div className="mx-auto flex max-w-5xl items-center justify-between">
          <span>Apache-2.0 · EU AI Act 2026 · GDPR-aligned</span>
          <span className="font-mono">v0.1 · console:next</span>
        </div>
      </footer>
    </div>
  );
}

// ── CorvinMark SVG ─────────────────────────────────────────────────────

function CorvinMarkSmall({ className }: { className?: string }) {
  return (
    <svg viewBox="12 12 96 96" aria-hidden="true" className={cn("shrink-0", className)}>
      <path fill="none" stroke="currentColor" strokeWidth="8" strokeLinecap="round" strokeLinejoin="round" d="M28 40 L56 60 L28 80"/>
      <rect fill="currentColor" x="66" y="72" width="30" height="9" rx="2"/>
      <circle cx="80" cy="50" r="10" fill="#C9A227"/>
      <circle cx="80" cy="50" r="10" fill="none" stroke="currentColor" strokeWidth="2"/>
    </svg>
  );
}

function CorvinMark({ className }: { className?: string }) {
  return (
    <svg viewBox="12 12 96 96" aria-hidden="true" className={cn("h-7 w-7 text-foreground shrink-0", className)}>
      <path fill="none" stroke="currentColor" strokeWidth="8" strokeLinecap="round" strokeLinejoin="round" d="M28 40 L56 60 L28 80"/>
      <rect fill="currentColor" x="66" y="72" width="30" height="9" rx="2"/>
      <circle cx="80" cy="50" r="10" fill="#C9A227"/>
      <circle cx="80" cy="50" r="10" fill="none" stroke="currentColor" strokeWidth="2"/>
    </svg>
  );
}

export { CorvinMark };
