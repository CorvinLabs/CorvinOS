import { useQuery } from "@tanstack/react-query";
import { BookOpen, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { getMemoryIndex } from "@/lib/api";
import { cn } from "@/lib/utils";

// The per-tenant memory view does not exist yet; the host developer's own
// auto-memory must never be shown to a tenant (S-1a). The page renders the
// server's explicit empty state and nothing else.
export function MemoryPage() {
  const indexQ = useQuery({
    queryKey: ["memory-index"],
    queryFn: ({ signal }) => getMemoryIndex(signal),
    refetchInterval: 30_000,
  });

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Memory</h1>
          <p className="text-sm text-muted-foreground">
            Persistent memory for this workspace
            {indexQ.data?.tenant_id ? (
              <> ({indexQ.data.tenant_id})</>
            ) : null}
          </p>
        </div>
        <Button
          size="icon"
          variant="ghost"
          className="h-8 w-8 text-muted-foreground"
          aria-label="Refresh"
          onClick={() => indexQ.refetch()}
          disabled={indexQ.isFetching}
        >
          <RefreshCw className={cn("h-4 w-4", indexQ.isFetching && "animate-spin")} />
        </Button>
      </div>

      {indexQ.isLoading && <Skeleton className="h-40 w-full rounded-lg" />}

      {indexQ.isError && (
        <p className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
          Memory is unavailable right now.
        </p>
      )}

      {indexQ.data && (
        <div className="flex flex-col items-center justify-center rounded-lg border border-dashed border-border py-16 text-center">
          <BookOpen className="mb-3 h-8 w-8 text-muted-foreground/40" />
          <p className="text-sm font-medium">Not available yet</p>
          <p className="mt-1 max-w-md text-xs text-muted-foreground">
            {indexQ.data.reason ?? "No memory entries."}
          </p>
        </div>
      )}
    </div>
  );
}
