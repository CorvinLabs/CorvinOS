/** Shows, in a chat's header, which project the chat is attached to (T-0364). Renders nothing when none is attached
 *  or when the lookup fails - a missing chip must never break the chat. */
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { FolderKanban } from "lucide-react";
import { getChatAttachment } from "@/lib/api/projects";

export function ProjectChip({ sid }: { sid: string }) {
  const q = useQuery({
    queryKey: ["projects", "chat-attachment", sid],
    queryFn: ({ signal }) => getChatAttachment(sid, signal),
    staleTime: 15_000,
    retry: false,
  });
  const id = q.data?.project_id;
  if (!id) return null;
  return (
    <Link
      to={`/app/projects/${encodeURIComponent(id)}?tab=settings`}
      className="inline-flex max-w-[12rem] items-center gap-1 whitespace-nowrap rounded-full border border-border px-2 py-0.5 text-[11px] text-muted-foreground hover:text-foreground"
      data-testid="project-chip"
      title="This chat can follow the project's rules and use its tools. The assistant can read the project but not change its files."
    >
      <FolderKanban className="h-3 w-3 shrink-0" aria-hidden /> <span className="truncate">{q.data?.title || id}</span>
    </Link>
  );
}
