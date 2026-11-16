/**
 * "Project | Only for me": which space a page shows. The choice lives in the URL (?space=personal)
 * so it survives a reload and can be linked; the project view is the default.
 */
import { useSearchParams } from "react-router-dom";
import { Button } from "@/components/ui/button";

export type Space = "project" | "personal";

export function useSpace(): [Space, (s: Space) => void] {
  const [params, setParams] = useSearchParams();
  const space: Space = params.get("space") === "personal" ? "personal" : "project";
  const set = (s: Space) => setParams((p) => {
    const n = new URLSearchParams(p);
    if (s === "personal") n.set("space", "personal"); else n.delete("space");
    n.delete("node"); n.delete("item");
    return n;
  }, { replace: true });
  return [space, set];
}

export function SpaceSwitch({ space, onChange }: { space: Space; onChange: (s: Space) => void }) {
  return (
    <div className="inline-flex rounded-md border border-border" role="group" aria-label="Space" data-testid="space-switch">
      <Button size="sm" variant={space === "project" ? "accent" : "ghost"} aria-pressed={space === "project"} onClick={() => onChange("project")}>Project</Button>
      <Button size="sm" variant={space === "personal" ? "accent" : "ghost"} aria-pressed={space === "personal"} onClick={() => onChange("personal")} data-testid="space-personal">Only for me</Button>
    </div>
  );
}
