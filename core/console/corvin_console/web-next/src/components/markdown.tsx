import * as React from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { AlertCircle, Check, Copy, ExternalLink, FileText } from "lucide-react";
import "highlight.js/styles/github-dark.css";
import { cn } from "@/lib/utils";

// ── Mermaid diagram block ──────────────────────────────────────────────────

let _mermaidId = 0;

function MermaidBlock({ code }: { code: string }) {
  const id = React.useRef(`mermaid-${++_mermaidId}`).current;
  const ref = React.useRef<HTMLDivElement>(null);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (!ref.current) return;
    let cancelled = false;
    import("mermaid").then(({ default: mermaid }) => {
      mermaid.initialize({
        startOnLoad: false,
        theme: "dark",
        // "strict": neutralize HTML in node labels and disable click/href
        // directives. Assistant/workflow output is LLM/tool-derived (a delegated
        // worker can surface untrusted web/file content), so a crafted ```mermaid
        // block must not be able to execute script in the console origin.
        securityLevel: "strict",
        fontFamily: "inherit",
      });
      mermaid.render(id, code).then(({ svg }) => {
        if (!cancelled && ref.current) {
          ref.current.innerHTML = svg;
          setError(null);
        }
      }).catch((e) => {
        if (!cancelled) setError(String(e?.message ?? e));
      });
    });
    return () => { cancelled = true; };
  }, [code, id]);

  if (error) {
    return (
      <div className="my-3 flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
        <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        <span>Diagram error: {error}</span>
      </div>
    );
  }
  return <div ref={ref} className="my-3 overflow-x-auto [&>svg]:max-w-full [&>svg]:mx-auto [&>svg]:block" />;
}

const PDF_EXT = /\.pdf($|[?#])/i;

interface MarkdownProps {
  text: string;
  /** Compact mode shrinks spacing for chat bubbles. */
  compact?: boolean;
  className?: string;
}

/**
 * GitHub-flavoured Markdown renderer for chat messages.
 *
 * - Code blocks → highlight.js (github-dark) + a "copy" button.
 * - Images → rendered inline with a max height + lazy loading.
 * - PDF links → styled with an icon + open-in-new-tab affordance.
 * - Tables → scroll-on-overflow + zebra striping.
 * - Links → external links get a tab-out icon.
 * - Headings, lists, blockquotes, hr → tasteful typography that matches
 *   the Corvin palette via Tailwind classes (NOT @tailwindcss/typography
 *   so we keep full control over spacing and colour).
 */
export function Markdown({ text, compact, className }: MarkdownProps) {
  const components: Components = React.useMemo(
    () => ({
      h1: ({ children }) => (
        <h1 className="mt-4 mb-2 font-serif text-2xl font-light tracking-tight first:mt-0">
          {children}
        </h1>
      ),
      h2: ({ children }) => (
        <h2 className="mt-4 mb-2 font-serif text-xl font-light tracking-tight first:mt-0">
          {children}
        </h2>
      ),
      h3: ({ children }) => (
        <h3 className="mt-3 mb-1.5 font-serif text-lg font-medium first:mt-0">{children}</h3>
      ),
      h4: ({ children }) => (
        <h4 className="mt-3 mb-1 font-medium first:mt-0">{children}</h4>
      ),
      p: ({ children }) => (
        <p className={cn(compact ? "my-1.5" : "my-2", "leading-relaxed first:mt-0 last:mb-0")}>
          {children}
        </p>
      ),
      ul: ({ children }) => (
        <ul className={cn(compact ? "my-1.5" : "my-2", "list-disc space-y-1 pl-5 marker:text-muted-foreground")}>
          {children}
        </ul>
      ),
      ol: ({ children }) => (
        <ol className={cn(compact ? "my-1.5" : "my-2", "list-decimal space-y-1 pl-5 marker:text-muted-foreground")}>
          {children}
        </ol>
      ),
      li: ({ children }) => <li className="leading-relaxed">{children}</li>,
      blockquote: ({ children }) => (
        <blockquote className="my-3 border-l-2 border-accent/60 pl-3 italic text-muted-foreground">
          {children}
        </blockquote>
      ),
      hr: () => <hr className="my-4 border-border/60" />,
      strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
      em: ({ children }) => <em className="italic">{children}</em>,
      table: ({ children }) => (
        <div className="my-3 overflow-x-auto rounded-md border border-border/60">
          <table className="w-full text-sm">{children}</table>
        </div>
      ),
      thead: ({ children }) => <thead className="bg-muted/40 text-left text-xs uppercase tracking-wider text-muted-foreground">{children}</thead>,
      tbody: ({ children }) => <tbody className="divide-y divide-border/60">{children}</tbody>,
      th: ({ children }) => <th className="px-3 py-2 font-medium">{children}</th>,
      td: ({ children }) => <td className="px-3 py-2 align-top">{children}</td>,
      a: ({ href, children }) => {
        const url = href || "";
        const isPdf = PDF_EXT.test(url);
        const isExternal = /^https?:\/\//i.test(url);
        return (
          <a
            href={url}
            target="_blank"
            rel="noreferrer"
            className={cn(
              "inline-flex items-center gap-1 text-accent underline-offset-2 hover:underline",
              isPdf && "rounded-md border border-border/60 bg-card px-2 py-1 no-underline hover:bg-muted/60",
            )}
          >
            {isPdf && <FileText className="h-3.5 w-3.5" />}
            {children}
            {isExternal && !isPdf && <ExternalLink className="h-3 w-3 opacity-70" />}
          </a>
        );
      },
      img: ({ src, alt }) => {
        const s = typeof src === "string" ? src : "";
        if (!s) return null;
        return (
          <span className="my-2 block">
            <img
              src={s}
              alt={alt ?? ""}
              loading="lazy"
              className="max-h-[28rem] rounded-md border border-border/60 bg-card object-contain"
            />
            {alt && <span className="mt-1 block text-[11px] italic text-muted-foreground">{alt}</span>}
          </span>
        );
      },
      code: (props) => {
        const { inline, className, children, ...rest } = props as {
          inline?: boolean;
          className?: string;
          children?: React.ReactNode;
        };
        if (inline) {
          return (
            <code
              className={cn(
                "rounded bg-muted/60 px-1.5 py-0.5 font-mono text-[0.85em]",
                className,
              )}
              {...rest}
            >
              {children}
            </code>
          );
        }
        return (
          <code className={cn("font-mono text-[12.5px]", className)} {...rest}>
            {children}
          </code>
        );
      },
      pre: ({ children }) => {
        // children is a single <code className="language-xxx">…</code>
        const code = extractCodeText(children);
        const lang = extractLanguage(children);
        if (lang === "mermaid") return <MermaidBlock code={code.trim()} />;
        return <CodeBlock code={code} lang={lang}>{children}</CodeBlock>;
      },
    }),
    [compact],
  );

  return (
    <div className={cn("markdown-body", className)}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]} components={components}>
        {text}
      </ReactMarkdown>
    </div>
  );
}

