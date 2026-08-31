import { Link, useLocation, useNavigate } from "react-router-dom";
import { ArrowLeft, Compass, Home } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth";

/**
 * Friendly 404 with a Back-button + sensible recovery targets, instead
 * of the old `<Navigate to="/" replace />` that just whisked the user
 * to the landing-page and erased their browser history.
 */
export function NotFoundPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const { status } = useAuth();
  const homeTarget = status === "authenticated" ? "/app" : "/";
  return (
    <div className="mx-auto flex min-h-[60vh] max-w-xl flex-col items-center justify-center px-6 py-12 text-center">
      <Compass className="mb-4 h-10 w-10 text-accent" />
      <h1 className="font-serif text-2xl font-light tracking-tight">
        Page not found.
      </h1>
      <p className="mt-2 text-sm text-muted-foreground">
        The address <span className="font-mono">{location.pathname}</span> doesn't
        exist in the console. This may be a broken link or a typo.
      </p>
      <div className="mt-6 flex flex-wrap items-center justify-center gap-2">
        <Button variant="outline" onClick={() => navigate(-1)}>
          <ArrowLeft className="h-4 w-4" />
          Go back
        </Button>
        <Button asChild variant="accent">
          <Link to={homeTarget}>
            <Home className="h-4 w-4" />
            {status === "authenticated" ? "Dashboard" : "Home"}
          </Link>
        </Button>
      </div>
    </div>
  );
}
