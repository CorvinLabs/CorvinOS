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
import PanelHost from "@/panels/PanelHost";

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

  return (
    <div className="p-4">
      <h1 className="text-lg font-semibold mb-1">FrontendForge</h1>
      <p className="text-sm text-muted-foreground mb-4">
        Author an external Console panel. The live preview runs through the real
        panel host, sandboxed. Download it when ready; the loader (P7) will mount
        saved panels.
      </p>
      <div className="grid grid-cols-2 gap-4" style={{ minHeight: 480 }}>
        <div className="flex flex-col">
          <label className="text-xs font-medium mb-1">Panel HTML</label>
          <textarea
            className="flex-1 font-mono text-xs border rounded p-2"
            spellCheck={false}
            value={code}
            onChange={(e) => setCode(e.target.value)}
            aria-label="Panel HTML source"
          />
          <button
            className="mt-2 self-start rounded border px-3 py-1 text-sm"
            onClick={() => downloadPanel(code)}
          >
            Download panel.html
          </button>
        </div>
        <div className="flex flex-col">
          <label className="text-xs font-medium mb-1">Live preview (sandboxed)</label>
          <div className="flex-1 border rounded overflow-auto">
            {/* srcDoc → origin "null", allow-scripts only: unsaved code stays isolated. */}
            <PanelHost srcDoc={code} sandbox="allow-scripts" theme="light" />
          </div>
        </div>
      </div>
    </div>
  );
}
