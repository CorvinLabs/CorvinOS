import * as React from "react";
import { Loader2 } from "lucide-react";
import { useLocation } from "react-router-dom";
// PublicLayout, CorvinMark: temporary fallback (ADR-0561: landing/login to be redesigned)
const PublicLayout = ({ children }: { children: React.ReactNode }) => <div>{children}</div>;
const CorvinMark = (props: React.HTMLAttributes<HTMLDivElement>) => <div {...props}>🔷</div>;

export function LoginPage() {
  const location = useLocation();
  React.useEffect(() => {
    // Local-login: the backend creates a session on loopback and redirects
    // back to where the operator was going (`next`, a same-origin console
    // path validated server-side) — or to /console/ when there is none.
    const from = (location.state as { from?: string } | null)?.from;
    const next = typeof from === "string" && from.startsWith("/app") ? `/console${from}` : "";
    window.location.replace(
      "/v1/console/auth/local-login" + (next ? `?next=${encodeURIComponent(next)}` : ""),
    );
  }, [location.state]);

  return (
    <PublicLayout>
      <div className="mx-auto flex min-h-[70vh] w-full max-w-md flex-col items-center justify-center gap-4 px-6 py-12">
        <CorvinMark className="h-10 w-10" />
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        <p className="text-sm text-muted-foreground">Opening session…</p>
      </div>
    </PublicLayout>
  );
}
