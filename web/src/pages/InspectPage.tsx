// Author: @SaadZarook
import { useMutation } from "@tanstack/react-query";
import { Fragment, useState } from "react";
import { pickMatches, splitError, startRun } from "../api/client";
import { useEntities } from "../api/useEntities";
import { EmptyState } from "../components/EmptyState";
import { HealthyComparisonSection } from "../components/HealthyComparison";
import { InspectForm } from "../components/InspectForm";
import { InspectResult } from "../components/InspectResult";
import { SuggestedNextSteps } from "../components/SuggestedNextSteps";
import type { NavItem } from "../routes";
import { useSelection } from "../selection";

/** Inspect a subject: start an inspect probe from any identifier and read each run as it fills in. */
export function InspectPage({ item }: { item: NavItem }) {
  const { application, environment } = useSelection();
  const entities = useEntities(application?.name);
  const entity = entities.data?.[0];
  const [started, setStarted] = useState<{ ids: string[]; scope: string }>();
  const scope = `${application?.name ?? ""}/${environment?.name ?? ""}`;

  const onStarted = (ids: string[]) => setStarted({ ids, scope });
  const start = useMutation({
    mutationFn: startRun,
    onSuccess: (id) => onStarted([id]),
  });
  const pick = useMutation({
    mutationFn: ({ runId, subjects }: { runId: string; subjects: string[] }) =>
      pickMatches(runId, subjects),
    onSuccess: onStarted,
  });

  return (
    <>
      <div className="page-header">
        <h1>Inspect a subject</h1>
        <p>{item.subtitle}</p>
      </div>
      {application === undefined ? (
        <EmptyState
          title="Choose an application"
          message="Inspect runs against one application at a time."
        />
      ) : entities.isPending ? (
        <p aria-busy="true">Loading entities…</p>
      ) : entities.isError ? (
        <div className="error-state" role="alert">
          <p>{entities.error.message}</p>
          <button
            className="btn"
            type="button"
            onClick={() => void entities.refetch()}
          >
            Retry
          </button>
        </div>
      ) : entity === undefined ? (
        <EmptyState
          title="Nothing to inspect"
          message="This application has no entities yet."
        />
      ) : (
        <>
          <InspectForm
            entity={entity}
            pending={start.isPending || pick.isPending}
            error={splitError(start.error ?? pick.error, "value").rest}
            valueError={splitError(start.error ?? pick.error, "value").field}
            onSubmit={(request) => start.mutate(request)}
          />
          {/* Switching application or environment clears the results. */}
          {started?.scope === scope ? (
            started.ids.map((id) => (
              <Fragment key={id}>
                <InspectResult
                  runId={id}
                  entity={entity}
                  onInspect={(runId, subjects) =>
                    pick.mutate({ runId, subjects })
                  }
                />
                <HealthyComparisonSection runId={id} production={environment?.production === true} />
                <SuggestedNextSteps runId={id} />
              </Fragment>
            ))
          ) : (
            <EmptyState
              title="Nothing inspected yet"
              message="Paste any identifier of a subject to see its current state."
            />
          )}
        </>
      )}
    </>
  );
}
