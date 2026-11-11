/**
 * The folder picker: one dialog for every place a folder is chosen (new project, Add existing folder, Move, Locate folder, Default
 * place). It lists the DIRECTORIES of the machine that runs the console and shows the server's verdict for the picker's purpose
 * before "Choose" is enabled; the action judges the folder again on confirm. Typing a path is the advanced way ("Enter a path
 * instead…", Ctrl/Cmd+L), judged by the same verdict. Every decision lives in pages/projects/picker-logic.ts.
 *
 * At 375 px it is a full-screen sheet: places as a chip row, breadcrumbs collapsed to the parent, 44 px rows, verdict and buttons
 * in a sticky footer.
 */
import { useCallback, useEffect, useId, useReducer, useRef, useState, type KeyboardEvent } from "react";
import { ChevronRight, Folder, FolderPlus, HardDrive, Link2, Star } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";
import { browse, browsePlaces, clearRecent, setFavourites, type BrowsePlace, type BrowseVerdict, type PickerPurpose } from "@/lib/api/projects";
import {
  INITIAL_PICKER, SLOW_MESSAGE, UNREADABLE_MESSAGE, allowsNewFolder, canChoose, crumbsView, enterable, entryNote, initialPath,
  cannotOpen, keyHandler, listingError, newFolderNameRule, placeGroups, reducer, target, toggleFavourite, verdictText, verdictWarnings,
} from "@/pages/projects/picker-logic";

export interface FolderPickerProps {
  open: boolean;
  purpose: PickerPurpose;
  projectId?: string;
  /** the project's title, for "Choose a folder for "<title>"" */
  title?: string;
  /** where to open (the current value); else the first place */
  initialPath?: string | null;
  /** test id of the advanced path field */
  inputTestId?: string;
  onChoose: (path: string) => void;
  onCancel: () => void;
}

