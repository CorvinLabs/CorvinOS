/**
 * Marketplace Skills Component — Browse, Install, Rate OS-Skills
 *
 * Features:
 * - Grid view of available skills with metadata
 * - Search and filter by category/layer
 * - Installation with progress tracking
 * - Skill ratings and reviews
 * - Tenant-scoped installation
 *
 * ADR-0535+: OS-Skills as discoverable marketplace items
 */

import React, { useEffect, useState } from "react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { AlertCircle, Download, Star, Trash2, Clock, CheckCircle2 } from "lucide-react";
import { Alert, AlertDescription } from "@/components/ui/alert";

interface Skill {
  id: string;
  name: string;
  version: string;
  description: string;
  category: string;
  tier: "buildin" | "contributor";
  l_layer: string;
  author: string;
  requires_approval: boolean;
  install_count: number;
  avg_rating?: number;
  rating_count: number;
}

interface SkillsListResponse {
  skills: Skill[];
  count: number;
  filters: Record<string, any>;
  total_available: number;
  tenant_id: string;
  timestamp: string;
}

interface SkillDetailsResponse {
  skill: Skill;
  reviews: SkillReview[];
  review_count: number;
  installed: boolean;
  installed_info?: Record<string, any>;
  requires_approval: boolean;
  tenant_id: string;
  timestamp: string;
}

interface SkillReview {
  review_id: string;
  skill_id: string;
  tenant_id: string;
  rating: number;
  comment?: string;
  timestamp: string;
}

interface InstallJob {
  job_id: string;
  skill_id: string;
  status: "pending" | "installing" | "completed" | "failed";
  progress: number;
  message: string;
  error?: string;
}

type ViewMode = "browse" | "details";

interface SkillsMarketplaceProps {
  onInstallComplete?: (skillId: string) => void;
}

