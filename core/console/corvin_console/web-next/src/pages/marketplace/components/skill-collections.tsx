/**
 * Skill Collections Component (Phase 1 Session 1).
 *
 * Displays pre-curated skill bundles for common use cases.
 */

import { useQuery } from "@tanstack/react-query";
import { Clock, BookOpen, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { getCollections } from "../discovery-api";

export function SkillCollections() {
  const { data, isLoading } = useQuery({
    queryKey: ["marketplace", "collections"],
    queryFn: () => getCollections(),
  });

  if (isLoading) {
    return (
      <div className="flex justify-center py-8">
        <Loader2 className="w-6 h-6 animate-spin text-gray-400" />
      </div>
    );
  }

  if (!data?.collections || data.collections.length === 0) {
    return null;
  }

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-xl font-semibold mb-2">Curated Collections</h2>
        <p className="text-sm text-gray-600">
          Pre-curated skill bundles for common use cases
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {data.collections.map((collection) => (
          <Card key={collection.id} className="flex flex-col">
            <CardHeader className="pb-3">
              <div className="flex items-start justify-between">
                <div className="text-3xl">{collection.icon}</div>
                <span className="text-xs bg-blue-100 text-blue-800 px-2 py-1 rounded">
                  {collection.difficulty}
                </span>
              </div>
              <CardTitle className="text-lg mt-3">{collection.name}</CardTitle>
            </CardHeader>

            <CardContent className="flex-1 flex flex-col space-y-3">
              <p className="text-sm text-gray-600">{collection.description}</p>

              <div className="text-sm space-y-1">
                <p className="font-semibold text-gray-700">Use Case</p>
                <p className="text-gray-600">{collection.target_use_case}</p>
              </div>

              <div className="flex items-center gap-4 text-xs text-gray-500">
                <div className="flex items-center gap-1">
                  <BookOpen className="w-4 h-4" />
                  {collection.skills.length} skills
                </div>
                <div className="flex items-center gap-1">
                  <Clock className="w-4 h-4" />
                  {collection.estimated_setup_time_minutes}min
                </div>
              </div>

              <Button size="sm" className="mt-auto">
                Browse Collection
              </Button>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}