function CodeBlock({ code, lang, children }: { code: string; lang: string | null; children: React.ReactNode }) {
  const [copied, setCopied] = React.useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard may be denied */
    }
  };
  return (
    <div className="group relative my-3 overflow-hidden rounded-md border border-border/60 bg-muted">
      <div className="flex items-center justify-between border-b border-border/40 bg-card/30 px-3 py-1.5 text-[10px] uppercase tracking-wider text-muted-foreground">
        <span className="font-mono">{lang ?? "code"}</span>
        <button
          onClick={copy}
          className="flex items-center gap-1 rounded px-1.5 py-0.5 opacity-0 transition-opacity hover:bg-muted/40 hover:text-foreground group-hover:opacity-100"
        >
          {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
          {copied ? "copied" : "copy"}
        </button>
      </div>
      <pre className="overflow-x-auto px-3 py-2 text-[12.5px] leading-relaxed text-muted-foreground">
        {children}
      </pre>
    </div>
  );
}

// Helpers to extract code text + language from react-markdown's <pre><code> wrapper
function extractCodeText(node: React.ReactNode): string {
  if (typeof node === "string") return node;
  if (Array.isArray(node)) return node.map(extractCodeText).join("");
  if (React.isValidElement(node)) {
    const children = (node.props as { children?: React.ReactNode }).children;
    return extractCodeText(children);
  }
  return "";
}

function extractLanguage(node: React.ReactNode): string | null {
  if (React.isValidElement(node)) {
    const className = (node.props as { className?: string }).className ?? "";
    const m = /language-([\w-]+)/.exec(className);
    if (m) return m[1];
  }
  if (Array.isArray(node)) {
    for (const c of node) {
      const l = extractLanguage(c);
      if (l) return l;
    }
  }
  return null;
}
