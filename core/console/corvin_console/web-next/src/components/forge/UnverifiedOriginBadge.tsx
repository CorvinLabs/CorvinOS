import { AlertCircle } from "lucide-react";
import { Badge } from "@/components/ui/badge";

/** Bundle format v1 carries no origin proof: checksums show the archive was not
 *  altered in transit, not who made it. Every imported artifact shows this. */
export function UnverifiedOriginBadge() {
  return (
    <Badge variant="warn" className="gap-1" title="Checksums prove integrity in transit, not who built this bundle.">
      <AlertCircle className="h-3 w-3" />
      Unverified origin
    </Badge>
  );
}

export default UnverifiedOriginBadge;
