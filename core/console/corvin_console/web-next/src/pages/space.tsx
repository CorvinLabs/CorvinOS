import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BookOpen,
  CheckCircle2,
  Eye,
  Globe,
  KeyRound,
  Lock,
  Pencil,
  Plus,
  RefreshCw,
  Rss,
  Send,
  Shield,
  Trash2,
  UserPlus,
  Users,
  X,
  XCircle,
} from "lucide-react";
import {
  createGrant,
  createSpaceDomain,
  deleteSpaceDomain,
  followActor,
  getSocialFollowers,
  getSocialFollowing,
  getSocialStatus,
  getSpaceDomains,
  getSpaceProfile,
  joinSocial,
  leaveSocial,
  listGrantTemplates,
  listGrants,
  publishToDomain,
  revokeGrant,
  updateSpaceProfile,
  type GrantTemplate,
  type SocialActor,
  type SpaceDomain,
  type SpaceProfile,
} from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import { useAuth } from "@/lib/auth";

// ── Helpers ────────────────────────────────────────────────────────────

function fmtDate(ts: number | null | undefined): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleDateString("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}

function VisibilityBadge({ v }: { v: string }) {
  if (v === "public")
    return (
      <Badge className="border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400">
        <Globe className="mr-1 h-3 w-3" /> Public
      </Badge>
    );
  if (v === "followers")
    return (
      <Badge className="border-blue-500/40 bg-blue-500/10 text-blue-700 dark:text-blue-400">
        <Rss className="mr-1 h-3 w-3" /> Followers
      </Badge>
    );
  return (
    <Badge variant="outline">
      <Lock className="mr-1 h-3 w-3" /> Private
    </Badge>
  );
}

// ── Domain Card ────────────────────────────────────────────────────────

interface DomainCardProps {
  domain: SpaceDomain;
  onPublish: (domain: SpaceDomain) => void;
  onDelete: (slug: string) => void;
  isDeleting: boolean;
}

function DomainCard({ domain, onPublish, onDelete, isDeleting }: DomainCardProps) {
  return (
    <Card className="flex flex-col gap-0">
      <CardHeader className="pb-2">
        <div className="flex items-start justify-between gap-2">
          <div className="flex min-w-0 flex-col gap-1">
            <CardTitle className="truncate text-base">
              <BookOpen className="mr-1.5 inline h-4 w-4 text-muted-foreground" />
              {domain.name}
            </CardTitle>
            <div className="font-mono text-[11px] text-muted-foreground">/{domain.slug}</div>
          </div>
          <VisibilityBadge v={domain.visibility} />
        </div>
      </CardHeader>
      <CardContent className="flex flex-1 flex-col gap-3 pt-0">
        {domain.description ? (
          <CardDescription className="line-clamp-2">{domain.description}</CardDescription>
        ) : (
          <CardDescription className="italic text-muted-foreground/50">
            No description
          </CardDescription>
        )}
        <div className="text-xs text-muted-foreground">
          {domain.post_count ?? 0}{" "}
          {(domain.post_count ?? 0) === 1 ? "post" : "posts"} · created{" "}
          {fmtDate(domain.created_at)}
        </div>
        <div className="flex gap-2">
          <Button size="sm" className="flex-1" onClick={() => onPublish(domain)}>
            <Send className="mr-1.5 h-3.5 w-3.5" /> Publish
          </Button>
          <Button
            size="sm"
            variant="outline"
            className="text-destructive hover:text-destructive"
            disabled={isDeleting}
            onClick={() => {
              if (window.confirm(`Delete domain "${domain.name}"? This cannot be undone.`))
                onDelete(domain.slug);
            }}
          >
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function EmptyDomainSlot({ onAdd }: { onAdd: () => void }) {
  return (
    <button
      onClick={onAdd}
      className={cn(
        "flex h-full min-h-[180px] flex-col items-center justify-center gap-2 rounded-xl",
        "border-2 border-dashed border-border text-muted-foreground",
        "transition-colors hover:border-accent hover:text-accent",
      )}
    >
      <Plus className="h-8 w-8" />
      <span className="text-sm font-medium">Add Domain</span>
    </button>
  );
}

// ── Actor list item ────────────────────────────────────────────────────

function ActorRow({ actor }: { actor: SocialActor }) {
  return (
    <div className="flex items-center gap-3 rounded-md px-3 py-2 hover:bg-muted/50">
      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-accent/20 text-xs font-medium text-accent">
        {(actor.display_name ?? actor.actor_id).slice(0, 1).toUpperCase()}
      </div>
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-medium">
          {actor.display_name ?? actor.actor_id}
        </div>
        <div className="truncate font-mono text-[11px] text-muted-foreground">
          {actor.actor_id}
        </div>
      </div>
      {actor.is_ai && (
        <Badge variant="outline" className="shrink-0 text-[10px]">
          AI
        </Badge>
      )}
      {actor.compliance_zone && (
        <Badge variant="outline" className="shrink-0 font-mono text-[10px]">
          {actor.compliance_zone}
        </Badge>
      )}
    </div>
  );
}

// ── Profile Section ────────────────────────────────────────────────────

function ProfileSection() {
  const qc = useQueryClient();
  const { session } = useAuth();

  const profileQ = useQuery({
    queryKey: ["space-profile"],
    queryFn: ({ signal }) => getSpaceProfile(signal),
    staleTime: 30_000,
  });

  const socialQ = useQuery({
    queryKey: ["space-social-status"],
    queryFn: ({ signal }) => getSocialStatus(signal),
    staleTime: 30_000,
  });

  const followingQ = useQuery({
    queryKey: ["space-following"],
    queryFn: ({ signal }) => getSocialFollowing(signal),
    staleTime: 30_000,
  });

  const followersQ = useQuery({
    queryKey: ["space-followers"],
    queryFn: ({ signal }) => getSocialFollowers(signal),
    staleTime: 30_000,
  });

  // Profile edit state
  const [editing, setEditing] = React.useState(false);
  const [form, setForm] = React.useState<Partial<SpaceProfile>>({});

  // Dialogs
  const [joinOpen, setJoinOpen] = React.useState(false);
  const [joinForm, setJoinForm] = React.useState({
    display_name: "",
    host: "",
    compliance_zone: "eu",
  });
  const [followOpen, setFollowOpen] = React.useState(false);
  const [followForm, setFollowForm] = React.useState({
    actor_id: "",
    inbox_url: "",
    public_key_hex: "",
    display_name: "",
    compliance_zone: "",
  });

  const profile = profileQ.data?.profile;

  React.useEffect(() => {
    if (profile && !editing) {
      setForm({
        display_name: profile.display_name,
        bio: profile.bio,
        contact_handle: profile.contact_handle,
        website: profile.website,
        location: profile.location,
      });
    }
  }, [profile, editing]);

  const csrf = session?.csrf_token ?? "";

  const updateMut = useMutation({
    mutationFn: (data: Partial<SpaceProfile>) => updateSpaceProfile(csrf, data),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["space-profile"] });
      setEditing(false);
    },
  });

  const joinMut = useMutation({
    mutationFn: () =>
      joinSocial(csrf, {
        display_name: joinForm.display_name,
        host: joinForm.host,
        compliance_zone: joinForm.compliance_zone,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["space-social-status"] });
      setJoinOpen(false);
      setJoinForm({ display_name: "", host: "", compliance_zone: "eu" });
    },
  });

  const leaveMut = useMutation({
    mutationFn: () => leaveSocial(csrf),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["space-social-status"] });
      void qc.invalidateQueries({ queryKey: ["space-following"] });
      void qc.invalidateQueries({ queryKey: ["space-followers"] });
    },
  });

  const followMut = useMutation({
    mutationFn: () =>
      followActor(csrf, {
        actor_id: followForm.actor_id,
        inbox_url: followForm.inbox_url,
        public_key_hex: followForm.public_key_hex,
        display_name: followForm.display_name || undefined,
        compliance_zone: followForm.compliance_zone || undefined,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["space-following"] });
      setFollowOpen(false);
      setFollowForm({
        actor_id: "",
        inbox_url: "",
        public_key_hex: "",
        display_name: "",
        compliance_zone: "",
      });
    },
  });

  const social = socialQ.data;
  const isLoading = profileQ.isLoading || socialQ.isLoading;

  if (isLoading) {
    return (
      <div className="py-10 text-center text-sm text-muted-foreground">
        <RefreshCw className="mx-auto mb-2 h-5 w-5 animate-spin" />
        Loading profile…
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      {/* Profile Card */}
      <Card>
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between">
            <CardTitle className="text-lg">Personal Profile</CardTitle>
            {!editing ? (
              <Button variant="outline" size="sm" onClick={() => setEditing(true)}>
                <Pencil className="mr-1.5 h-3.5 w-3.5" /> Edit
              </Button>
            ) : (
              <div className="flex gap-2">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setEditing(false);
                    if (profile) {
                      setForm({
                        display_name: profile.display_name,
                        bio: profile.bio,
                        contact_handle: profile.contact_handle,
                        website: profile.website,
                        location: profile.location,
                      });
                    }
                  }}
                >
                  Cancel
                </Button>
                <Button
                  size="sm"
                  disabled={updateMut.isPending}
                  onClick={() => updateMut.mutate(form)}
                >
                  {updateMut.isPending ? "Saving…" : "Save"}
                </Button>
              </div>
            )}
          </div>
        </CardHeader>
        <CardContent>
          {editing ? (
            <div className="flex flex-col gap-4">
              <div className="grid gap-1.5">
                <Label htmlFor="display_name">Display Name</Label>
                <Input
                  id="display_name"
                  value={form.display_name ?? ""}
                  onChange={(e) => setForm((f) => ({ ...f, display_name: e.target.value }))}
                />
              </div>
              <div className="grid gap-1.5">
                <Label htmlFor="bio">Bio</Label>
                <Textarea
                  id="bio"
                  rows={3}
                  value={form.bio ?? ""}
                  onChange={(e) => setForm((f) => ({ ...f, bio: e.target.value }))}
                  placeholder="Tell people about yourself or your agent…"
                />
              </div>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
                <div className="grid gap-1.5">
                  <Label htmlFor="contact_handle">Contact Handle</Label>
                  <Input
                    id="contact_handle"
                    value={form.contact_handle ?? ""}
                    onChange={(e) =>
                      setForm((f) => ({ ...f, contact_handle: e.target.value }))
                    }
                    placeholder="@handle"
                  />
                </div>
                <div className="grid gap-1.5">
                  <Label htmlFor="website">Website</Label>
                  <Input
                    id="website"
                    value={form.website ?? ""}
                    onChange={(e) => setForm((f) => ({ ...f, website: e.target.value }))}
                    placeholder="https://…"
                  />
                </div>
                <div className="grid gap-1.5">
                  <Label htmlFor="location">Location</Label>
                  <Input
                    id="location"
                    value={form.location ?? ""}
                    onChange={(e) => setForm((f) => ({ ...f, location: e.target.value }))}
                    placeholder="City, Country"
                  />
                </div>
              </div>
              {updateMut.isError && (
                <p className="text-sm text-destructive">
                  {updateMut.error instanceof Error
                    ? updateMut.error.message
                    : "Save failed"}
                </p>
              )}
            </div>
          ) : (
            <div className="flex flex-col gap-3">
              <h2 className="text-2xl font-semibold">
                {profile?.display_name || (
                  <span className="text-muted-foreground italic">No display name</span>
                )}
              </h2>
              {profile?.bio ? (
                <p className="text-sm text-muted-foreground">{profile.bio}</p>
              ) : (
                <p className="text-sm italic text-muted-foreground/50">No bio yet.</p>
              )}
              <div className="flex flex-wrap gap-4 text-sm text-muted-foreground">
                {profile?.contact_handle && (
                  <span>
                    <span className="font-medium">Handle:</span> {profile.contact_handle}
                  </span>
                )}
                {profile?.website && (
                  <span>
                    <span className="font-medium">Web:</span>{" "}
                    <a
                      href={profile.website}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-accent hover:underline"
                    >
                      {profile.website}
                    </a>
                  </span>
                )}
                {profile?.location && (
                  <span>
                    <span className="font-medium">Location:</span> {profile.location}
                  </span>
                )}
              </div>
              {profile?.updated_at && (
                <div className="text-xs text-muted-foreground/60">
                  Updated {fmtDate(profile.updated_at)}
                </div>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Social Federation Card */}
      <Card>
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between">
            <div>
              <CardTitle className="text-lg">Social Federation</CardTitle>
              <CardDescription>
                CorvinFed · ActivityPub-compatible social federation
              </CardDescription>
            </div>
            <div className="flex items-center gap-2">
              {social?.status?.is_enabled ? (
                <>
                  <Badge className="border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400">
                    <CheckCircle2 className="mr-1 h-3 w-3" /> Joined
                  </Badge>
                  <Button
                    variant="outline"
                    size="sm"
                    className="text-destructive hover:text-destructive"
                    disabled={leaveMut.isPending}
                    onClick={() => {
                      if (
                        window.confirm(
                          "Leave the federation? This will remove your actor and all follows.",
                        )
                      )
                        leaveMut.mutate();
                    }}
                  >
                    <XCircle className="mr-1.5 h-3.5 w-3.5" />
                    {leaveMut.isPending ? "Leaving…" : "Leave Federation"}
                  </Button>
                </>
              ) : (
                <>
                  <Badge variant="outline" className="text-muted-foreground">
                    Not joined
                  </Badge>
                  <Button size="sm" onClick={() => setJoinOpen(true)}>
                    <UserPlus className="mr-1.5 h-3.5 w-3.5" /> Join Federation
                  </Button>
                </>
              )}
            </div>
          </div>
        </CardHeader>
        {social?.status?.is_enabled && (
          <CardContent className="flex flex-col gap-4">
            {social.status?.actor_id && (
              <div className="rounded-md bg-muted/50 px-3 py-2">
                <div className="text-xs text-muted-foreground">Actor ID</div>
                <div className="font-mono text-sm">{social.status?.actor_id}</div>
              </div>
            )}

            <div className="flex items-center gap-3 text-sm text-muted-foreground">
              <span>
                <span className="font-semibold text-foreground">
                  {followingQ.data?.actors?.length ?? 0}
                </span>{" "}
                following
              </span>
              <span>·</span>
              <span>
                <span className="font-semibold text-foreground">
                  {followersQ.data?.actors?.length ?? 0}
                </span>{" "}
                followers
              </span>
            </div>

            <Tabs defaultValue="following">
              <div className="flex items-center justify-between">
                <TabsList>
                  <TabsTrigger value="following">
                    <Users className="mr-1.5 h-3.5 w-3.5" />
                    Following
                  </TabsTrigger>
                  <TabsTrigger value="followers">
                    <Eye className="mr-1.5 h-3.5 w-3.5" />
                    Followers
                  </TabsTrigger>
                </TabsList>
                <Button variant="outline" size="sm" onClick={() => setFollowOpen(true)}>
                  <UserPlus className="mr-1.5 h-3.5 w-3.5" /> Follow Actor
                </Button>
              </div>

              <TabsContent value="following" className="mt-3">
                {followingQ.isLoading ? (
                  <div className="py-4 text-center text-sm text-muted-foreground">
                    Loading…
                  </div>
                ) : (followingQ.data?.actors?.length ?? 0) === 0 ? (
                  <div className="py-6 text-center text-sm text-muted-foreground">
                    Not following anyone yet.
                  </div>
                ) : (
                  <div className="flex flex-col gap-0.5">
                    {(followingQ.data?.actors ?? []).map((a) => (
                      <ActorRow key={a.actor_id} actor={a} />
                    ))}
                  </div>
                )}
              </TabsContent>

              <TabsContent value="followers" className="mt-3">
                {followersQ.isLoading ? (
                  <div className="py-4 text-center text-sm text-muted-foreground">
                    Loading…
                  </div>
                ) : (followersQ.data?.actors?.length ?? 0) === 0 ? (
                  <div className="py-6 text-center text-sm text-muted-foreground">
                    No followers yet.
                  </div>
                ) : (
                  <div className="flex flex-col gap-0.5">
                    {(followersQ.data?.actors ?? []).map((a) => (
                      <ActorRow key={a.actor_id} actor={a} />
                    ))}
                  </div>
                )}
              </TabsContent>
            </Tabs>
          </CardContent>
        )}
      </Card>

      {/* Join Federation Dialog */}
      <Dialog open={joinOpen} onOpenChange={setJoinOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Join CorvinFed</DialogTitle>
            <DialogDescription>
              Register your actor in the CorvinFed social graph.
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-4">
            <div className="grid gap-1.5">
              <Label htmlFor="join-display-name">Display Name</Label>
              <Input
                id="join-display-name"
                value={joinForm.display_name}
                onChange={(e) =>
                  setJoinForm((f) => ({ ...f, display_name: e.target.value }))
                }
                placeholder="Your actor display name"
              />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="join-host">Host</Label>
              <Input
                id="join-host"
                value={joinForm.host}
                onChange={(e) => setJoinForm((f) => ({ ...f, host: e.target.value }))}
                placeholder="corvin.example.com"
              />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="join-zone">Compliance Zone</Label>
              <Select
                id="join-zone"
                value={joinForm.compliance_zone}
                onChange={(e) =>
                  setJoinForm((f) => ({ ...f, compliance_zone: e.target.value }))
                }
              >
                <option value="eu">EU (GDPR)</option>
                <option value="us">US</option>
                <option value="local">Local only</option>
              </Select>
            </div>
          </div>
          {joinMut.isError && (
            <p className="text-sm text-destructive">
              {joinMut.error instanceof Error ? joinMut.error.message : "Join failed"}
            </p>
          )}
          <DialogFooter>
            <Button variant="ghost" onClick={() => setJoinOpen(false)}>
              Cancel
            </Button>
            <Button
              disabled={joinMut.isPending || !joinForm.display_name || !joinForm.host}
              onClick={() => joinMut.mutate()}
            >
              {joinMut.isPending ? "Joining…" : "Join"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Follow Actor Dialog */}
      <Dialog open={followOpen} onOpenChange={setFollowOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Follow Actor</DialogTitle>
            <DialogDescription>
              Add a federated actor to your following list.
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-4">
            <div className="grid gap-1.5">
              <Label htmlFor="follow-actor-id">Actor ID</Label>
              <Input
                id="follow-actor-id"
                value={followForm.actor_id}
                onChange={(e) =>
                  setFollowForm((f) => ({ ...f, actor_id: e.target.value }))
                }
                placeholder="https://…/users/alice"
              />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="follow-inbox">Inbox URL</Label>
              <Input
                id="follow-inbox"
                value={followForm.inbox_url}
                onChange={(e) =>
                  setFollowForm((f) => ({ ...f, inbox_url: e.target.value }))
                }
                placeholder="https://…/users/alice/inbox"
              />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="follow-pubkey">Public Key (hex)</Label>
              <Input
                id="follow-pubkey"
                value={followForm.public_key_hex}
                onChange={(e) =>
                  setFollowForm((f) => ({ ...f, public_key_hex: e.target.value }))
                }
                placeholder="0x…"
              />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="grid gap-1.5">
                <Label htmlFor="follow-display-name">Display Name (optional)</Label>
                <Input
                  id="follow-display-name"
                  value={followForm.display_name}
                  onChange={(e) =>
                    setFollowForm((f) => ({ ...f, display_name: e.target.value }))
                  }
                />
              </div>
              <div className="grid gap-1.5">
                <Label htmlFor="follow-zone">Compliance Zone (optional)</Label>
                <Input
                  id="follow-zone"
                  value={followForm.compliance_zone}
                  onChange={(e) =>
                    setFollowForm((f) => ({ ...f, compliance_zone: e.target.value }))
                  }
                  placeholder="eu"
                />
              </div>
            </div>
          </div>
          {followMut.isError && (
            <p className="text-sm text-destructive">
              {followMut.error instanceof Error ? followMut.error.message : "Follow failed"}
            </p>
          )}
          <DialogFooter>
            <Button variant="ghost" onClick={() => setFollowOpen(false)}>
              Cancel
            </Button>
            <Button
              disabled={
                followMut.isPending ||
                !followForm.actor_id ||
                !followForm.inbox_url ||
                !followForm.public_key_hex
              }
              onClick={() => followMut.mutate()}
            >
              {followMut.isPending ? "Following…" : "Follow"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// ── Domains Section ────────────────────────────────────────────────────

function DomainsSection() {
  const qc = useQueryClient();
  const { session } = useAuth();

  const domainsQ = useQuery({
    queryKey: ["space-domains"],
    queryFn: ({ signal }) => getSpaceDomains(signal),
    staleTime: 30_000,
  });

  const [createOpen, setCreateOpen] = React.useState(false);
  const [createForm, setCreateForm] = React.useState({
    slug: "",
    name: "",
    description: "",
    visibility: "public",
  });

  const [publishOpen, setPublishOpen] = React.useState(false);
  const [publishTarget, setPublishTarget] = React.useState<SpaceDomain | null>(null);
  const [publishForm, setPublishForm] = React.useState({
    content: "",
    tags: "",
    visibility: "public",
  });

  const csrf = session?.csrf_token ?? "";

  const createMut = useMutation({
    mutationFn: () =>
      createSpaceDomain(csrf, {
        slug: createForm.slug,
        name: createForm.name,
        description: createForm.description || undefined,
        visibility: createForm.visibility,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["space-domains"] });
      setCreateOpen(false);
      setCreateForm({ slug: "", name: "", description: "", visibility: "public" });
    },
  });

  const deleteMut = useMutation({
    mutationFn: (slug: string) => deleteSpaceDomain(csrf, slug),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["space-domains"] });
    },
  });

  const publishMut = useMutation({
    mutationFn: () => {
      if (!publishTarget) throw new Error("No target domain");
      const tags = publishForm.tags
        ? publishForm.tags
            .split(",")
            .map((t) => t.trim())
            .filter(Boolean)
        : undefined;
      return publishToDomain(csrf, publishTarget.slug, {
        content: publishForm.content,
        tags,
        visibility: publishForm.visibility,
      });
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["space-domains"] });
      setPublishOpen(false);
      setPublishTarget(null);
      setPublishForm({ content: "", tags: "", visibility: "public" });
    },
  });

  const domains = domainsQ.data?.domains ?? [];
  const maxDomains = domainsQ.data?.max_domains ?? 5;
  const licenseUnlimited = domainsQ.data?.license_unlimited ?? false;
  const atLimit = !licenseUnlimited && domains.length >= maxDomains;
  const slots = Math.max(licenseUnlimited ? domains.length + 1 : maxDomains, domains.length);

  function openPublish(domain: SpaceDomain) {
    setPublishTarget(domain);
    setPublishForm({ content: "", tags: "", visibility: domain.visibility });
    setPublishOpen(true);
  }

  if (domainsQ.isLoading) {
    return (
      <div className="py-10 text-center text-sm text-muted-foreground">
        <RefreshCw className="mx-auto mb-2 h-5 w-5 animate-spin" />
        Loading domains…
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <div className="text-sm text-muted-foreground">
          {licenseUnlimited
            ? `${domains.length} domain${domains.length !== 1 ? "s" : ""}`
            : `${domains.length} / ${maxDomains} domain${maxDomains !== 1 ? "s" : ""} used`}
        </div>
        {!atLimit && (
          <Button size="sm" variant="outline" onClick={() => setCreateOpen(true)}>
            <Plus className="mr-1.5 h-3.5 w-3.5" /> New Domain
          </Button>
        )}
      </div>

      {/* Upgrade nudge — shown only on free tier when limit is reached */}
      {atLimit && (
        <div className="flex items-start gap-3 rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm">
          <span className="mt-0.5 text-amber-500">⚡</span>
          <div className="flex-1">
            <span className="font-medium text-amber-600 dark:text-amber-400">
              Free tier — 1 domain included.
            </span>{" "}
            <a
              href="https://corvin-labs.com/pricing"
              target="_blank"
              rel="noreferrer"
              className="underline underline-offset-2 hover:text-amber-500"
            >
              Upgrade
            </a>{" "}
            for unlimited public domains.
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {Array.from({ length: slots }, (_, i) => {
          const domain = domains[i];
          if (domain) {
            return (
              <DomainCard
                key={domain.slug}
                domain={domain}
                onPublish={openPublish}
                onDelete={(slug) => deleteMut.mutate(slug)}
                isDeleting={deleteMut.isPending}
              />
            );
          }
          if (!atLimit && (licenseUnlimited ? i === domains.length : i < maxDomains)) {
            return <EmptyDomainSlot key={`empty-${i}`} onAdd={() => setCreateOpen(true)} />;
          }
          return null;
        })}
      </div>

      {/* Create Domain Dialog */}
      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Create Domain</DialogTitle>
            <DialogDescription>
              A domain is a named publishing channel (e.g. a blog or newsletter).
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-4">
            <div className="grid grid-cols-2 gap-4">
              <div className="grid gap-1.5">
                <Label htmlFor="create-slug">Slug</Label>
                <Input
                  id="create-slug"
                  value={createForm.slug}
                  onChange={(e) =>
                    setCreateForm((f) => ({
                      ...f,
                      slug: e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "-"),
                    }))
                  }
                  placeholder="my-blog"
                />
              </div>
              <div className="grid gap-1.5">
                <Label htmlFor="create-name">Name</Label>
                <Input
                  id="create-name"
                  value={createForm.name}
                  onChange={(e) => setCreateForm((f) => ({ ...f, name: e.target.value }))}
                  placeholder="My Blog"
                />
              </div>
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="create-description">Description (optional)</Label>
              <Input
                id="create-description"
                value={createForm.description}
                onChange={(e) =>
                  setCreateForm((f) => ({ ...f, description: e.target.value }))
                }
                placeholder="What this domain is about…"
              />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="create-visibility">Visibility</Label>
              <Select
                id="create-visibility"
                value={createForm.visibility}
                onChange={(e) =>
                  setCreateForm((f) => ({ ...f, visibility: e.target.value }))
                }
              >
                <option value="public">Public</option>
                <option value="followers">Followers only</option>
                <option value="private">Private</option>
              </Select>
            </div>
          </div>
          {createMut.isError && (
            <p className="text-sm text-destructive">
              {createMut.error instanceof Error ? createMut.error.message : "Create failed"}
            </p>
          )}
          <DialogFooter>
            <Button variant="ghost" onClick={() => setCreateOpen(false)}>
              Cancel
            </Button>
            <Button
              disabled={createMut.isPending || !createForm.slug || !createForm.name}
              onClick={() => createMut.mutate()}
            >
              {createMut.isPending ? "Creating…" : "Create"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Publish to Domain Dialog */}
      <Dialog open={publishOpen} onOpenChange={setPublishOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>
              Publish to{" "}
              <span className="text-accent">{publishTarget?.name ?? ""}</span>
            </DialogTitle>
            <DialogDescription>
              Write your post content. Supports plain text or Markdown.
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-4">
            <div className="grid gap-1.5">
              <Label htmlFor="publish-content">Content</Label>
              <Textarea
                id="publish-content"
                rows={6}
                value={publishForm.content}
                onChange={(e) =>
                  setPublishForm((f) => ({ ...f, content: e.target.value }))
                }
                placeholder="Write your post here…"
              />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="grid gap-1.5">
                <Label htmlFor="publish-tags">Tags (comma-separated, optional)</Label>
                <Input
                  id="publish-tags"
                  value={publishForm.tags}
                  onChange={(e) =>
                    setPublishForm((f) => ({ ...f, tags: e.target.value }))
                  }
                  placeholder="ai, open-source, corvinios"
                />
              </div>
              <div className="grid gap-1.5">
                <Label htmlFor="publish-visibility">Visibility</Label>
                <Select
                  id="publish-visibility"
                  value={publishForm.visibility}
                  onChange={(e) =>
                    setPublishForm((f) => ({ ...f, visibility: e.target.value }))
                  }
                >
                  <option value="public">Public</option>
                  <option value="followers">Followers only</option>
                  <option value="private">Private</option>
                </Select>
              </div>
            </div>
          </div>
          {publishMut.isError && (
            <p className="text-sm text-destructive">
              {publishMut.error instanceof Error
                ? publishMut.error.message
                : "Publish failed"}
            </p>
          )}
          <DialogFooter>
            <Button variant="ghost" onClick={() => setPublishOpen(false)}>
              Cancel
            </Button>
            <Button
              disabled={publishMut.isPending || !publishForm.content.trim()}
              onClick={() => publishMut.mutate()}
            >
              <Send className="mr-1.5 h-3.5 w-3.5" />
              {publishMut.isPending ? "Publishing…" : "Publish"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// ── Grants Section ─────────────────────────────────────────────────────

function GrantsSection() {
  const qc = useQueryClient();
  const { session } = useAuth();
  const csrf = session?.csrf_token ?? "";

  const grantsQ = useQuery({
    queryKey: ["grants"],
    queryFn: ({ signal }) => listGrants({}, signal),
    staleTime: 20_000,
  });

  const templatesQ = useQuery({
    queryKey: ["grant-templates"],
    queryFn: ({ signal }) => listGrantTemplates(signal),
    staleTime: 300_000,
  });

  const [issueOpen, setIssueOpen] = React.useState(false);
  const [prefill, setPrefill] = React.useState<GrantTemplate | null>(null);
  const [form, setForm] = React.useState({ grantee_actor: "", capabilities: "" });

  function openWithTemplate(t: GrantTemplate) {
    setPrefill(t);
    setForm({ grantee_actor: "", capabilities: t.capabilities.join(", ") });
    setIssueOpen(true);
  }

  function openManual() {
    setPrefill(null);
    setForm({ grantee_actor: "", capabilities: "" });
    setIssueOpen(true);
  }

  const issueMut = useMutation({
    mutationFn: () =>
      createGrant(
        {
          grantee_actor: form.grantee_actor,
          capabilities: form.capabilities
            .split(",")
            .map((c) => c.trim())
            .filter(Boolean),
        },
        csrf,
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["grants"] });
      setIssueOpen(false);
    },
  });

  const revokeMut = useMutation({
    mutationFn: (grant_id: string) => revokeGrant(grant_id, csrf),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["grants"] }),
  });

  const actorId = grantsQ.data?.local_actor_id ?? "";
  const active = (grantsQ.data?.grants ?? []).filter((g) => !g.revoked_at);

  if (grantsQ.isLoading) {
    return (
      <div className="py-10 text-center text-sm text-muted-foreground">
        <RefreshCw className="mx-auto mb-2 h-5 w-5 animate-spin" />
        Loading grants…
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      {/* Quick-start templates */}
      <Card>
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between">
            <div>
              <CardTitle className="text-lg">Capability Grants</CardTitle>
              <CardDescription>
                Ed25519-signed grants that control who can access your data, agents, and domains.
              </CardDescription>
            </div>
            <Button size="sm" onClick={openManual}>
              <Plus className="mr-1.5 h-3.5 w-3.5" /> New Grant
            </Button>
          </div>
        </CardHeader>
        {actorId && (
          <CardContent className="pb-3">
            <div className="rounded-md bg-muted/50 px-3 py-2">
              <div className="text-xs text-muted-foreground">Your Actor ID (grantor)</div>
              <div className="font-mono text-sm">{actorId}</div>
            </div>
          </CardContent>
        )}
      </Card>

      {/* Template cards */}
      {(templatesQ.data?.templates ?? []).length > 0 && (
        <div>
          <h3 className="mb-3 text-sm font-medium text-muted-foreground">Quick Templates</h3>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {(templatesQ.data?.templates ?? []).map((t) => (
              <button
                key={t.id}
                onClick={() => openWithTemplate(t)}
                className="flex flex-col gap-2 rounded-lg border border-border/60 px-4 py-3 text-left transition-colors hover:border-accent/50 hover:bg-accent/5"
              >
                <div className="flex items-center gap-2">
                  <Shield className="h-4 w-4 text-accent" />
                  <span className="text-sm font-medium">{t.label}</span>
                  {t.requires_confirmation && (
                    <Badge variant="outline" className="ml-auto text-[10px] text-amber-600">
                      Careful
                    </Badge>
                  )}
                </div>
                <p className="text-xs text-muted-foreground">{t.description}</p>
                <div className="flex flex-wrap gap-1">
                  {t.capabilities.map((c) => (
                    <Badge key={c} variant="outline" className="font-mono text-[9px]">
                      {c}
                    </Badge>
                  ))}
                </div>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Active grants list */}
      <div>
        <h3 className="mb-3 text-sm font-medium text-muted-foreground">
          Active Grants ({active.length})
        </h3>
        {active.length === 0 ? (
          <div className="rounded-lg border border-dashed border-border py-10 text-center text-sm text-muted-foreground">
            No active grants — use a template or create one manually.
          </div>
        ) : (
          <div className="flex flex-col gap-2">
            {active.map((g) => (
              <div
                key={g.grant_id}
                className="flex items-start gap-3 rounded-lg border border-border/60 px-4 py-3"
              >
                <KeyRound className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
                <div className="min-w-0 flex-1">
                  <div className="truncate font-mono text-sm font-medium">
                    {g.grantee_actor}
                  </div>
                  <div className="mt-1.5 flex flex-wrap gap-1">
                    {g.capabilities.map((c) => (
                      <Badge key={c} variant="outline" className="font-mono text-[10px]">
                        {c}
                      </Badge>
                    ))}
                  </div>
                  <div className="mt-1 text-xs text-muted-foreground">
                    Issued{" "}
                    {g.issued_at
                      ? new Date(g.issued_at * 1000).toLocaleDateString("en-GB", {
                          day: "2-digit",
                          month: "short",
                          year: "numeric",
                        })
                      : "—"}
                  </div>
                </div>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-7 w-7 shrink-0 text-muted-foreground hover:text-destructive"
                  disabled={revokeMut.isPending}
                  onClick={() => {
                    if (window.confirm("Revoke this grant?"))
                      revokeMut.mutate(g.grant_id!);
                  }}
                >
                  <X className="h-3.5 w-3.5" />
                </Button>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Issue Grant Dialog */}
      <Dialog open={issueOpen} onOpenChange={setIssueOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>
              {prefill ? `Issue "${prefill.label}" Grant` : "Issue Grant"}
            </DialogTitle>
            <DialogDescription>
              The grant is signed with your personal Ed25519 key.
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-4">
            <div className="grid gap-1.5">
              <Label htmlFor="grant-grantee">Grantee Actor ID</Label>
              <Input
                id="grant-grantee"
                value={form.grantee_actor}
                onChange={(e) => setForm((f) => ({ ...f, grantee_actor: e.target.value }))}
                placeholder="@alice@example.com or https://…"
              />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="grant-caps">Capabilities (comma-separated)</Label>
              <Input
                id="grant-caps"
                value={form.capabilities}
                onChange={(e) => setForm((f) => ({ ...f, capabilities: e.target.value }))}
                placeholder="domain.*.read, a2a.send"
              />
            </div>
            {prefill?.requires_confirmation && (
              <p className="rounded-md bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-400">
                This is a full-trust grant. Use with care.
              </p>
            )}
          </div>
          {issueMut.isError && (
            <p className="text-sm text-destructive">
              {issueMut.error instanceof Error ? issueMut.error.message : "Failed"}
            </p>
          )}
          <DialogFooter>
            <Button variant="ghost" onClick={() => setIssueOpen(false)}>
              Cancel
            </Button>
            <Button
              disabled={
                issueMut.isPending || !form.grantee_actor || !form.capabilities
              }
              onClick={() => issueMut.mutate()}
            >
              {issueMut.isPending ? "Issuing…" : "Issue Grant"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// ── Main Page ──────────────────────────────────────────────────────────

export function SpacePage() {
  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">CorvinSpace</h1>
          <p className="mt-0.5 text-sm text-muted-foreground">
            Your personal publishing and social graph hub
          </p>
        </div>
        <Badge variant="outline" className="text-xs">
          Beta
        </Badge>
      </div>

      <Tabs defaultValue="profile">
        <TabsList>
          <TabsTrigger value="profile">
            <Users className="mr-1.5 h-4 w-4" />
            Profile &amp; Social
          </TabsTrigger>
          <TabsTrigger value="domains">
            <BookOpen className="mr-1.5 h-4 w-4" />
            Domains
          </TabsTrigger>
          <TabsTrigger value="grants">
            <Shield className="mr-1.5 h-4 w-4" />
            Grants
          </TabsTrigger>
        </TabsList>

        <TabsContent value="profile" className="mt-6">
          <ProfileSection />
        </TabsContent>

        <TabsContent value="domains" className="mt-6">
          <DomainsSection />
        </TabsContent>

        <TabsContent value="grants" className="mt-6">
          <GrantsSection />
        </TabsContent>
      </Tabs>
    </div>
  );
}
