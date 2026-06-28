import { Badge } from "@/components/ui/primitives";
import { isLowCsat } from "@/lib/utils";

// Renders an Intercom CSAT rating (1-5). Low ratings get a red "needs attention"
// treatment; missing ratings render as an em dash.
export default function CsatBadge({ rating }: { rating: number | null | undefined }) {
  if (rating == null) {
    return <span className="text-muted-foreground">—</span>;
  }
  if (isLowCsat(rating)) {
    return (
      <Badge className="border-destructive/40 bg-destructive/10 text-destructive">
        ⚠ {rating}/5
      </Badge>
    );
  }
  return <span className="tabular-nums text-foreground">{rating}/5</span>;
}
