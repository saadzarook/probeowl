// Author: @SaadZarook
import type { CatalogReview, EnvironmentSummary } from "../api/client";

/** "Needs review": suggested labels not yet confirmed and tables that differ from the live schema; hidden when both are 0. */
export function ReviewBanner({ review, environments }: { review: CatalogReview | null | undefined; environments: EnvironmentSummary[] }) {
  if (!review || (review.unconfirmed === 0 && review.drifted === 0)) return null;
  const drifting = environments.filter((e) => review.tables.some((t) => (t.drift ?? []).some((d) => d.environment === e.name)));
  return (
    <div className="review-banner" role="status" aria-label="Needs review">
      <strong>Needs review</strong>
      <span>
        {review.unconfirmed} suggested {review.unconfirmed === 1 ? "label" : "labels"} not yet confirmed · {review.drifted}{" "}
        {review.drifted === 1 ? "table differs" : "tables differ"} from the live schema
        {drifting.length > 0 && ` (${drifting.map((e) => e.title).join(", ")})`}
      </span>
    </div>
  );
}