export function SkillsMarketplace({ onInstallComplete }: SkillsMarketplaceProps) {
  const [viewMode, setViewMode] = useState<ViewMode>("browse");
  const [skills, setSkills] = useState<Skill[]>([]);
  const [selectedSkill, setSelectedSkill] = useState<Skill | null>(null);
  const [loading, setLoading] = useState(true);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [categoryFilter, setCategoryFilter] = useState<string | null>(null);
  const [layerFilter, setLayerFilter] = useState<string | null>(null);
  const [installingSkills, setInstallingSkills] = useState<Set<string>>(new Set());
  const [userRating, setUserRating] = useState<number | null>(null);
  const [ratingComment, setRatingComment] = useState("");

  // Fetch skills list
  useEffect(() => {
    fetchSkills();
  }, [categoryFilter, layerFilter]);

  const fetchSkills = async () => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams();
      if (categoryFilter) params.append("category", categoryFilter);
      if (layerFilter) params.append("layer", layerFilter);
      params.append("limit", "100");

      const response = await fetch(`/v1/console/marketplace/skills?${params}`, {
        credentials: "include",
      });

      if (!response.ok) {
        throw new Error(`Failed to fetch skills: ${response.statusText}`);
      }

      const data: SkillsListResponse = await response.json();
      setSkills(data.skills);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load skills");
    } finally {
      setLoading(false);
    }
  };

  const handleSearch = async (query: string) => {
    setSearchQuery(query);

    if (!query || query.length < 2) {
      fetchSkills();
      return;
    }

    setSearching(true);
    try {
      const response = await fetch(
        `/v1/console/marketplace/skills/search?q=${encodeURIComponent(query)}`,
        { credentials: "include" }
      );

      if (!response.ok) {
        throw new Error("Search failed");
      }

      const data = await response.json();
      setSkills(data.skills);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Search failed");
    } finally {
      setSearching(false);
    }
  };

  const handleSelectSkill = async (skill: Skill) => {
    setSelectedSkill(skill);
    setViewMode("details");
    setUserRating(null);
    setRatingComment("");

    // Fetch detailed skill info
    try {
      const response = await fetch(`/v1/console/marketplace/skills/${skill.id}`, {
        credentials: "include",
      });

      if (response.ok) {
        const data: SkillDetailsResponse = await response.json();
        setSelectedSkill(data.skill);
      }
    } catch (err) {
      // Non-critical; show what we have
      console.error("Failed to fetch skill details:", err);
    }
  };

  const handleInstallSkill = async (skillId: string) => {
    setInstallingSkills((prev) => new Set([...prev, skillId]));
    try {
      const response = await fetch(`/v1/console/marketplace/skills/${skillId}/install`, {
        method: "POST",
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": getCsrfToken(),
        },
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || "Installation failed");
      }

      const job: InstallJob = await response.json();

      // Poll job status
      await pollInstallProgress(job.job_id);

      // Refresh skills list
      fetchSkills();
      onInstallComplete?.(skillId);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Installation failed");
    } finally {
      setInstallingSkills((prev) => {
        const next = new Set(prev);
        next.delete(skillId);
        return next;
      });
    }
  };

  const pollInstallProgress = async (jobId: string, maxAttempts = 30) => {
    for (let i = 0; i < maxAttempts; i++) {
      try {
        const response = await fetch(
          `/v1/console/marketplace/skills/install/${jobId}/progress`,
          { credentials: "include" }
        );

        if (!response.ok) {
          throw new Error("Failed to get install progress");
        }

        const job: InstallJob = await response.json();

        if (job.status === "completed" || job.status === "failed") {
          if (job.status === "failed") {
            setError(job.error || "Installation failed");
          }
          return;
        }

        // Wait before next poll
        await new Promise((resolve) => setTimeout(resolve, 500));
      } catch (err) {
        console.error("Error polling install progress:", err);
        break;
      }
    }
  };

  const handleUninstallSkill = async (skillId: string) => {
    if (!confirm("Are you sure you want to uninstall this skill?")) {
      return;
    }

    try {
      const response = await fetch(`/v1/console/marketplace/skills/${skillId}/uninstall`, {
        method: "POST",
        credentials: "include",
        headers: {
          "X-CSRF-Token": getCsrfToken(),
        },
      });

      if (!response.ok) {
        throw new Error("Uninstallation failed");
      }

      fetchSkills();
      setViewMode("browse");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Uninstallation failed");
    }
  };

  const handleRateSkill = async (skillId: string, rating: number) => {
    try {
      const response = await fetch(`/v1/console/marketplace/skills/${skillId}/rate`, {
        method: "POST",
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": getCsrfToken(),
        },
        body: JSON.stringify({
          rating,
          comment: ratingComment || null,
        }),
      });

      if (!response.ok) {
        throw new Error("Failed to submit rating");
      }

      setUserRating(rating);
      setRatingComment("");
      // Refresh skill details
      if (selectedSkill) {
        handleSelectSkill(selectedSkill);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to submit rating");
    }
  };

  const getCsrfToken = (): string => {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? (meta as HTMLMetaElement).content : "";
  };

  // Category and Layer options
  const categories = Array.from(
    new Set(skills.flatMap((s) => [s.category]))
  ).filter(Boolean);
  const layers = Array.from(
    new Set(skills.flatMap((s) => [s.l_layer]))
  ).filter(Boolean);

  if (viewMode === "details" && selectedSkill) {
    return (
      <SkillDetailsPanel
        skill={selectedSkill}
        onBack={() => {
          setViewMode("browse");
          setSelectedSkill(null);
        }}
        onInstall={() => handleInstallSkill(selectedSkill.id)}
        onUninstall={() => handleUninstallSkill(selectedSkill.id)}
        onRate={handleRateSkill}
        isInstalling={installingSkills.has(selectedSkill.id)}
        userRating={userRating}
        setUserRating={setUserRating}
        ratingComment={ratingComment}
        setRatingComment={setRatingComment}
      />
    );
  }

  return (
    <div className="space-y-6">
      {error && (
        <Alert variant="destructive">
          <AlertCircle className="h-4 w-4" />
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {/* Search Bar */}
      <div className="space-y-4">
        <Input
          placeholder="Search skills by name or description..."
          value={searchQuery}
          onChange={(e) => handleSearch(e.target.value)}
          disabled={searching || loading}
          className="w-full"
        />
      </div>

      {/* Filters */}
      <div className="flex gap-4 flex-wrap">
        <div className="flex gap-2">
          <span className="text-sm font-medium text-gray-600">Category:</span>
          {categories.map((cat) => (
            <Badge
              key={cat}
              variant={categoryFilter === cat ? "default" : "outline"}
              className="cursor-pointer"
              onClick={() => setCategoryFilter(categoryFilter === cat ? null : cat)}
            >
              {cat}
            </Badge>
          ))}
        </div>

        <div className="flex gap-2">
          <span className="text-sm font-medium text-gray-600">Layer:</span>
          {layers.map((layer) => (
            <Badge
              key={layer}
              variant={layerFilter === layer ? "default" : "outline"}
              className="cursor-pointer"
              onClick={() => setLayerFilter(layerFilter === layer ? null : layer)}
            >
              {layer}
            </Badge>
          ))}
        </div>
      </div>

      {/* Skills Grid */}
      {loading ? (
        <div className="text-center py-12">
          <Clock className="h-8 w-8 mx-auto mb-2 animate-spin text-gray-400" />
          <p className="text-gray-600">Loading skills...</p>
        </div>
      ) : skills.length === 0 ? (
        <div className="text-center py-12">
          <p className="text-gray-600">
            {searchQuery ? "No skills match your search" : "No skills available"}
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {skills.map((skill) => (
            <SkillCard
              key={skill.id}
              skill={skill}
              onSelect={() => handleSelectSkill(skill)}
              onInstall={() => handleInstallSkill(skill.id)}
              isInstalling={installingSkills.has(skill.id)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

interface SkillCardProps {
  skill: Skill;
  onSelect: () => void;
  onInstall: () => void;
  isInstalling: boolean;
}

function SkillCard({ skill, onSelect, onInstall, isInstalling }: SkillCardProps) {
  return (
    <Card className="hover:shadow-lg transition-shadow cursor-pointer">
      <CardHeader onClick={onSelect} className="pb-3">
        <div className="flex items-start justify-between">
          <div className="flex-1">
            <CardTitle className="text-lg">{skill.name}</CardTitle>
            <CardDescription className="text-xs mt-1">
              {skill.author}
            </CardDescription>
          </div>
          <Badge variant={skill.tier === "buildin" ? "default" : "secondary"}>
            {skill.tier}
          </Badge>
        </div>
      </CardHeader>

      <CardContent className="space-y-3">
        <p className="text-sm text-gray-700">{skill.description}</p>

        <div className="flex gap-2">
          <Badge variant="outline">{skill.l_layer}</Badge>
          <Badge variant="outline">{skill.category}</Badge>
        </div>

        {/* Rating Display */}
        <div className="flex items-center gap-1 text-sm">
          <Star className="h-4 w-4 fill-yellow-400 text-yellow-400" />
          <span>
            {skill.avg_rating ? skill.avg_rating.toFixed(1) : "No ratings"}
          </span>
          <span className="text-gray-500">({skill.rating_count})</span>
        </div>

        {/* Install Button */}
        <Button
          onClick={(e) => {
            e.stopPropagation();
            onInstall();
          }}
          disabled={isInstalling}
          className="w-full"
        >
          {isInstalling ? (
            <>
              <Clock className="h-4 w-4 mr-2 animate-spin" />
              Installing...
            </>
          ) : (
            <>
              <Download className="h-4 w-4 mr-2" />
              Install
            </>
          )}
        </Button>
      </CardContent>
    </Card>
  );
}

interface SkillDetailsPanelProps {
  skill: Skill;
  onBack: () => void;
  onInstall: () => void;
  onUninstall: () => void;
  onRate: (skillId: string, rating: number) => void;
  isInstalling: boolean;
  userRating: number | null;
  setUserRating: (rating: number | null) => void;
  ratingComment: string;
  setRatingComment: (comment: string) => void;
}

function SkillDetailsPanel({
  skill,
  onBack,
  onInstall,
  onUninstall,
  onRate,
  isInstalling,
  userRating,
  setUserRating,
  ratingComment,
  setRatingComment,
}: SkillDetailsPanelProps) {
  const [skillDetails, setSkillDetails] = useState<SkillDetailsResponse | null>(null);
  const [loadingDetails, setLoadingDetails] = useState(true);

  useEffect(() => {
    const fetchDetails = async () => {
      try {
        const response = await fetch(`/v1/console/marketplace/skills/${skill.id}`, {
          credentials: "include",
        });

        if (response.ok) {
          const data: SkillDetailsResponse = await response.json();
          setSkillDetails(data);
        }
      } catch (err) {
        console.error("Failed to fetch skill details:", err);
      } finally {
        setLoadingDetails(false);
      }
    };

    fetchDetails();
  }, [skill.id]);

  if (loadingDetails) {
    return (
      <div className="space-y-4">
        <Button variant="outline" onClick={onBack}>
          ← Back
        </Button>
        <div className="text-center py-12">
          <Clock className="h-8 w-8 mx-auto mb-2 animate-spin text-gray-400" />
        </div>
      </div>
    );
  }

  const installed = skillDetails?.installed ?? false;

  return (
    <div className="space-y-6">
      <Button variant="outline" onClick={onBack}>
        ← Back
      </Button>

      <Card>
        <CardHeader>
          <div className="flex items-start justify-between">
            <div>
              <CardTitle className="text-2xl">{skill.name}</CardTitle>
              <CardDescription className="mt-2">{skill.description}</CardDescription>
            </div>
            <Badge variant={skill.tier === "buildin" ? "default" : "secondary"}>
              {skill.tier}
            </Badge>
          </div>
        </CardHeader>

        <CardContent className="space-y-6">
          {/* Metadata */}
          <div className="grid grid-cols-2 gap-4 text-sm">
            <div>
              <span className="font-medium">Version:</span>
              <p className="text-gray-600">{skill.version}</p>
            </div>
            <div>
              <span className="font-medium">Layer:</span>
              <p className="text-gray-600">{skill.l_layer}</p>
            </div>
            <div>
              <span className="font-medium">Category:</span>
              <p className="text-gray-600">{skill.category}</p>
            </div>
            <div>
              <span className="font-medium">Author:</span>
              <p className="text-gray-600">{skill.author}</p>
            </div>
          </div>

          {/* Status */}
          <div className="space-y-2">
            {installed ? (
              <div className="flex items-center gap-2 text-green-700 bg-green-50 p-3 rounded">
                <CheckCircle2 className="h-5 w-5" />
                <span>Installed on this tenant</span>
              </div>
            ) : null}

            {skill.requires_approval ? (
              <Alert>
                <AlertCircle className="h-4 w-4" />
                <AlertDescription>
                  This skill requires operator approval for security reasons.
                </AlertDescription>
              </Alert>
            ) : null}
          </div>

          {/* Action Buttons */}
          <div className="flex gap-4">
            {!installed ? (
              <Button
                onClick={onInstall}
                disabled={isInstalling}
                className="flex-1"
              >
                {isInstalling ? (
                  <>
                    <Clock className="h-4 w-4 mr-2 animate-spin" />
                    Installing...
                  </>
                ) : (
                  <>
                    <Download className="h-4 w-4 mr-2" />
                    Install Skill
                  </>
                )}
              </Button>
            ) : (
              <Button
                onClick={onUninstall}
                variant="destructive"
                className="flex-1"
              >
                <Trash2 className="h-4 w-4 mr-2" />
                Uninstall
              </Button>
            )}
          </div>

          {/* Rating Section */}
          {installed && (
            <div className="space-y-4 pt-4 border-t">
              <h3 className="font-medium">Rate This Skill</h3>

              <div className="flex gap-2">
                {[1, 2, 3, 4, 5].map((rating) => (
                  <button
                    key={rating}
                    onClick={() => setUserRating(userRating === rating ? null : rating)}
                    className="transition-transform hover:scale-110"
                  >
                    <Star
                      className={`h-6 w-6 ${
                        userRating && userRating >= rating
                          ? "fill-yellow-400 text-yellow-400"
                          : "text-gray-300"
                      }`}
                    />
                  </button>
                ))}
              </div>

              <input
                type="text"
                placeholder="Add a comment (optional)"
                value={ratingComment}
                onChange={(e) => setRatingComment(e.target.value)}
                className="w-full px-3 py-2 border rounded text-sm"
                maxLength={500}
              />

              <Button
                onClick={() => userRating && onRate(skill.id, userRating)}
                disabled={!userRating}
                className="w-full"
              >
                Submit Rating
              </Button>
            </div>
          )}

          {/* Reviews */}
          {skillDetails?.reviews && skillDetails.reviews.length > 0 && (
            <div className="space-y-4 pt-4 border-t">
              <h3 className="font-medium">Reviews ({skillDetails.review_count})</h3>

              <div className="space-y-3">
                {skillDetails.reviews.slice(0, 5).map((review) => (
                  <div key={review.review_id} className="text-sm border rounded p-3">
                    <div className="flex items-center gap-2 mb-1">
                      <div className="flex">
                        {[...Array(review.rating)].map((_, i) => (
                          <Star
                            key={i}
                            className="h-4 w-4 fill-yellow-400 text-yellow-400"
                          />
                        ))}
                      </div>
                      <span className="text-gray-500 text-xs">
                        {new Date(review.timestamp).toLocaleDateString()}
                      </span>
                    </div>
                    {review.comment && <p className="text-gray-700">{review.comment}</p>}
                  </div>
                ))}
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
