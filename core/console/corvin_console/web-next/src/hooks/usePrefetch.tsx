/**
 * usePrefetch - Prefetch page chunks on hover/focus for instant navigation
 * Improves perceived performance when navigating between pages
 */

import { useEffect } from "react";

const PREFETCHABLE_PAGES = [
  "/app/dashboard",
  "/app/chat",
  "/app/workflows",
  "/app/personas",
  "/app/rag-hub",
  "/app/custom-provider",
];

/**
 * Prefetch a page's chunk when link is hovered or focused
 * Uses requestIdleCallback to avoid blocking user interactions
 */
export function usePrefetch(href: string | undefined) {
  useEffect(() => {
    if (!href || !PREFETCHABLE_PAGES.some(p => href.startsWith(p))) {
      return;
    }

    const prefetch = () => {
      // Dynamically import to trigger chunk loading in the background
      const pageName = href.split("/app/")[1]?.split("?")[0];
      if (pageName) {
        // Trigger dynamic import - browser caches the chunk
        import(`@/pages/${pageName}`).catch(() => {
          // Silently fail - prefetch is optional
        });
      }
    };

    // Use requestIdleCallback if available, otherwise use timeout
    if ("requestIdleCallback" in window) {
      const id = requestIdleCallback(prefetch, { timeout: 2000 });
      return () => cancelIdleCallback(id);
    } else {
      const timeout = setTimeout(prefetch, 100);
      return () => clearTimeout(timeout);
    }
  }, [href]);
}

/**
 * Link component wrapper that prefetches on hover
 */
export function PrefetchLink({
  href,
  children,
  ...props
}: React.PropsWithChildren<{
  href: string;
  [key: string]: unknown;
}>) {
  usePrefetch(href);

  return (
    <a href={href} {...props}>
      {children}
    </a>
  );
}