function useCompact(): boolean {
  const q = "(max-width: 639px)";
  const [m, setM] = useState(() => typeof window !== "undefined" && window.matchMedia?.(q).matches);
  useEffect(() => {
    const mq = window.matchMedia?.(q);
    if (!mq) return;
    const on = () => setM(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return !!m;
}

const PLACE_ICON = { drive: HardDrive } as const;

export function FolderPicker(props: FolderPickerProps) {
  return (
    <Dialog open={props.open} onOpenChange={(o) => { if (!o) props.onCancel(); }}>
      {props.open && <PickerBody {...props} />}
    </Dialog>
  );
}

function PickerBody({ purpose, projectId, title, initialPath: given, inputTestId, onChoose, onCancel }: FolderPickerProps) {
  const { session } = useAuth();
  const csrf = session?.csrf_token ?? "";
  const compact = useCompact();
  const listId = useId();
  const [s, dispatch] = useReducer(reducer, INITIAL_PICKER);
  const [places, setPlaces] = useState<BrowsePlace[] | null>(null);
  const [placesError, setPlacesError] = useState<string | null>(null);
  const [placesSeq, setPlacesSeq] = useState(0);
  const [favNote, setFavNote] = useState<string | null>(null);
  const [newVerdict, setNewVerdict] = useState<{ for: string; v?: BrowseVerdict; error?: string } | null>(null);
  const [moreBusy, setMoreBusy] = useState(false);
  const listRef = useRef<HTMLUListElement>(null);
  const pathRef = useRef<HTMLInputElement>(null);
  const chooseRef = useRef<HTMLButtonElement>(null);
  const started = useRef(false);

  // places: the left column (chips on a phone); the first answer also says where to start when no folder was given
  useEffect(() => {
    const ac = new AbortController();
    setPlacesError(null);
    browsePlaces(ac.signal).then((r) => {
      if (r.verdict && !r.verdict.ok) { setPlacesError(r.verdict.message || SLOW_MESSAGE); setPlaces([]); }
      else setPlaces(r.places);
      if (!started.current) {
        const p = initialPath(r.places, given);
        if (p) { started.current = true; dispatch({ type: "navigate", path: p }); }
      }
    }).catch((e) => {
      if (ac.signal.aborted) return;
      setPlacesError(listingError(e));
      if (!started.current && given) { started.current = true; dispatch({ type: "navigate", path: given }); }
    });
    return () => ac.abort();
  }, [placesSeq]); // eslint-disable-line react-hooks/exhaustive-deps

  // the listing of the current folder, with its verdict
  useEffect(() => {
    if (s.path === null || s.status !== "loading") return;
    const ac = new AbortController();
    const seq = s.seq;
    browse({ path: s.path, purpose, projectId, hidden: s.hidden }, ac.signal)
      .then((l) => dispatch({ type: "loaded", listing: l, seq }))
      .catch((e) => { if (!ac.signal.aborted) dispatch({ type: "failed", message: listingError(e), seq }); });
    return () => ac.abort();
  }, [s.seq]); // eslint-disable-line react-hooks/exhaustive-deps

  // a new folder name: the verdict of exactly the folder "Choose" would return (debounced; the server throttles at 10/s)
  const tgt = target(s);
  const naming = s.newName !== "" && newFolderNameRule(s.newName) === null && s.status === "ready";
  useEffect(() => {
    if (!naming || !tgt) { setNewVerdict(null); return; }
    const ac = new AbortController();
    setNewVerdict({ for: tgt });
    const t = setTimeout(() => {
      browse({ path: tgt, purpose, projectId }, ac.signal)
        .then((l) => setNewVerdict({ for: tgt, v: l.verdict }))
        .catch((e) => { if (!ac.signal.aborted) setNewVerdict({ for: tgt, error: listingError(e) }); });
    }, 300);
    return () => { clearTimeout(t); ac.abort(); };
  }, [naming, tgt]); // eslint-disable-line react-hooks/exhaustive-deps

  // keep the focused row in view
  useEffect(() => {
    if (s.focus < 0) return;
    listRef.current?.querySelector<HTMLElement>(`[data-index="${s.focus}"]`)?.scrollIntoView?.({ block: "nearest" });
  }, [s.focus]);
  // the path field opens with the current folder, selected: typing replaces it, an arrow key edits it
  useEffect(() => { if (s.pathMode) { pathRef.current?.focus(); pathRef.current?.select(); } }, [s.pathMode]);

  const verdict: BrowseVerdict | undefined = s.newName
    ? (newFolderNameRule(s.newName) !== null ? undefined : newVerdict?.for === tgt ? newVerdict?.v : undefined)
    : s.listing?.verdict;
  const choosable = canChoose(s, verdict) && (!s.newName || (newVerdict?.for === tgt && !!newVerdict?.v));
  const choose = useCallback(() => { if (choosable && tgt) onChoose(tgt); }, [choosable, tgt, onChoose]);

  // React events bubble through the portal into the component that opened the picker (often a <form>): a key or a submit in the
  // picker must never reach it - Enter in the path field once submitted the Default place dialog with an empty folder
  const onKeyDown = (e: KeyboardEvent<HTMLElement>) => {
    e.stopPropagation();
    const el = e.target as HTMLElement;
    const inField = el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable;
    // a button handles its own Enter/Space
    if (!inField && el.tagName === "BUTTON" && (e.key === "Enter" || e.key === " ")) return;
    const ev = keyHandler(s, { key: e.key, ctrlKey: e.ctrlKey, metaKey: e.metaKey, altKey: e.altKey, inField }, Date.now());
    if (!ev) return;
    // Esc: the dialog's own handler cancels (it runs first and closes only the top dialog); the key must not reach a form behind
    if (ev.type === "cancel") return;
    e.preventDefault();
    dispatch(ev);
    if (ev.type === "navigate") listRef.current?.focus();
    // Space marks the current folder: the next key (Enter) is "Choose" when the verdict allows it
    if (ev.type === "mark") requestAnimationFrame(() => { if (chooseRef.current && !chooseRef.current.disabled) chooseRef.current.focus(); });
  };

  const go = (path: string) => { dispatch({ type: "navigate", path }); listRef.current?.focus(); };
  const submitPath = () => { const p = s.pathDraft.trim(); if (p) dispatch({ type: "navigate", path: p }); };
  const loadMore = () => {
    if (!s.listing?.next_page || s.path === null) return;
    const seq = s.seq;
    setMoreBusy(true);
    browse({ path: s.path, purpose, projectId, hidden: s.hidden, page: s.listing.next_page })
      .then((l) => dispatch({ type: "appended", listing: l, seq }))
      .catch((e) => dispatch({ type: "failed", message: listingError(e), seq }))
      .finally(() => setMoreBusy(false));
  };
  const star = () => {
    if (!places || !s.listing) return;
    const next = toggleFavourite(places, s.listing.path);
    if (next === null) { setFavNote("At most 10 favourites."); return; }
    setFavNote(null);
    setFavourites(next, csrf).then(() => setPlacesSeq((n) => n + 1)).catch((e) => setFavNote(listingError(e)));
  };
  const clearRecents = () => {
    clearRecent(csrf).then((r) => setPlaces(r.places)).catch((e) => setFavNote(listingError(e)));
  };

  const groups = placeGroups(places ?? []);
  const isFav = !!s.listing && (places ?? []).some((p) => p.kind === "favourite" && p.path === s.listing?.path);
  const heading = title ? `Choose a folder for "${title}"` : "Choose a folder";
  const entries = s.listing?.entries ?? [];
  const blocked = !!s.listing && !s.listing.readable && entries.length === 0 && s.listing.verdict.reason_class !== "slow";
  const slow = s.listing?.verdict.reason_class === "slow";
  const crumbs = crumbsView(s.listing?.breadcrumbs ?? [], compact);
  const nameProblem = s.newName ? newFolderNameRule(s.newName) : null;
  const warnings = verdictWarnings(verdict);

  return (
    <DialogContent
      data-testid="folder-picker" data-purpose={purpose}
      onEscapeKeyDown={(e) => { e.preventDefault(); onCancel(); }}
      onKeyDown={onKeyDown}
      onOpenAutoFocus={(e) => { e.preventDefault(); requestAnimationFrame(() => listRef.current?.focus()); }}
      className={cn(
        "flex flex-col gap-0 overflow-hidden p-0 sm:h-[min(640px,85vh)] sm:max-w-4xl",
        "max-sm:inset-0 max-sm:left-0 max-sm:top-0 max-sm:h-[100dvh] max-sm:max-h-none max-sm:max-w-none max-sm:translate-x-0 max-sm:translate-y-0 max-sm:rounded-none max-sm:border-0",
      )}>
      <header className="border-b px-4 py-3 pr-12 sm:px-5">
        <DialogTitle className="text-lg" data-testid="picker-title">{heading}</DialogTitle>
        <DialogDescription className="text-xs">Folders on the computer that runs Corvin. Files are not shown.</DialogDescription>
      </header>

      <div className="flex min-h-0 flex-1 flex-col sm:flex-row">
        <nav aria-label="Places" data-testid="picker-places"
          className="shrink-0 border-b max-sm:overflow-x-auto sm:w-56 sm:overflow-y-auto sm:border-b-0 sm:border-r">
          <div className="flex gap-2 px-3 py-2 sm:flex-col sm:gap-3 sm:py-3">
            {places === null && !placesError && (
              <div className="flex gap-2 sm:flex-col" data-testid="picker-places-loading">
                {[0, 1, 2].map((i) => <Skeleton key={i} className="h-7 w-24 sm:w-full" />)}
              </div>
            )}
            {placesError && (
              <div className="flex shrink-0 items-center gap-2 text-xs sm:flex-col sm:items-start" data-testid="picker-places-error">
                <span className="text-amber-800 dark:text-amber-300">{placesError}</span>
                <button type="button" className="underline" onClick={() => setPlacesSeq((n) => n + 1)}>Retry</button>
              </div>
            )}
            {groups.map((g) => (
              <div key={g.id} className="flex shrink-0 gap-2 sm:flex-col sm:gap-0.5" data-testid="picker-place-group" data-group={g.id}>
                <h3 className="sr-only text-[11px] font-medium uppercase tracking-wide text-muted-foreground sm:not-sr-only sm:px-2 sm:pb-1">{g.label}</h3>
                {g.places.map((p) => {
                  const Icon = PLACE_ICON[p.kind as keyof typeof PLACE_ICON] ?? (p.kind === "favourite" ? Star : Folder);
                  const active = s.listing?.path === p.path;
                  return (
                    <button key={`${p.kind}:${p.path}`} type="button" disabled={!p.available} data-testid="picker-place" data-kind={p.kind}
                      data-path={p.path} title={p.path} onClick={() => go(p.path)}
                      aria-current={active ? "true" : undefined}
                      className={cn(
                        "flex min-w-0 shrink-0 items-center gap-1.5 text-left text-sm transition-colors disabled:cursor-not-allowed disabled:opacity-50",
                        "max-sm:h-9 max-sm:rounded-full max-sm:border max-sm:px-3",
                        "sm:w-full sm:rounded sm:px-2 sm:py-1.5 sm:hover:bg-muted",
                        active && "border-primary bg-primary/10 sm:bg-primary/10",
                      )}>
                      <Icon className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
                      <span className="truncate">{p.label}</span>
                      {p.note && <span className="shrink-0 text-[11px] text-muted-foreground">({p.note})</span>}
                    </button>
                  );
                })}
                {g.id === "recent" && (
                  <button type="button" className="shrink-0 whitespace-nowrap px-2 text-left text-xs text-muted-foreground underline"
                    onClick={clearRecents} data-testid="picker-clear-recent">Clear recent places</button>
                )}
              </div>
            ))}
          </div>
        </nav>

        <section className="flex min-h-0 min-w-0 flex-1 flex-col" aria-label="Folders">
          <div className="flex min-h-11 items-center gap-2 border-b px-3 py-1.5">
            <nav aria-label="Breadcrumbs" className="flex min-w-0 flex-1 items-center gap-0.5 overflow-hidden text-sm" data-testid="picker-crumbs">
              {!compact && s.listing?.parent && (
                <button type="button" className="mr-1 rounded px-1.5 text-muted-foreground hover:bg-muted" aria-label="Up one folder"
                  onClick={() => go(s.listing!.parent!)} data-testid="picker-up">‹</button>
              )}
              {crumbs.map((c, i) => (
                <span key={`${i}:${c.label}`} className="flex min-w-0 items-center">
                  {!compact && i > 0 && <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />}
                  {c.path ? (
                    <button type="button" className="truncate rounded px-1 hover:bg-muted" onClick={() => go(c.path!)} data-testid="picker-crumb"
                      title={c.path}>{c.label}</button>
                  ) : (
                    <span className={cn("truncate px-1", i === crumbs.length - 1 ? "font-medium" : "text-muted-foreground")}
                      data-testid={i === crumbs.length - 1 ? "picker-crumb-current" : "picker-crumb-gap"}>{c.label}</span>
                  )}
                </span>
              ))}
              {!s.listing && s.path && <span className="truncate px-1 font-mono text-xs text-muted-foreground">{s.path}</span>}
            </nav>
            {s.listing && s.listing.readable && (
              <button type="button" onClick={star} aria-pressed={isFav} data-testid="picker-favourite"
                className="flex h-8 shrink-0 items-center gap-1 rounded px-1.5 text-xs text-muted-foreground hover:bg-muted"
                title={isFav ? "Remove from favourites" : "Add to favourites"}>
                <Star className={cn("h-3.5 w-3.5", isFav && "fill-current text-primary")} aria-hidden />
                <span className="sr-only">{isFav ? "Remove from favourites" : "Add to favourites"}</span>
              </button>
            )}
            <label className="flex shrink-0 items-center gap-1.5 text-xs text-muted-foreground">
              <input type="checkbox" checked={s.hidden} onChange={() => dispatch({ type: "toggleHidden" })} data-testid="picker-hidden" />
              Show hidden
            </label>
          </div>
          {favNote && <p className="px-3 pt-1 text-xs text-amber-800 dark:text-amber-300" role="status">{favNote}</p>}

          {s.pathMode && (
            <form className="flex flex-wrap items-center gap-2 border-b bg-muted/40 px-3 py-2" data-testid="picker-path-form"
              onSubmit={(e) => { e.preventDefault(); e.stopPropagation(); submitPath(); }}>
              <label className="sr-only" htmlFor={`${listId}-path`}>Folder path</label>
              <Input id={`${listId}-path`} ref={pathRef} value={s.pathDraft} data-testid={inputTestId ?? "picker-path-input"}
                onChange={(e) => dispatch({ type: "pathDraft", value: e.target.value })}
                placeholder="absolute path, e.g. /home/you/code" className="h-9 min-w-0 flex-1 font-mono text-xs" spellCheck={false} autoComplete="off" />
              <Button type="submit" size="sm" variant="outline" disabled={!s.pathDraft.trim()} data-testid="picker-path-go">Go</Button>
              <button type="button" className="text-xs underline" onClick={() => dispatch({ type: "pathMode", open: false })}>Hide</button>
            </form>
          )}

          <ul ref={listRef} id={listId} role="listbox" aria-label="Folders" tabIndex={0} data-testid="picker-list"
            aria-activedescendant={s.focus >= 0 ? `${listId}-r${s.focus}` : undefined}
            className="min-h-0 flex-1 overflow-y-auto py-1 outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring">
            {s.status === "loading" && [0, 1, 2, 3, 4].map((i) => (
              <li key={i} className="flex h-11 items-center px-3 sm:h-9" data-testid="picker-skeleton" aria-hidden><Skeleton className="h-4 w-2/5" /></li>
            ))}
            {s.status === "error" && (
              <li className="space-y-2 px-3 py-3 text-sm" data-testid="picker-error">
                <p role="alert" className="text-red-700 dark:text-red-300">{s.error}</p>
                {s.path && <Button size="sm" variant="outline" onClick={() => go(s.path!)} data-testid="picker-retry">Retry</Button>}
              </li>
            )}
            {s.status === "ready" && slow && (
              <li className="space-y-2 px-3 py-3 text-sm" data-testid="picker-slow">
                <p className="text-amber-800 dark:text-amber-300">did not answer in time</p>
                <Button size="sm" variant="outline" onClick={() => go(s.path!)} data-testid="picker-retry">Retry</Button>
              </li>
            )}
            {s.status === "ready" && !slow && blocked && (
              <li className="px-3 py-3 text-sm text-muted-foreground" data-testid="picker-blocked">
                {cannotOpen(s.listing) ? "This folder cannot be opened here." : UNREADABLE_MESSAGE}
              </li>
            )}
            {s.status === "ready" && !slow && !blocked && entries.length === 0 && (
              <li className="px-3 py-3 text-sm text-muted-foreground" data-testid="picker-empty">
                No folders here.{allowsNewFolder(purpose) ? " Choose this folder, or make a new one below." : ""}
              </li>
            )}
            {s.status === "ready" && entries.map((e, i) => {
              const can = enterable(e);
              const note = entryNote(e);
              return (
                <li key={`${e.path}:${i}`} id={`${listId}-r${i}`} role="option" aria-selected={s.focus === i} aria-disabled={!can}
                  data-index={i} data-testid="picker-row" data-name={e.name} data-enterable={can ? "true" : "false"}
                  onClick={() => { dispatch({ type: "focus", index: i }); if (can) go(e.path); }}
                  className={cn(
                    "flex h-11 cursor-pointer select-none items-center gap-2 border-l-2 px-3 text-sm sm:h-9",
                    s.focus === i ? "border-primary bg-primary/10" : "border-transparent",
                    can ? "hover:bg-muted" : "cursor-default text-muted-foreground",
                  )}>
                  {e.symlink ? <Link2 className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
                    : <Folder className={cn("h-4 w-4 shrink-0", can ? "text-primary" : "text-muted-foreground")} aria-hidden />}
                  <span className="min-w-0 flex-1 truncate">{e.name}</span>
                  {note && <span className="min-w-0 max-w-[55%] shrink truncate text-right text-xs text-muted-foreground" data-testid="picker-row-note">{note}</span>}
                  {can && <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />}
                </li>
              );
            })}
            {s.status === "ready" && s.listing?.next_page && (
              <li className="px-3 py-2">
                <Button size="sm" variant="ghost" onClick={loadMore} disabled={moreBusy} data-testid="picker-more">
                  {moreBusy ? "Loading…" : "Show more folders"}
                </Button>
              </li>
            )}
          </ul>

          {allowsNewFolder(purpose) && s.status === "ready" && !slow && !blocked && (
            <div className="space-y-1 border-t px-3 py-2">
              <label className="flex flex-wrap items-center gap-2 text-sm">
                <FolderPlus className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
                <span className="shrink-0">New folder here</span>
                <Input value={s.newName} maxLength={120} placeholder="name" aria-describedby={`${listId}-rule`}
                  onChange={(e) => dispatch({ type: "newName", value: e.target.value })}
                  className="h-9 min-w-0 flex-1 basis-40 font-mono text-xs" data-testid="picker-new-name" spellCheck={false} autoComplete="off" />
              </label>
              <p id={`${listId}-rule`} className={cn("text-xs", nameProblem ? "text-red-700 dark:text-red-300" : "text-muted-foreground")}
                data-testid="picker-name-rule">
                {nameProblem ?? "Letters, digits, '-', '_' or '.', no leading dot, at most 80 characters. Created when you continue."}
              </p>
            </div>
          )}
        </section>
      </div>

      <footer className="sticky bottom-0 space-y-2 border-t bg-card px-4 py-3 sm:px-5" data-testid="picker-footer">
        <div className={cn("rounded border px-3 py-2 text-sm", verdict?.ok ? "border-border" : "border-amber-600/50")}
          data-testid="picker-verdict" data-ok={verdict ? String(verdict.ok) : "pending"} aria-live="polite">
          <div className="break-all font-mono text-xs text-muted-foreground" data-testid="picker-target">{tgt ?? " "}</div>
          <div data-testid="picker-verdict-text">
            {nameProblem ? "Choose a valid name for the new folder."
              : s.status === "loading" || (s.newName && !verdict && !newVerdict?.error) ? "Checking…"
                : s.status === "error" ? "This folder could not be checked."
                  : newVerdict?.error && s.newName ? newVerdict.error
                    : verdict ? verdictText(verdict, purpose) : " "}
          </div>
          {warnings.map((w) => (
            <div key={w} className="mt-1 text-xs text-amber-800 dark:text-amber-300" data-testid="picker-warning">{w}</div>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {!s.pathMode && (
            <button type="button" className="mr-auto text-xs underline" onClick={() => dispatch({ type: "pathMode", open: true })}
              data-testid="picker-path-link">Enter a path instead…</button>
          )}
          <div className="ml-auto flex gap-2">
            <Button type="button" variant="ghost" size="sm" onClick={onCancel} data-testid="picker-cancel">Cancel</Button>
            <Button ref={chooseRef} type="button" size="sm" disabled={!choosable} onClick={choose} data-testid="picker-choose">Choose</Button>
          </div>
        </div>
      </footer>
    </DialogContent>
  );
}

/** A chosen folder (or none yet) and the button that opens the picker. Cancel leaves the value as it was. */
export function FolderField({ value, onChange, purpose, projectId, title, initialPath: start, inputTestId, testid, disabled, label = "Choose a folder…" }: {
  value: string; onChange: (path: string) => void; purpose: PickerPurpose; projectId?: string; title?: string; initialPath?: string | null;
  inputTestId?: string; testid: string; disabled?: boolean; label?: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="flex min-w-0 flex-wrap items-center gap-2" data-testid={testid}>
      {value && <span className="min-w-0 break-all font-mono text-xs" data-testid={`${testid}-value`}>{value}</span>}
      <Button type="button" size="sm" variant="outline" onClick={() => setOpen(true)} disabled={disabled} data-testid={`${testid}-choose`}>
        {value ? "Change…" : label}
      </Button>
      <FolderPicker open={open} purpose={purpose} projectId={projectId} title={title} initialPath={value || start} inputTestId={inputTestId}
        onChoose={(p) => { onChange(p); setOpen(false); }} onCancel={() => setOpen(false)} />
    </div>
  );
}
