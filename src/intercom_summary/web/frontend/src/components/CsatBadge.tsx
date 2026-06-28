import { Badge } from "@/components/ui/primitives";
import { isLowCsat } from "@/lib/utils";

// Renders an Intercom CSAT rating (1-5). Low ratings get a red "needs attention"
// treatment; missing ratings render as an em dash. An optional dispute status adds a
// marker: an open dispute is flagged amber, an accepted one voids the rating ("excluded").
export default function CsatBadge({
  rating,
  disputeStatus,
}: {
  rating: number | null | undefined;
  disputeStatus?: string | null;
}) {
  if (rating == null) {
    return <span className="text-muted-foreground">—</span>;
  }

  if (disputeStatus === "accepted") {
    return (
      <span className="inline-flex items-center gap-1 text-muted-foreground">
        <span className="line-through">{rating}/5</span>
        <span className="text-[10px] uppercase tracking-wide">excluded</span>
      </span>
    );
  }

  const value = isLowCsat(rating) ? (
    <Badge className="border-destructive/40 bg-destructive/10 text-destructive">
      ⚠ {rating}/5
    </Badge>
  ) : (
    <span className="tabular-nums text-foreground">{rating}/5</span>
  );

  if (disputeStatus === "open") {
    return (
      <span className="inline-flex items-center gap-1">
        {value}
        <span className="text-amber-500" title="CSAT dispute pending">
          ⚖
        </span>
      </span>
    );
  }
  return value;
}
