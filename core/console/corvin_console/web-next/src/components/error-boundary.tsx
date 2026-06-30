import * as React from "react";
import { AlertTriangle, ChevronDown, RefreshCw, RotateCw, WifiOff } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

function isChunkLoadError(e: Error): boolean {
  const msg = e.message ?? "";
  return (
    msg.includes("Failed to fetch dynamically imported module") ||
    msg.includes("Importing a module script failed") ||
    msg.includes("Loading chunk") ||
    msg.includes("ChunkLoadError")
  );
}

interface ChunkErrorBoundaryState {
  hasError: boolean;
}

/**
 * Outer boundary specifically for lazy-chunk load failures (network drops,
 * stale deployment). These can't be recovered by React state reset — only a
 * full page reload works.
 */
export class ChunkErrorBoundary extends React.Component<
  { children: React.ReactNode },
  ChunkErrorBoundaryState
> {
  state: ChunkErrorBoundaryState = { hasError: false };

  static getDerivedStateFromError(e: Error): ChunkErrorBoundaryState | null {
    if (isChunkLoadError(e)) return { hasError: true };
    return null; // let inner boundaries handle non-chunk errors
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="grid min-h-screen place-items-center">
          <div className="flex flex-col items-center gap-4 text-center max-w-sm px-4">
            <WifiOff className="h-8 w-8 text-muted-foreground" />
            <div className="space-y-1">
              <p className="font-medium">Page failed to load</p>
              <p className="text-sm text-muted-foreground">
                A network hiccup prevented the page from downloading. Reload to try again.
              </p>
            </div>
            <Button onClick={() => window.location.reload()}>
              <RefreshCw className="h-3.5 w-3.5" />
              Reload page
            </Button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

interface RouteErrorBoundaryState {
  error: Error | null;
  showDetails: boolean;
}

interface RouteErrorBoundaryProps {
  children: React.ReactNode;
  /** Optional label to identify the failing route in the fallback. */
  label?: string;
}

/**
 * Localised error-boundary for a single route. Catches render-time
 * exceptions so a bad page (e.g. shape-mismatch against a backend that
 * drifted) does not white-screen the whole console.
 *
 * Resets when the user clicks "Try again"; the sidebar nav stays
 * usable throughout because this boundary sits inside the route, not
 * around the whole AppLayout.
 */
export class RouteErrorBoundary extends React.Component<
  RouteErrorBoundaryProps,
  RouteErrorBoundaryState
> {
  state: RouteErrorBoundaryState = { error: null, showDetails: false };

  static getDerivedStateFromError(error: Error): RouteErrorBoundaryState | null {
    // Chunk-load errors (stale deployment — hash changed) must not be swallowed
    // here. Return null so the error propagates to the outer ChunkErrorBoundary
    // which triggers a hard reload. "Try again" (state reset) can never fix a
    // 404'd JS chunk — only a full page reload can.
    if (isChunkLoadError(error)) return null;
    return { error, showDetails: false };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    // Surface the trace in the browser console for debugging, but
    // never include user data — the message itself is the same string
    // the boundary renders.
    console.error(
      "[RouteErrorBoundary]",
      this.props.label ?? "(route)",
      error.message,
      info.componentStack,
    );
  }

  reset = () => this.setState({ error: null, showDetails: false });
  toggleDetails = () => this.setState((s) => ({ showDetails: !s.showDetails }));

  render() {
    if (this.state.error) {
      return (
        <div className="mx-auto max-w-3xl py-8">
          <Card className="border-destructive/40 bg-destructive/5">
            <CardContent className="space-y-3 py-6">
              <div className="flex items-start gap-3">
                <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-destructive" />
                <div className="space-y-1 flex-1">
                  <h2 className="font-serif text-lg">Something went wrong on this page.</h2>
                  <p className="text-sm text-muted-foreground">
                    The rest of the console is still usable — try a different section in the
                    sidebar, or reload this page.
                  </p>
                  <button
                    onClick={this.toggleDetails}
                    className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors mt-2"
                  >
                    <ChevronDown
                      className={`h-3 w-3 transition-transform duration-200 ${this.state.showDetails ? "" : "-rotate-90"}`}
                    />
                    {this.state.showDetails ? "Hide" : "Show"} technical details
                  </button>
                  {this.state.showDetails && (
                    <pre className="mt-1 max-h-32 overflow-auto rounded-md border border-border/60 bg-background/40 px-3 py-2 font-mono text-[11px] text-muted-foreground">
                      {String(this.state.error.message)}
                    </pre>
                  )}
                </div>
              </div>
              <div className="flex justify-end">
                <Button variant="outline" size="sm" onClick={this.reset}>
                  <RotateCw className="h-3.5 w-3.5" />
                  Try again
                </Button>
              </div>
            </CardContent>
          </Card>
        </div>
      );
    }
    return this.props.children;
  }
}
