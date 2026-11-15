// Author: @SaadZarook
import { cva, type VariantProps } from "class-variance-authority";
import type { ComponentProps } from "react";
import { cn } from "./cn";

// One size, one colour per outcome (rule 9). Filled chips describe data; outlined ones, Error and
// Not configured, describe the system (web/AGENTS.md).
const badgeClass = cva(
  "box-border inline-flex h-6 shrink-0 items-center whitespace-nowrap rounded-full border border-transparent px-2 text-xs font-semibold",
  {
    variants: {
      tone: {
        found: "bg-found-bg text-found-fg",
        missing: "bg-missing-bg text-missing-fg",
        waiting: "bg-waiting-bg text-waiting-fg",
        blocked: "bg-blocked-bg text-blocked-fg",
        ambiguous: "bg-ambiguous-bg text-ambiguous-fg",
        stale: "bg-stale-bg text-stale-fg",
        error: "border-production-text bg-card text-production-text",
        "not-configured": "border-dashed border-muted bg-card text-muted",
        neutral: "bg-blocked-bg text-blocked-fg",
        production: "bg-production text-on-production",
      },
    },
    defaultVariants: { tone: "neutral" },
  },
);

export type BadgeTone = NonNullable<VariantProps<typeof badgeClass>["tone"]>;

/** A status chip: 24 px, sentence case, never clipped. */
export function Badge({ tone, className, ...props }: ComponentProps<"span"> & VariantProps<typeof badgeClass>) {
  return <span className={cn(badgeClass({ tone }), className)} {...props} />;
}
