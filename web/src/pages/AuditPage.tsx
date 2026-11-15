// Author: @SaadZarook
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

function EventRow({ event }: { event: AuditEvent }) {
  const where = [event.application, event.environment].filter(Boolean).join(" / ");
  return (
    <tr>
      <td className="nowrap">{event.occurred_at ? formatTime(event.occurred_at) : "—"}</td>
      <td>{event.actor.name}</td>
      <td>
        <code>{event.action}</code>
      </td>
      <td>{where || "—"}</td>
      <td>{event.run_id ? <Link to={`/runs?run=${encodeURIComponent(event.run_id)}`}>Open run</Link> : "—"}</td>
      <td className="audit-details">
        <code>{detailsText(event.details)}</code>
      </td>
    </tr>
  );
}

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
      <div className="form-row">
        <label>
          Actor
          <input value={actor} onChange={(e) => setActor(e.target.value)} placeholder="Who acted, or for whom" />
        </label>
        <label>
          Action
          <input value={action} onChange={(e) => setAction(e.target.value)} placeholder="Such as run_started" />
        </label>
        <label>
          Application
          <select
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
          </select>
        </label>
        <label>
          Environment
          <select value={environment} onChange={(e) => setEnvironment(e.target.value)} disabled={application === ""}>
            <option value="">All environments</option>
            {environments.map((e) => (
              <option key={e.name} value={e.name}>
                {e.title}
              </option>
            ))}
          </select>
        </label>
      </div>
      <button className="btn" type="submit">
        Show
      </button>
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
        <button className="btn" type="button" onClick={() => void audit.refetch()}>
          Retry
        </button>
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
      <div className="table-scroll">
        <table className="run-list audit-list">
          <thead>
            <tr>
              <th scope="col">When</th>
              <th scope="col">Actor</th>
              <th scope="col">Action</th>
              <th scope="col">Where</th>
              <th scope="col">Run</th>
              <th scope="col">Details</th>
            </tr>
          </thead>
          <tbody>
            {events.map((event, index) => (
              // Every stored event has an id; the contract lists it as defaulted, hence the fallback.
              <EventRow key={event.id ?? index} event={event} />
            ))}
          </tbody>
        </table>
      </div>
      {audit.hasNextPage && (
        <button className="btn btn-outline" type="button" disabled={audit.isFetchingNextPage} onClick={() => void audit.fetchNextPage()}>
          {audit.isFetchingNextPage ? "Loading…" : "Show older"}
        </button>
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
