/**
 * Manifest-Driven Route Rendering (ADR-0561 Phase 1)
 *
 * Renders Routes for all panels in the manifest.
 * Replaces hardcoded panelRoutes(PANELS) with dynamic rendering from manifest.
 */

import { lazy, Suspense } from "react"
import { Route } from "react-router-dom"
import { Loader2 } from "lucide-react"
import type { PanelDescriptor, PanelElement } from "@/adapters/capabilities"
import PanelHost from "./PanelHost"

function LoadingFallback() {
  return (
    <div className="flex justify-center py-12">
      <Loader2 className="h-6 w-6 animate-spin" />
    </div>
  )
}

function renderPanelElement(panelId: string, element: PanelElement) {
  if (element.kind === "react-component") {
    // Lazy load from component (existing panels)
    // This requires a mapping; for now, we'll need to keep the component registry
    // as a fallback. Future: plugins provide their own components.
    return null // TODO: implement component registry lookup
  }

  if (element.kind === "react") {
    // Dynamic async import
    const Lazy = lazy(() => {
      // Parse the load path: "() => import('@/pages/foo')" → import('@/pages/foo')
      // For now, we'll just return a placeholder
      return Promise.resolve({
        default: () => <div>Panel {panelId} (react kind)</div>,
      })
    })
    return (
      <Suspense fallback={<LoadingFallback />}>
        <Lazy />
      </Suspense>
    )
  }

  if (element.kind === "iframe") {
    // Sandboxed iframe panel
    return <PanelHost src={element.src} sandbox={{}} />
  }

  if (element.kind === "skill-inspector") {
    // Generic Skill inspector (placeholder)
    return (
      <div className="p-4">
        <h2>Skill Inspector: {element.skill_id}</h2>
        <p className="text-muted-foreground text-sm">
          Skill: {element.skill_id} (to be implemented in Phase 4)
        </p>
      </div>
    )
  }

  if (element.kind === "plugin-inspector") {
    // Generic Plugin inspector (placeholder)
    return (
      <div className="p-4">
        <h2>Plugin Inspector: {element.plugin_id}</h2>
        <p className="text-muted-foreground text-sm">
          Plugin: {element.plugin_id} (to be implemented in Phase 3)
        </p>
      </div>
    )
  }

  return <div>Unknown panel kind: {(element as any).kind}</div>
}

export function manifestPanelRoutes(panels: PanelDescriptor[]) {
  return panels.map((panel) => {
    const element = renderPanelElement(panel.id, panel.element)

    if (!element) {
      // Fallback: render a placeholder
      return (
        <Route
          key={panel.id}
          path={panel.route}
          element={
            <div className="p-4">
              <h2>{panel.title}</h2>
              <p className="text-muted-foreground text-sm">
                Panel {panel.id} (element.kind: {(panel.element as any).kind})
              </p>
            </div>
          }
        />
      )
    }

    return (
      <Route key={panel.id} path={panel.route} element={element} />
    )
  })
}
