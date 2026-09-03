/**
 * Manifest-Driven Navigation Renderer (ADR-0561 Phase 1)
 *
 * Converts ConsoleManifest nav_groups to React components.
 * Renders sidebar from backend manifest instead of hardcoded NAV_GROUPS.
 */

import React from "react"
import { NavLink } from "react-router-dom"
import { ChevronDown } from "lucide-react"
import type { NavGroup, PanelDescriptor } from "@/adapters/capabilities"
import { cn } from "@/lib/utils"

interface ManifestNavProps {
  navGroups: NavGroup[]
  panels: PanelDescriptor[]
}

function getPanelIcon(iconName: string): React.ReactNode {
  // Map icon names to lucide-react components
  const iconMap: Record<string, React.ComponentType<{ className?: string }>> = {
    MessagesSquare: () => <span>💬</span>,
    LayoutDashboard: () => <span>📊</span>,
    Layers: () => <span>📚</span>,
    Cpu: () => <span>⚙️</span>,
    BookOpen: () => <span>📖</span>,
    Blocks: () => <span>🧩</span>,
    Settings: () => <span>⚙️</span>,
    Zap: () => <span>⚡</span>,
    Shield: () => <span>🛡️</span>,
  }

  const IconComponent = iconMap[iconName] || (() => <span>•</span>)
  return <IconComponent />
}

function NavItemLink({ panel, primary }: { panel: PanelDescriptor; primary?: boolean }) {
  return (
    <NavLink
      to={`/app/${panel.route}`}
      end={panel.route === panel.route}
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
      <span className="h-4 w-4 shrink-0">{getPanelIcon(panel.icon)}</span>
      {panel.title}
    </NavLink>
  )
}

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

function NavGroupSection({ group, panels }: { group: NavGroup; panels: PanelDescriptor[] }) {
  const [open, toggle] = useNavCollapse(group.id, group.defaultOpen)
  const isPrimary = !group.label

  // Find panels for this group
  const groupPanels = group.items
    .map((item) => panels.find((p) => p.id === item.panel_id))
    .filter(Boolean) as PanelDescriptor[]

  if (isPrimary) {
    return (
      <div className="flex flex-col gap-0.5">
        {groupPanels.map((panel) => (
          <NavItemLink key={panel.id} panel={panel} primary />
        ))}
      </div>
    )
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
          {groupPanels.map((panel) => (
            <NavItemLink key={panel.id} panel={panel} />
          ))}
        </div>
      )}
    </div>
  )
}

export function ManifestNavRenderer({ navGroups, panels }: ManifestNavProps) {
  if (!navGroups || navGroups.length === 0) {
    return <div className="text-muted-foreground text-xs px-3">No navigation available</div>
  }

  return (
    <nav className="flex flex-1 flex-col gap-4 overflow-y-auto">
      {navGroups.map((group, i) => (
        <React.Fragment key={group.id}>
          {i > 0 && <div className="mx-3 border-t border-border/60" />}
          <NavGroupSection group={group} panels={panels} />
        </React.Fragment>
      ))}
    </nav>
  )
}
