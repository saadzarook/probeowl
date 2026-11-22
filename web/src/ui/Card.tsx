// Author: @SaadZarook
import type { ComponentProps } from "react";
import { cn } from "./cn";

/** A card: a bordered section on the page's surface. */
export function Card({ className, ...props }: ComponentProps<"section">) {
  return <section className={cn("rounded-lg border border-border bg-card p-4 text-foreground md:p-6", className)} {...props} />;
}
