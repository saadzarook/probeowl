// Author: @SaadZarook
import { EmptyState } from "../components/EmptyState";
import type { NavItem } from "../routes";

/** Placeholder for a screen that is not built yet. */
export function PlaceholderPage({ item }: { item: NavItem }) {
  return (
    <>
      <div className="page-header">
        <h1>{item.label}</h1>
        <p>{item.subtitle}</p>
      </div>
      <EmptyState title="Nothing here yet" message="This screen is not built yet." />
    </>
  );
}
