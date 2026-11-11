// Author: @SaadZarook
import { useState } from "react";
import { EmptyState } from "../components/EmptyState";
import { HealthyComparisonSection } from "../components/HealthyComparison";
import { RunResult } from "../components/RunResult";
import { SuggestedNextSteps } from "../components/SuggestedNextSteps";
import { ValidateForm } from "../components/ValidateForm";
import type { NavItem } from "../routes";
import { useSelection } from "../selection";

/** Validate a flow: start a validate probe on one subject and read the run as it fills in. */
export function ValidatePage({ item }: { item: NavItem }) {
  const { application, environment } = useSelection();
  const [started, setStarted] = useState<{ id: string; scope: string }>();
  const scope = `${application?.name ?? ""}/${environment?.name ?? ""}`;

  return (
    <>
      <div className="page-header">
        <h1>{item.label}</h1>
        <p>{item.subtitle}</p>
      </div>
      {application === undefined ? (
        <EmptyState title="Choose an application" message="Validate runs against one application at a time." />
      ) : (
        <>
          <ValidateForm onStarted={(id) => setStarted({ id, scope })} />
          {/* Switching application or environment clears the result. */}
          {started?.scope === scope ? (
            <>
              <RunResult runId={started.id} />
              {/* A new run starts with nothing compared. */}
              <HealthyComparisonSection key={started.id} runId={started.id} production={environment?.production === true} />
              <SuggestedNextSteps runId={started.id} />
            </>
          ) : (
            <EmptyState title="Nothing validated yet" message="Pick a probe and paste any ID of the subject to check a flow." />
          )}
        </>
      )}
    </>
  );
}
