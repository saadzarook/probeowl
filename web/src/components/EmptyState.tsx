// Author: @SaadZarook
import type { ReactNode } from "react";
import { guidePage } from "../guide";
import { OwlMark } from "./OwlMark";

interface EmptyStateProps {
  title: string;
  message: ReactNode;
  /** What to do next: buttons, a command to copy, links to the guide. */
  children?: ReactNode;
  /** 1 where the empty state is the whole page, so the page still has its one main heading. */
  level?: 1 | 2;
}

/** Empty state with the owl mark; the owl appears in empty states only. */
export function EmptyState({ title, message, children, level = 2 }: EmptyStateProps) {
  const Heading = level === 1 ? "h1" : "h2";
  return (
    <section className="empty-state" aria-label={title}>
      <OwlMark />
      <Heading>{title}</Heading>
      <p>{message}</p>
      {children && <div className="empty-actions">{children}</div>}
    </section>
  );
}

/** A link to a page of the guide, opening in a new tab. */
export function GuideLink({ page, children }: { page: string; children: ReactNode }) {
  return (
    <a href={guidePage(page)} target="_blank" rel="noreferrer">
      {children}
    </a>
  );
}
