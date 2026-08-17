/**
 * FrontendForge (ADR-0364 P6) — in-browser authoring of external Console panels.
 *
 * A panel is a small web page that runs INSIDE the Console (sandboxed iframe) and
 * shows data from your Console's own API. Here you pick a template, edit its HTML/JS,
 * watch it run live through the real panel host (ADR-0362), and download it. The live
 * preview IS a real panel run — "it previews" and "it works" are the same thing.
 *
 * Operator-only: behind the `frontend_forge` flag (default off). The preview iframe is
 * srcDoc (origin "null", allow-scripts only): unsaved code never gets same-origin
 * access. Installing a downloaded panel as a served surface is the loader's job (P7).
 */
import { useState, type ReactNode } from "react";
import { Wand2, Download, RotateCcw, Code2, Eye, FileCode2, BookOpen } from "lucide-react";
import PanelHost from "@/panels/PanelHost";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

interface Template {
  id: string;
  name: string;
  desc: string;
  code: string;
}

// Each template is a COMPLETE, working panel — the operator learns the API by editing
// one that already runs, not by staring at a blank editor. All use the same handshake:
// wait for `corvin:host:hello`, read ctx.baseUrl, call the same-origin Console API.
const TEMPLATES: Template[] = [
  {
    id: "welcome",
    name: "Welcome",
    desc: "Minimal — shows the handshake and the context the Console hands your panel.",
    code: `<!doctype html>
<html>
<head><meta charset="utf-8"><style>
  body { font: 14px system-ui, sans-serif; padding: 16px; color: #e2e8f0; }
</style></head>
<body>
  <h2>My first panel</h2>
  <div id="out">connecting to the Console…</div>
  <script>
    // 1. The Console sends "corvin:host:hello" with a context object.
    window.addEventListener("message", function (ev) {
      if (ev.data && ev.data.type === "corvin:host:hello") {
        var ctx = ev.data.ctx;
        document.getElementById("out").textContent =
          "Connected! API base = " + ctx.baseUrl + ", theme = " + ctx.theme;
      }
    });
    // 2. Tell the Console we are ready.
    parent.postMessage({ type: "corvin:panel:ready", protocolVersion: "1" }, "*");
  </script>
</body>
</html>`,
  },
  {
    id: "metric",
    name: "Metric card",
    desc: "A big number pulled live from a Console API — the building block of a dashboard.",
    code: `<!doctype html>
<html>
<head><meta charset="utf-8"><style>
  body { font: 14px system-ui, sans-serif; padding: 16px; color: #e2e8f0; }
  .n { font-size: 44px; font-weight: 700; }
  .l { color: #94a3b8; text-transform: uppercase; font-size: 11px; letter-spacing: .05em; }
</style></head>
<body>
  <div class="n" id="n">—</div>
  <div class="l">context-engineering turns</div>
  <script>
    window.addEventListener("message", function (ev) {
      if (!ev.data || ev.data.type !== "corvin:host:hello") return;
      // Call the Console API with the browser's own credentials.
      fetch(ev.data.ctx.baseUrl + "/vibe-engineering/traces", { credentials: "include" })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          var n = 0;
          (d.sessions || []).forEach(function (s) { n += (s.turns || []).length; });
          document.getElementById("n").textContent = n;
        });
    });
    parent.postMessage({ type: "corvin:panel:ready", protocolVersion: "1" }, "*");
  </script>
</body>
</html>`,
  },
  {
    id: "list",
    name: "Data list",
    desc: "Fetch rows from a Console API and render them — for logs, sessions, anything.",
    code: `<!doctype html>
<html>
<head><meta charset="utf-8"><style>
  body { font: 14px system-ui, sans-serif; padding: 16px; color: #e2e8f0; }
  .row { padding: 8px 10px; border: 1px solid #33415580; border-radius: 8px; margin: 6px 0; }
  .k { font-family: ui-monospace, monospace; font-size: 12px; color: #94a3b8; }
</style></head>
<body>
  <h3>Recent sessions</h3>
  <div id="list">loading…</div>
  <script>
    window.addEventListener("message", function (ev) {
      if (!ev.data || ev.data.type !== "corvin:host:hello") return;
      fetch(ev.data.ctx.baseUrl + "/vibe-engineering/traces", { credentials: "include" })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          var el = document.getElementById("list");
          el.innerHTML = "";
          (d.sessions || []).slice(0, 10).forEach(function (s) {
            var div = document.createElement("div");
            div.className = "row";
            var k = document.createElement("span");
            k.className = "k";
            k.textContent = s.session + " · " + (s.turns || []).length + " turns";
            div.appendChild(k);
            el.appendChild(div);
          });
        });
    });
    parent.postMessage({ type: "corvin:panel:ready", protocolVersion: "1" }, "*");
  </script>
</body>
</html>`,
  },
];

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

/** Backwards-compat export used by tests. */
export const STARTER_PANEL = TEMPLATES[0].code;

