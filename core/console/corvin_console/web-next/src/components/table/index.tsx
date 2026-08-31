/**
 * DataTable — navigable table widget with sort/filter/pagination.
 * Used both in the Compute dashboard (Artifact Viewer) and in the
 * Workflow-run viewer.
 *
 * Server-side sorting/filtering/pagination: every change triggers an
 * API call. No client-side loading of all rows.
 */
import * as React from "react";
import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  Download,
  Loader2,
  Search,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

// ── Types ─────────────────────────────────────────────────────────────────

export interface TableColumn {
  name: string;
  type: string;
}

export interface TablePage {
  schema: TableColumn[];
  rows: Record<string, unknown>[];
  total_rows: number;
  page: number;
  per_page: number;
  total_pages: number;
  sort_col: string | null;
  sort_dir: "asc" | "desc";
  filter_text: string;
  pii_redacted: string[];
  all_columns?: string[];
  filename?: string;
  rows_returned?: number;
}

export interface DataTableFetchParams {
  page: number;
  per_page: number;
  sort_col: string | null;
  sort_dir: "asc" | "desc";
  filter: string;
  cols?: string[];
}

// ── Helpers ───────────────────────────────────────────────────────────────

function CellValue({ value }: { value: unknown }) {
  if (value === null || value === undefined)
    return <span className="text-muted-foreground/50 italic">null</span>;
  if (value === "[REDACTED]")
    return (
      <span className="rounded bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-400 px-1 text-[10px] font-mono">
        [REDACTED]
      </span>
    );
  const str = String(value);
  const isNumeric = typeof value === "number";
  return (
    <span className={cn("font-mono text-xs", isNumeric && "tabular-nums text-right block")}>
      {isNumeric ? Number.isInteger(value) ? value.toLocaleString() : value.toFixed(4) : str}
    </span>
  );
}

// ── Main DataTable component ──────────────────────────────────────────────

export interface DataTableProps {
  /** Fetches a page. Must return TablePage on success. */
  fetchPage: (params: DataTableFetchParams) => Promise<TablePage>;
  /** Shown in the header. */
  title?: string;
  /** URL to download the full file. */
  downloadUrl?: string;
  /** Initial page size. */
  defaultPerPage?: number;
  /** If true, show compact inline variant (no pagination controls visible). */
  compact?: boolean;
  /** Extra CSS class on the outer wrapper. */
  className?: string;
}

