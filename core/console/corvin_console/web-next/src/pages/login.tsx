import * as React from "react";
import { Loader2 } from "lucide-react";
import { PublicLayout, CorvinMark } from "@/components/layout";

export function LoginPage() {
  React.useEffect(() => {
    // Local-login: the backend creates a session on loopback and redirects
    // to /console/ — no token required.
    window.location.replace("/v1/console/auth/local-login");
  }, []);

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
