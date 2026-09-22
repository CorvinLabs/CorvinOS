/**
 * `toast()` for the control-plane pages (control-plane-*.tsx,
 * control-intent-router.tsx).
 *
 * Added 2026-09-22: those five pages (39298a96) imported `@/hooks/use-toast`
 * in the shadcn shape — `toast({ title, description, variant })` — and the
 * module never existed, so `vite build` failed for the whole console. The
 * console has no global <Toaster/>, so this draws a small self-removing notice
 * straight into the document instead of dropping the message: an install
 * failure the operator never sees is worse than a plain-looking one.
 * `src/hooks/useToast.ts` is the separate component-local hook; it has a
 * different API and is not touched.
 */
export interface ToastOptions {
  title: string;
  description?: string;
  variant?: "default" | "destructive";
  /** ms; 0 keeps it until clicked. */
  duration?: number;
}

const HOST_ID = "corvin-toast-host";

function host(): HTMLElement | null {
  if (typeof document === "undefined") return null;
  let el = document.getElementById(HOST_ID);
  if (!el) {
    el = document.createElement("div");
    el.id = HOST_ID;
    el.setAttribute("aria-live", "polite");
    el.style.cssText =
      "position:fixed;right:16px;bottom:16px;z-index:9999;display:flex;flex-direction:column;gap:8px;max-width:min(360px,calc(100vw - 32px))";
    document.body.appendChild(el);
  }
  return el;
}

export function toast({ title, description, variant = "default", duration = 5000 }: ToastOptions): void {
  const root = host();
  if (!root) return;
  const item = document.createElement("div");
  item.setAttribute("role", variant === "destructive" ? "alert" : "status");
  item.className =
    "rounded-md border px-3 py-2 text-sm shadow-md cursor-pointer " +
    (variant === "destructive"
      ? "bg-destructive text-destructive-foreground"
      : "bg-background text-foreground");
  const t = document.createElement("div");
  t.className = "font-medium";
  t.textContent = title;
  item.appendChild(t);
  if (description) {
    const d = document.createElement("div");
    d.className = "opacity-80";
    d.textContent = description;
    item.appendChild(d);
  }
  const remove = () => item.remove();
  item.addEventListener("click", remove);
  root.appendChild(item);
  if (duration > 0) setTimeout(remove, duration);
}

export function useToast() {
  return { toast };
}
