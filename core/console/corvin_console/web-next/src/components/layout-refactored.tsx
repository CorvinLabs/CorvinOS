/**
 * AppLayout — Refactored for ADR-0561 Phase 2 (Manifest-Driven)
 *
 * OLD: Hardcoded NAV_GROUPS + PANELS arrays
 * NEW: useConsoleManifest() provides nav_groups + panels from backend
 *
 * Changes:
 * - No hardcoded PANELS or NAV_GROUPS
 * - Sidebar rendered from manifest.nav_groups
 * - Routes mounted from manifest.panels
 * - Fallback: if manifest fails, show core panels only
 * - Same UX, zero behavior change
 */

import * as React from "react"
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom"
import { Loader2, LogOut, Menu, X, ChevronDown } from "lucide-react"
import { useQuery } from "@tanstack/react-query"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { ThemeToggle } from "@/components/theme-toggle"
import { RouteErrorBoundary } from "@/components/error-boundary"
import { ConsoleAssistant } from "@/components/assistant/ConsoleAssistant"
import { useAuth } from "@/lib/auth"
import { useSettingsStream } from "@/hooks/use-settings-stream"
import { useBuildFreshness } from "@/hooks/use-build-freshness"
import { getOsEngineSetting, getLicenseInfo } from "@/lib/api"
import { LicenseBadge } from "@/components/license-gate"
import { cn } from "@/lib/utils"
import { useConsoleManifest } from "@/adapters/capabilities"
import { ManifestNavRenderer } from "@/components/manifest-nav-renderer"
import type { NavGroup, PanelDescriptor } from "@/adapters/capabilities"

// ── Engine chip ─────────────────────────────────────────────────────────────

const ENGINE_LABELS: Record<string, string> = {
  claude_code: "Claude Code",
  codex_cli: "Codex",
  opencode: "OpenCode",
  hermes: "Hermes",
  copilot: "Copilot",
}

function EngineChip() {
  const q = useQuery({
    queryKey: ["os-engine-setting"],
    queryFn: ({ signal }) => getOsEngineSetting(signal),
    refetchInterval: 60_000,
    staleTime: 30_000,
    retry: false,
  })

  const engine = q.data?.default_engine ?? "claude_code"
  const label = ENGINE_LABELS[engine] ?? engine
  const isLocal = engine === "hermes"

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
      {isLocal ? <span>⚙️</span> : <span>☁️</span>}
      <span className="font-medium">{label}</span>
      {isLocal && (
        <span className="rounded bg-emerald-500/15 px-1 text-[9px] font-semibold uppercase tracking-wide text-emerald-600">
          local
        </span>
      )}
    </Link>
  )
}

// ── License tier footer ─────────────────────────────────────────────────────

function LicenseTierFooter() {
  const { data } = useQuery({
    queryKey: ["license", "info"],
    queryFn: ({ signal }) => getLicenseInfo(signal),
    staleTime: 5 * 60_000,
    retry: false,
  })
  if (!data) return null
  return (
    <Link to="/app/license" className="flex items-center justify-between px-3 py-1.5 rounded-md hover:bg-muted/40 transition-colors">
      <span className="text-[11px] text-muted-foreground">Licence</span>
      <LicenseBadge tier={data.tier} />
    </Link>
  )
}

// ── Collapse state ──────────────────────────────────────────────────────────

function useNavCollapse(groupId: string, defaultOpen: boolean) {
  const key = `corvin_nav_open_${groupId}`
  const [open, setOpen] = React.useState<boolean>(() => {
    try {
      const stored = localStorage.getItem(key)
      return stored !== null ? stored === "true" : defaultOpen
    } catch {
      return defaultOpen
    }
  })
  const toggle = React.useCallback(() => {
    setOpen((prev) => {
      const next = !prev
      try {
        localStorage.setItem(key, String(next))
      } catch {
        /* ignore */
      }
      return next
    })
  }, [key])
  return [open, toggle] as const
}

// ── Fallback panels (if manifest fails) ──────────────────────────────────────