export function DataTable({
  fetchPage,
  title,
  downloadUrl,
  defaultPerPage = 50,
  compact = false,
  className,
}: DataTableProps) {
  const [data, setData] = React.useState<TablePage | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);

  // Navigation state
  const [page, setPage] = React.useState(1);
  const [perPage, setPerPage] = React.useState(compact ? 10 : defaultPerPage);
  const [sortCol, setSortCol] = React.useState<string | null>(null);
  const [sortDir, setSortDir] = React.useState<"asc" | "desc">("asc");
  const [filter, setFilter] = React.useState("");
  const [filterInput, setFilterInput] = React.useState("");

  // Debounce filter input
  React.useEffect(() => {
    const t = setTimeout(() => {
      setFilter(filterInput);
      setPage(1);
    }, 350);
    return () => clearTimeout(t);
  }, [filterInput]);

  // Keep fetchPage in a ref so we can include a stable reference in the effect
  // without triggering re-fetches every time the parent re-renders.
  const fetchPageRef = React.useRef(fetchPage);
  React.useEffect(() => { fetchPageRef.current = fetchPage; }, [fetchPage]);

  // Fetch whenever params change
  React.useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchPageRef.current({ page, per_page: perPage, sort_col: sortCol, sort_dir: sortDir, filter })
      .then((d) => { if (!cancelled) { setData(d); setLoading(false); } })
      .catch((e) => { if (!cancelled) { setError(e?.message ?? String(e)); setLoading(false); } });
    return () => { cancelled = true; };
  }, [page, perPage, sortCol, sortDir, filter]);

  function handleSort(col: string) {
    if (sortCol === col) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortCol(col);
      setSortDir("asc");
    }
    setPage(1);
  }

  const columns = data?.schema ?? [];
  const rows = data?.rows ?? [];
  const totalRows = data?.total_rows ?? 0;
  const totalPages = data?.total_pages ?? 1;
  const piiCols = new Set(data?.pii_redacted ?? []);

  return (
    <div className={cn("rounded-xl border border-border bg-card overflow-hidden", className)}>
      {/* ── Header ── */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-border bg-muted/20">
        {title && (
          <span className="text-xs font-medium text-foreground truncate mr-auto">
            {title}
          </span>
        )}
        {!title && <span className="mr-auto" />}

        {/* Row count */}
        {data && (
          <span className="text-[10px] text-muted-foreground tabular-nums shrink-0">
            {totalRows.toLocaleString()} rows
            {data.pii_redacted.length > 0 && (
              <span className="ml-1 text-amber-600">· {data.pii_redacted.length} PII columns redacted</span>
            )}
          </span>
        )}

        {/* Filter input */}
        {!compact && (
          <div className="relative flex items-center">
            <Search className="absolute left-2 h-3 w-3 text-muted-foreground pointer-events-none" />
            <input
              value={filterInput}
              onChange={(e) => setFilterInput(e.target.value)}
              placeholder="Search…"
              className="h-6 pl-6 pr-6 text-xs border border-border rounded bg-background w-40 focus:outline-none focus:ring-1 focus:ring-accent"
            />
            {filterInput && (
              <button
                onClick={() => { setFilterInput(""); }}
                className="absolute right-1.5 text-muted-foreground hover:text-foreground"
              >
                <X className="h-3 w-3" />
              </button>
            )}
          </div>
        )}

        {/* Per-page selector */}
        {!compact && (
          <select
            value={perPage}
            onChange={(e) => { setPerPage(Number(e.target.value)); setPage(1); }}
            className="h-6 text-xs border border-border rounded bg-background px-1 focus:outline-none"
          >
            {[10, 25, 50, 100, 250, 500].map((n) => (
              <option key={n} value={n}>{n} / page</option>
            ))}
          </select>
        )}

        {downloadUrl && (
          <a href={downloadUrl} download
            className="h-6 px-2 flex items-center gap-1 text-xs border border-border rounded bg-background hover:bg-muted/30 text-muted-foreground hover:text-foreground">
            <Download className="h-3 w-3" />
          </a>
        )}
      </div>

      {/* ── Table ── */}
      <div className="overflow-auto" style={{ maxHeight: compact ? "240px" : "480px" }}>
        {loading && !data ? (
          <div className="flex items-center justify-center py-8 gap-2 text-xs text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />Loading data…
          </div>
        ) : error ? (
          <div className="px-4 py-4 text-xs text-destructive">{error}</div>
        ) : (
          <table className="w-full text-xs border-collapse">
            <thead>
              <tr className="border-b border-border bg-muted/30 sticky top-0 z-10">
                {columns.map((col) => (
                  <th
                    key={col.name}
                    onClick={() => handleSort(col.name)}
                    className={cn(
                      "px-3 py-2 text-left font-medium text-muted-foreground cursor-pointer",
                      "hover:text-foreground hover:bg-muted/50 whitespace-nowrap select-none",
                      piiCols.has(col.name) && "text-amber-600",
                    )}
                  >
                    <div className="flex items-center gap-1">
                      <span className="truncate max-w-[120px]" title={col.name}>
                        {col.name}
                      </span>
                      <span className="text-[10px] text-muted-foreground/50">{col.type.split(" ")[0]}</span>
                      {sortCol === col.name ? (
                        sortDir === "asc"
                          ? <ArrowUp className="h-3 w-3 text-accent shrink-0" />
                          : <ArrowDown className="h-3 w-3 text-accent shrink-0" />
                      ) : (
                        <ArrowUpDown className="h-3 w-3 text-muted-foreground/40 shrink-0" />
                      )}
                    </div>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, i) => (
                <tr key={i} className={cn(
                  "border-b border-border/50 hover:bg-muted/20 transition-colors",
                  loading && "opacity-50",
                )}>
                  {columns.map((col) => (
                    <td key={col.name} className="px-3 py-1.5 max-w-[200px] truncate">
                      <CellValue value={row[col.name]} />
                    </td>
                  ))}
                </tr>
              ))}
              {rows.length === 0 && !loading && (
                <tr>
                  <td colSpan={columns.length} className="text-center py-8 text-xs text-muted-foreground">
                    {filter ? "No results for this search." : "No data."}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        )}
      </div>

      {/* ── Pagination ── */}
      {!compact && data && totalPages > 1 && (
        <div className="flex items-center justify-between px-3 py-2 border-t border-border bg-muted/10">
          <span className="text-[10px] text-muted-foreground tabular-nums">
            Page {page} / {totalPages} · Row {((page - 1) * perPage + 1).toLocaleString()}–
            {Math.min(page * perPage, totalRows).toLocaleString()} of {totalRows.toLocaleString()}
          </span>
          <div className="flex items-center gap-0.5">
            <Button variant="ghost" size="icon" className="h-6 w-6"
              disabled={page <= 1} onClick={() => setPage(1)}>
              <ChevronsLeft className="h-3 w-3" />
            </Button>
            <Button variant="ghost" size="icon" className="h-6 w-6"
              disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
              <ChevronLeft className="h-3 w-3" />
            </Button>
            <span className="w-12 text-center">
              <input
                type="number"
                min={1}
                max={totalPages}
                value={page}
                onChange={(e) => {
                  const v = parseInt(e.target.value);
                  if (!isNaN(v) && v >= 1 && v <= totalPages) setPage(v);
                }}
                className="w-full text-center text-xs border border-border rounded bg-background h-6 focus:outline-none focus:ring-1 focus:ring-accent [appearance:textfield]"
              />
            </span>
            <Button variant="ghost" size="icon" className="h-6 w-6"
              disabled={page >= totalPages} onClick={() => setPage((p) => p + 1)}>
              <ChevronRight className="h-3 w-3" />
            </Button>
            <Button variant="ghost" size="icon" className="h-6 w-6"
              disabled={page >= totalPages} onClick={() => setPage(totalPages)}>
              <ChevronsRight className="h-3 w-3" />
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

// ── TableCard — compact inline version for the workflow log ──────────────

export interface TableEvent {
  table_id: string;
  node_id?: string;
  filename: string;
  mime_type: string;
  row_count?: number | null;
  size_bytes: number;
  src: string;
  ts: number;
}

export interface TableCardProps {
  event: TableEvent;
  /** If provided, opens a full DataTable on click. */
  onExpand?: (event: TableEvent) => void;
}

export function TableCard({ event, onExpand }: TableCardProps) {
  const sizeLabel = event.size_bytes > 1_048_576
    ? `${(event.size_bytes / 1_048_576).toFixed(1)} MB`
    : `${Math.round(event.size_bytes / 1024)} KB`;

  return (
    <div className="my-2 rounded-lg border border-border overflow-hidden bg-card">
      <div className="px-3 py-2 bg-muted/20 border-b border-border flex items-center gap-2 text-xs">
        <span className="text-base">📋</span>
        <span className="font-medium truncate">{event.filename}</span>
        <span className="text-muted-foreground shrink-0">
          {event.row_count != null ? `${event.row_count.toLocaleString()} rows · ` : ""}{sizeLabel}
        </span>
        {event.node_id && (
          <span className="ml-auto text-muted-foreground shrink-0">{event.node_id}</span>
        )}
      </div>
      <div className="px-3 py-2 flex items-center gap-2">
        {onExpand && (
          <button
            onClick={() => onExpand(event)}
            className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground border border-border rounded px-2 py-1 hover:bg-muted/30"
          >
            🔍 Open table
          </button>
        )}
        <a
          href={event.src.replace("/tables/", "/tables/").replace("tables", "tables").replace(/\/tables\/(.+)$/, "/tables/$1") + "?format=csv"}
          download={event.filename}
          className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground border border-border rounded px-2 py-1 hover:bg-muted/30"
        >
          <Download className="h-3 w-3" /> Download
        </a>
      </div>
    </div>
  );
}

// ── Full-screen table overlay ─────────────────────────────────────────────

export interface TableOverlayProps {
  event: TableEvent;
  fetchPage: (params: DataTableFetchParams) => Promise<TablePage>;
  onClose: () => void;
}

export function TableOverlay({ event, fetchPage, onClose }: TableOverlayProps) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4">
      <div className="bg-card border border-border rounded-2xl shadow-2xl w-full max-w-5xl max-h-[90vh] flex flex-col overflow-hidden">
        <div className="flex items-center justify-between px-4 py-3 border-b border-border bg-muted/20">
          <div className="flex items-center gap-2">
            <span className="text-base">📋</span>
            <span className="font-semibold text-sm">{event.filename}</span>
          </div>
          <button onClick={onClose} className="text-muted-foreground hover:text-foreground p-1 rounded">
            <span className="text-lg leading-none">×</span>
          </button>
        </div>
        <div className="flex-1 overflow-hidden p-3">
          <DataTable
            fetchPage={fetchPage}
            title={event.filename}
            defaultPerPage={100}
            className="h-full"
          />
        </div>
      </div>
    </div>
  );
}
