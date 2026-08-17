/**
 * FrontendForge (ADR-0364 P6) — in-browser authoring of external Console panels.
 *
 * An operator writes a panel's HTML/JS on the left and sees it running LIVE on the
 * right, embedded through the same PanelHost the real registry uses (ADR-0362 P4) —
 * so "it previews" and "it works as a real panel" are the same thing. The preview
 * iframe is srcDoc (origin "null", fully sandboxed, allow-scripts only): unsaved
 * in-editor code never gets same-origin access. Saving/deploying a panel as a served
 * asset is the loader's job (P7); here the operator authors + downloads.
 *
 * Operator-only: the panel is registered behind the `frontend_forge` flag (default
 * off), so it does not appear on a normal install until an operator turns it on.
 * A textarea editor is the honest MVP; a Monaco upgrade is a UX follow-up, not a
 * blocker (Monaco is not a project dependency today).
 */
import { useState } from "react";
import { Wand2, Download, RotateCcw, Code2, Eye } from "lucide-react";
import PanelHost from "@/panels/PanelHost";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

export const STARTER_PANEL = `<!doctype html>
<html>
<head><meta charset="utf-8"><style>
  body { font: 14px system-ui, sans-serif; padding: 12px; }
</style></head>
<body>
  <h1>My panel</h1>
  <div id="out">connecting to Console…</div>
  <script>
    // A panel talks to the Console only via postMessage (ADR-0362).
    window.addEventListener("message", function (ev) {
      if (ev.data && ev.data.type === "corvin:host:hello") {
        document.getElementById("out").textContent =
          "connected — API base: " + ev.data.ctx.baseUrl +
          " · theme: " + ev.data.ctx.theme;
      }
    });
    // Announce readiness to the host.
    parent.postMessage({ type: "corvin:panel:ready", protocolVersion: "1" }, "*");
  </script>
</body>
</html>`;

/** Trigger a browser download of the authored panel HTML. Exported for testing. */
export function downloadPanel(html: string, filename = "panel.html"): void {
  const blob = new Blob([html], { type: "text/html" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export default function FrontendForgePage() {
  const [code, setCode] = useState<string>(STARTER_PANEL);

  const dirty = code !== STARTER_PANEL;
  return (
    <div className="p-6 max-w-[1400px] mx-auto">
      {/* header */}
      <div className="flex items-start justify-between gap-4 mb-6">
        <div className="flex items-start gap-3">
          <div className="grid place-items-center h-10 w-10 rounded-lg bg-primary/10 text-primary shrink-0">
            <Wand2 className="h-5 w-5" />
          </div>
          <div>
            <h1 className="text-xl font-semibold tracking-tight">FrontendForge</h1>
            <p className="text-sm text-muted-foreground mt-0.5 max-w-xl">
              Author an external Console panel and see it running live through the real,
              sandboxed panel host. Download it when ready — the plugin loader mounts saved panels.
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <Button variant="ghost" size="sm" onClick={() => setCode(STARTER_PANEL)} disabled={!dirty}>
            <RotateCcw className="h-4 w-4 mr-1.5" /> Reset
          </Button>
          <Button size="sm" onClick={() => downloadPanel(code)}>
            <Download className="h-4 w-4 mr-1.5" /> Download panel.html
          </Button>
        </div>
      </div>

      {/* editor + preview */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5" style={{ minHeight: 540 }}>
        <Card className="flex flex-col overflow-hidden">
          <div className="flex items-center gap-2 px-4 py-2.5 border-b bg-muted/40 text-sm font-medium">
            <Code2 className="h-4 w-4 text-muted-foreground" /> Panel source
            {dirty && <span className="ml-auto text-xs text-muted-foreground">edited</span>}
          </div>
          <textarea
            className="flex-1 font-mono text-xs leading-relaxed p-4 bg-transparent text-foreground resize-none outline-none focus:ring-0"
            spellCheck={false}
            value={code}
            onChange={(e) => setCode(e.target.value)}
            aria-label="Panel HTML source"
          />
        </Card>

        <Card className="flex flex-col overflow-hidden">
          <div className="flex items-center gap-2 px-4 py-2.5 border-b bg-muted/40 text-sm font-medium">
            <Eye className="h-4 w-4 text-muted-foreground" /> Live preview
            <span className="ml-auto inline-flex items-center gap-1.5 text-xs text-muted-foreground">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" /> sandboxed
            </span>
          </div>
          <div className="flex-1 overflow-auto bg-background">
            {/* srcDoc → origin "null", allow-scripts only: unsaved code stays isolated.
                No theme prop → the preview follows the live Console theme (PanelHost). */}
            <PanelHost srcDoc={code} sandbox="allow-scripts" />
          </div>
        </Card>
      </div>
    </div>
  );
}
