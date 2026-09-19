/**
 * Packages tab (ADR-0892 D4) — skill packages (ADR-0268 ZIPs) on
 * packages.py: upload (validated, audited before the atomic move into
 * <home>/packages/<tenant>/installed/), details, uninstall (audited before the
 * delete). A package is the ONLY way a skill arrives through the marketplace
 * today: the marketplace repository ships no skill index (D7), and the old
 * "skill install" routes were stubs — deleted.
 */
import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, Loader2, Package as PackageIcon, Upload } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { ApiError } from "@/lib/api/client";
import { useAuth } from "@/lib/auth";
import { KEY_PACKAGES } from "../header";
import { deletePackage, getPackageDetails, listPackages, uploadPackage, type PackageInfo } from "../api";

function uploadError(e: unknown): string {
  if (e instanceof ApiError && e.status === 400) return "Not installed — the ZIP is not a valid skill package (manifest.json missing or invalid).";
  if (e instanceof ApiError && e.status === 409) return "Not installed — this package version is already installed.";
  if (e instanceof ApiError && e.status === 404) return "Not installed — the package marketplace is switched off on this build.";
  return "Not installed — the upload did not succeed.";
}

export function PackagesTab() {
  const { session } = useAuth();
  const csrf = session?.csrf_token ?? "";
  const qc = useQueryClient();
  const q = useQuery({ queryKey: [...KEY_PACKAGES], queryFn: ({ signal }) => listPackages(signal), retry: false });
  const [detailId, setDetailId] = useState<string | null>(null);
  const detail = useQuery({
    queryKey: ["marketplace", "package", detailId],
    queryFn: ({ signal }) => getPackageDetails(detailId as string, signal),
    enabled: detailId !== null,
    retry: false,
  });
  const [msg, setMsg] = useState<string | null>(null);
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const upload = useMutation({
    mutationFn: (file: File) => uploadPackage(file, csrf),
    onSuccess: (r) => { setMsg(`Installed ${r.display_name} v${r.version} — audited.`); qc.invalidateQueries({ queryKey: [...KEY_PACKAGES] }); },
    onError: (e) => setMsg(uploadError(e)),
  });
  const remove = useMutation({
    mutationFn: (id: string) => deletePackage(id, csrf),
    onSuccess: (_r, id) => { setMsg(`Uninstalled ${id} — audited.`); setDetailId(null); qc.invalidateQueries({ queryKey: [...KEY_PACKAGES] }); },
    onError: () => setMsg("Not uninstalled — the package could not be removed."),
  });

  if (q.isLoading) {
    return <div className="py-16 flex justify-center"><Loader2 className="w-8 h-8 animate-spin text-muted-foreground" /></div>;
  }
  if (q.isError) {
    const off = q.error instanceof ApiError && q.error.status === 404;
    return (
      <Card className="border-destructive/30 bg-destructive/10">
        <CardContent className="py-6 flex items-center gap-2 text-destructive text-sm">
          <AlertCircle size={18} /> {off ? "The package marketplace is switched off on this build." : "The installed packages could not be loaded."}
        </CardContent>
      </Card>
    );
  }
  const packages: PackageInfo[] = q.data?.packages ?? [];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-xs text-muted-foreground" data-testid="packages-summary">
          {packages.length} skill packages installed for this tenant. A package is a ZIP with a manifest.json (ADR-0268); upload installs it after validation.
        </p>
        <input ref={fileRef} type="file" accept=".zip,application/zip" className="hidden" aria-label="Package ZIP"
               onChange={(e) => { const f = e.target.files?.[0]; if (f) upload.mutate(f); e.target.value = ""; }} />
        <Button variant="accent" size="sm" disabled={upload.isPending || !csrf} onClick={() => fileRef.current?.click()}>
          {upload.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Upload className="w-4 h-4" />} Upload package
        </Button>
      </div>
      {msg && <p className="text-xs text-muted-foreground" data-testid="packages-msg">{msg}</p>}

      {packages.length === 0 ? (
        <div className="py-10 text-center text-sm text-muted-foreground border border-dashed border-border rounded-lg">
          <PackageIcon className="w-10 h-10 mx-auto mb-2 text-muted-foreground/40" />
          No package installed yet.
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {packages.map((p) => (
            <Card key={p.package_id} data-testid={`package-card-${p.package_id}`}>
              <CardContent className="p-4 space-y-3">
                <div>
                  <button type="button" className="font-semibold hover:underline text-left" onClick={() => setDetailId(p.package_id)}>{p.display_name}</button>
                  <div className="text-xs text-muted-foreground font-mono">{p.package_id} · v{p.version}</div>
                </div>
                <p className="text-sm text-muted-foreground line-clamp-3">{p.description || "No description."}</p>
                <div className="flex flex-wrap gap-1 text-xs">
                  {p.author && <Badge variant="outline">by {p.author}</Badge>}
                  <Badge variant="outline">installed {new Date(p.installed_at).toLocaleDateString("en-US")}</Badge>
                </div>
                <div className="flex gap-2">
                  <Button size="sm" variant="secondary" onClick={() => setDetailId(p.package_id)}>Details</Button>
                  {confirmId !== p.package_id ? (
                    <Button size="sm" variant="outline" disabled={remove.isPending || !csrf} onClick={() => setConfirmId(p.package_id)}>Uninstall</Button>
                  ) : (
                    <>
                      <Button size="sm" variant="destructive" disabled={remove.isPending} onClick={() => { setConfirmId(null); remove.mutate(p.package_id); }}>
                        Confirm: delete {p.package_id}
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => setConfirmId(null)}>Cancel</Button>
                    </>
                  )}
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      <Dialog open={detailId !== null} onOpenChange={(o) => { if (!o) setDetailId(null); }}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>{detail.data?.display_name ?? detailId}</DialogTitle>
            <DialogDescription className="font-mono text-xs">{detailId}{detail.data ? ` · v${detail.data.version}` : ""}</DialogDescription>
          </DialogHeader>
          {detail.isLoading && <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />}
          {detail.isError && <p className="text-sm text-destructive">The package details could not be loaded.</p>}
          {detail.data && (
            <div className="space-y-3 text-sm">
              <p>{detail.data.description || "No description."}</p>
              <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-xs">
                <dt className="text-muted-foreground">Author</dt><dd>{detail.data.author || "—"}</dd>
                <dt className="text-muted-foreground">License</dt><dd>{detail.data.license || "—"}</dd>
                <dt className="text-muted-foreground">Installed</dt><dd>{new Date(detail.data.installed_at).toLocaleString("en-US")}</dd>
                <dt className="text-muted-foreground">Dependencies</dt><dd>{detail.data.dependencies.length ? detail.data.dependencies.join(", ") : "none"}</dd>
              </dl>
              {detail.data.permissions.length > 0 && (
                <div>
                  <div className="text-xs font-medium mb-1">Permissions</div>
                  <ul className="text-xs space-y-0.5">
                    {detail.data.permissions.map((perm) => (
                      <li key={perm.permission}><span className="font-mono">{perm.permission}</span>{perm.required ? " (required)" : " (optional)"}{perm.description ? ` — ${perm.description}` : ""}</li>
                    ))}
                  </ul>
                </div>
              )}
              <details>
                <summary className="text-xs cursor-pointer">Manifest</summary>
                <pre className="text-xs bg-muted rounded p-2 overflow-x-auto">{JSON.stringify(detail.data.manifest, null, 2)}</pre>
              </details>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