const FALLBACK_PANELS: PanelDescriptor[] = [
  {
    id: "chat",
    title: "Chat",
    route: "chat",
    icon: "MessagesSquare",
    kind: "feature",
    source: "builtin",
    nav_group: "primary",
    requiredFlag: null,
    requiredCapability: null,
    element: { kind: "react-component", component: "ChatPage" },
    version: "1.0.0",
    audit_events: ["console_panel_opened"],
    tenant_scoped: true,
  },
  {
    id: "dashboard",
    title: "Dashboard",
    route: "dashboard",
    icon: "LayoutDashboard",
    kind: "feature",
    source: "builtin",
    nav_group: "primary",
    requiredFlag: null,
    requiredCapability: null,
    element: { kind: "react-component", component: "DashboardPage" },
    version: "1.0.0",
    audit_events: ["console_panel_opened"],
    tenant_scoped: true,
  },
  {
    id: "settings",
    title: "Settings",
    route: "settings",
    icon: "Settings",
    kind: "feature",
    source: "builtin",
    nav_group: "system",
    requiredFlag: null,
    requiredCapability: null,
    element: { kind: "react-component", component: "SettingsPage" },
    version: "1.0.0",
    audit_events: ["console_panel_opened"],
    tenant_scoped: true,
  },
]

const FALLBACK_NAV_GROUPS: NavGroup[] = [
  {
    id: "primary",
    label: null,
    collapsible: false,
    defaultOpen: true,
    items: [{ panel_id: "chat" }, { panel_id: "dashboard" }],
  },
  {
    id: "system",
    label: "System",
    collapsible: true,
    defaultOpen: false,
    items: [{ panel_id: "settings" }],
  },
]

// ── AppLayout (manifest-driven) ─────────────────────────────────────────────

export function AppLayout() {
  const { session, logout } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()

  // ADR-0561: Fetch manifest (single source of truth)
  const { data: manifest, isLoading: manifestLoading, error: manifestError } = useConsoleManifest()

  // Use manifest panels/nav, or fallback if unavailable (200ms timeout)
  const panels = manifest?.panels ?? FALLBACK_PANELS
  const navGroups = manifest?.nav_groups ?? FALLBACK_NAV_GROUPS

  const [assistantOpen, setAssistantOpen] = React.useState(false)
  const [mobileNavOpen, setMobileNavOpen] = React.useState(false)
  useSettingsStream()

  const build = useBuildFreshness(
    Boolean(manifest?.flags?.console_auto_reload),
  )

  React.useEffect(() => {
    setMobileNavOpen(false)
  }, [location.pathname])

  const sidebarContent = (
    <>
      <Link to="/" className="mb-6 flex items-center gap-2.5 px-3">
        <span className="text-xl">⚙️</span>
        <div className="flex flex-col leading-tight">
          <span className="font-serif text-[1.05rem] font-semibold">Corvin</span>
          <span className="text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
            Operator Console
          </span>
        </div>
      </Link>

      {/* ADR-0561: Manifest-driven nav (replaced hardcoded NAV_GROUPS) */}
      {manifestLoading ? (
        <div className="flex justify-center py-4">
          <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
        </div>
      ) : manifestError ? (
        <div className="text-xs text-muted-foreground px-3">
          Nav loading (fallback)
        </div>
      ) : (
        <ManifestNavRenderer navGroups={navGroups} panels={panels} />
      )}

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
          onClick={async () => {
            await logout()
            navigate("/login", { replace: true })
          }}
        >
          <LogOut className="h-3.5 w-3.5" />
        </Button>
      </div>
    </>
  )

  return (
    <>
      {build.stale && (
        <div className="fixed inset-x-0 top-0 z-[60] flex items-center justify-center gap-3 bg-primary px-4 py-2 text-sm text-primary-foreground shadow-lg">
          <span>A new console build is live.</span>
          <button
            type="button"
            onClick={build.reload}
            className="rounded-md bg-primary-foreground/15 px-3 py-1 font-medium underline-offset-2 hover:bg-primary-foreground/25"
          >
            Reload now
          </button>
        </div>
      )}

      {mobileNavOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/50 md:hidden"
          onClick={() => setMobileNavOpen(false)}
          aria-hidden="true"
        />
      )}

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
              {location.pathname.startsWith("/app/") && (
                <span>{location.pathname.slice(5)}</span>
              )}
            </div>

            <div className="flex items-center gap-2">
              <EngineChip />
              <ThemeToggle />
              <Button
                variant="ghost"
                size="icon"
                onClick={() => setAssistantOpen(!assistantOpen)}
              >
                <span>🤖</span>
              </Button>
            </div>
          </header>

          <main className="flex-1 overflow-auto">
            <RouteErrorBoundary>
              <Outlet />
            </RouteErrorBoundary>
          </main>
        </div>
      </div>

      {assistantOpen && <ConsoleAssistant onClose={() => setAssistantOpen(false)} />}
    </>
  )
}