function Step({ n, title, children }: { n: number; title: string; children: ReactNode }) {
  return (
    <div className="flex items-start gap-2.5">
      <div className="grid place-items-center h-5 w-5 rounded-full bg-primary/15 text-primary text-[11px] font-semibold shrink-0 mt-0.5">
        {n}
      </div>
      <div className="text-sm">
        <span className="font-medium">{title}</span>{" "}
        <span className="text-muted-foreground">{children}</span>
      </div>
    </div>
  );
}

export default function FrontendForgePage() {
  const [tplId, setTplId] = useState<string>(TEMPLATES[0].id);
  const [code, setCode] = useState<string>(TEMPLATES[0].code);
  const activeTpl = TEMPLATES.find((t) => t.id === tplId) ?? TEMPLATES[0];
  const dirty = code !== activeTpl.code;

  const pick = (t: Template) => { setTplId(t.id); setCode(t.code); };

  return (
    <div className="p-6 max-w-[1400px] mx-auto space-y-6">
      {/* header — what + why */}
      <div className="flex items-start gap-3">
        <div className="grid place-items-center h-10 w-10 rounded-lg bg-primary/10 text-primary shrink-0">
          <Wand2 className="h-5 w-5" />
        </div>
        <div>
          <h1 className="text-xl font-semibold tracking-tight">FrontendForge</h1>
          <p className="text-sm text-muted-foreground mt-0.5 max-w-2xl">
            Build your own Console panels. A <strong>panel</strong> is a small, sandboxed
            web page that runs inside the Console and shows data from your own API —
            a custom metric, a live list, a mini-dashboard. Start from a template,
            tweak it, watch it run live, and download it.
          </p>
        </div>
      </div>

      {/* workflow */}
      <Card className="p-4">
        <div className="grid gap-2.5 sm:grid-cols-2 lg:grid-cols-4">
          <Step n={1} title="Pick a template">a working example to start from.</Step>
          <Step n={2} title="Edit the code">change the HTML/JS on the left.</Step>
          <Step n={3} title="Watch the preview">it runs live, sandboxed, on the right.</Step>
          <Step n={4} title="Download">save <code>panel.html</code> to install later.</Step>
        </div>
      </Card>

      {/* template picker */}
      <div>
        <div className="flex items-center gap-2 mb-2 text-sm font-medium text-muted-foreground">
          <FileCode2 className="h-4 w-4" /> Templates
        </div>
        <div className="grid gap-3 sm:grid-cols-3">
          {TEMPLATES.map((t) => (
            <button
              key={t.id}
              onClick={() => pick(t)}
              className={
                "text-left rounded-lg border p-3 transition-colors " +
                (t.id === tplId
                  ? "border-primary bg-primary/5"
                  : "border-border hover:bg-muted/40")
              }
            >
              <div className="font-medium text-sm">{t.name}</div>
              <div className="text-xs text-muted-foreground mt-1">{t.desc}</div>
            </button>
          ))}
        </div>
      </div>

      {/* toolbar */}
      <div className="flex items-center justify-between gap-3">
        <div className="text-sm text-muted-foreground">
          Editing <span className="font-medium text-foreground">{activeTpl.name}</span>
          {dirty && <span className="ml-1.5 text-xs">(edited)</span>}
        </div>
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" onClick={() => setCode(activeTpl.code)} disabled={!dirty}>
            <RotateCcw className="h-4 w-4 mr-1.5" /> Reset
          </Button>
          <Button size="sm" onClick={() => downloadPanel(code, `${activeTpl.id}-panel.html`)}>
            <Download className="h-4 w-4 mr-1.5" /> Download panel.html
          </Button>
        </div>
      </div>

      {/* editor + preview */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5" style={{ minHeight: 460 }}>
        <Card className="flex flex-col overflow-hidden">
          <div className="flex items-center gap-2 px-4 py-2.5 border-b bg-muted/40 text-sm font-medium">
            <Code2 className="h-4 w-4 text-muted-foreground" /> Panel source
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
            <PanelHost srcDoc={code} sandbox="allow-scripts" />
          </div>
        </Card>
      </div>

      {/* help — what can my panel do */}
      <Card className="p-4">
        <div className="flex items-center gap-2 mb-2 text-sm font-medium">
          <BookOpen className="h-4 w-4 text-muted-foreground" /> What can my panel do?
        </div>
        <ul className="text-sm text-muted-foreground space-y-1.5 list-disc pl-5">
          <li>
            Wait for <code>corvin:host:hello</code>, then read <code>ctx.baseUrl</code>,{" "}
            <code>ctx.theme</code> and <code>ctx.tenantId</code>.
          </li>
          <li>
            Call any Console API under <code>ctx.baseUrl</code> (e.g.{" "}
            <code>/vibe-engineering/traces</code>, <code>/sessions</code>) with{" "}
            <code>credentials: "include"</code> — no token needed, it uses your session.
          </li>
          <li>
            Ask the Console to navigate (<code>corvin:panel:navigate</code>) or resize
            the frame (<code>corvin:panel:resize</code>). It cannot touch the Console's
            DOM or cookies — it is sandboxed.
          </li>
          <li>
            Download it here; installing a panel as a permanent Console surface arrives
            with the plugin loader.
          </li>
        </ul>
      </Card>
    </div>
  );
}
