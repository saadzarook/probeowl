// Author: @SaadZarook
import { Button, Field, FormGrid, Input, ListOrTable, Select, type Column } from "../ui";
import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import type { AuditEvent, AuditFilters } from "../api/client";
import { useAudit } from "../api/useAudit";
import { EmptyState } from "../components/EmptyState";
import type { NavItem } from "../routes";
import { formatTime } from "../runFormat";
import { useSelection } from "../selection";

/** "reason=incident probe=topup_completed": an event's details, compact, nested values as JSON. */
function detailsText(details: AuditEvent["details"]): string {
  return Object.entries(details ?? {})
    .filter(([, value]) => value !== null && value !== undefined)
    .map(([key, value]) => `${key}=${typeof value === "string" ? value : JSON.stringify(value)}`)
    .join(" ");
}

/** The event's columns: a table from 768 px, cards below (rule 6). */
const EVENT_COLUMNS: Column<AuditEvent>[] = [
  { header: "When", cell: (event) => (event.occurred_at ? formatTime(event.occurred_at) : "—") },
  { header: "Actor", cell: (event) => event.actor.name },
  { header: "Action", cell: (event) => <code className="font-mono">{event.action}</code> },
  { header: "Where", cell: (event) => [event.application, event.environment].filter(Boolean).join(" / ") || "—" },
  { header: "Run", cell: (event) => (event.run_id ? <Link to={`/runs?run=${encodeURIComponent(event.run_id)}`}>Open run</Link> : "—") },
  { header: "Details", cell: (event) => <code className="font-mono text-xs wrap-anywhere">{detailsText(event.details)}</code> },
];

/** The filters, applied when the form is sent: each one given narrows the list. */
function Filters({ onApply }: { onApply: (filters: AuditFilters) => void }) {
  const { applications } = useSelection();
  const [actor, setActor] = useState("");
  const [action, setAction] = useState("");
  const [application, setApplication] = useState("");
  const [environment, setEnvironment] = useState("");
  const environments = applications.find((a) => a.name === application)?.environments ?? [];
  const apply = (event: FormEvent) => {
    event.preventDefault();
    const given = { actor: actor.trim(), action: action.trim(), application, environment };
    onApply(Object.fromEntries(Object.entries(given).filter(([, v]) => v !== "")));
  };
  return (
    <form className="card audit-filters" onSubmit={apply} aria-label="Filter the audit">
      <FormGrid>
        <Field id="audit-actor" label="Actor">
          <Input value={actor} onChange={(e) => setActor(e.target.value)} placeholder="Who acted, or for whom" />
        </Field>
        <Field id="audit-action" label="Action">
          <Input value={action} onChange={(e) => setAction(e.target.value)} placeholder="Such as run_started" />
        </Field>
        <Field id="audit-application" label="Application">
          <Select
            value={application}
            onChange={(e) => {
              setApplication(e.target.value);
              setEnvironment("");
            }}
          >
            <option value="">All applications</option>
            {applications.map((a) => (
              <option key={a.name} value={a.name}>
                {a.title}
              </option>
            ))}
          </Select>
        </Field>
        <Field id="audit-environment" label="Environment">
          <Select value={environment} onChange={(e) => setEnvironment(e.target.value)} disabled={application === ""}>
            <option value="">All environments</option>
            {environments.map((e) => (
              <option key={e.name} value={e.name}>
                {e.title}
              </option>
            ))}
          </Select>
        </Field>
      </FormGrid>
      <Button type="submit">
        Show
      </Button>
    </form>
  );
}

function EventList({ filters }: { filters: AuditFilters }) {
  const audit = useAudit(filters);
  if (audit.isPending) return <p aria-busy="true">Loading the audit…</p>;
  if (audit.isError) {
    return (
      <div className="error-state" role="alert">
        <p>{audit.error.message}</p>
        <Button type="button" onClick={() => void audit.refetch()}>
          Retry
        </Button>
      </div>
    );
  }
  const events = audit.data.pages.flatMap((page) => page.events);
  if (events.length === 0) {
    const filtered = Object.keys(filters).length > 0;
    return (
      <EmptyState
        title={filtered ? "No events match these filters" : "Nothing in the audit yet"}
        message={filtered ? "Each filter narrows the list by exact name. Clear one to see more." : "Every run, setup change and kill switch is recorded here as it happens."}
      />
    );
  }
  return (
    <section className="card" aria-label="Audit events">
      <ListOrTable label="Audit events" rows={events} columns={EVENT_COLUMNS} rowKey={(event) => event.id ?? `${event.occurred_at}-${event.action}`} empty="No events." />
      {audit.hasNextPage && (
        <Button variant="outline" type="button" disabled={audit.isFetchingNextPage} onClick={() => void audit.fetchNextPage()}>
          {audit.isFetchingNextPage ? "Loading…" : "Show older"}
        </Button>
      )}
    </section>
  );
}

/** The audit, read-only: who did what, where and when, newest first, with masked details. */
export function AuditPage({ item }: { item: NavItem }) {
  const [filters, setFilters] = useState<AuditFilters>({});
  return (
    <>
      <div className="page-header">
        <h1>{item.label}</h1>
        <p>{item.subtitle}</p>
      </div>
      <Filters onApply={setFilters} />
      <EventList filters={filters} />
    </>
  );
}
