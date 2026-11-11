// Author: @SaadZarook
import type { LocationResult } from "../api/client";

function show(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value);
}

/** The exact query, parameters, guards and result behind one location's outcome. */
export function EvidencePanel({ title, location }: { title: string; location: Pick<LocationResult, "evidence" | "reason"> }) {
  const evidence = location.evidence;
  return (
    <section className="evidence" aria-label={`Evidence: ${title}`}>
      <h3>Evidence · {title}</h3>
      {!evidence ? (
        <p className="hint">No lookup ran{location.reason ? `: ${location.reason}` : "."}</p>
      ) : (
        <>
          <p className="hint">{evidence.connector}</p>
          <pre className="query">{evidence.query}</pre>
          {evidence.parameters && Object.keys(evidence.parameters).length > 0 && (
            <p className="hint">
              {Object.entries(evidence.parameters)
                .map(([name, v]) => `:${name} = ${show(v)}`)
                .join(" · ")}
            </p>
          )}
          {evidence.guards && evidence.guards.length > 0 && (
            <ul className="guards">
              {evidence.guards.map((g) => (
                <li key={g}>{g}</li>
              ))}
            </ul>
          )}
          <p>
            Result: <strong>{evidence.rows_total ?? evidence.rows?.length ?? 0} rows</strong> in {evidence.duration_ms} ms
          </p>
        </>
      )}
    </section>
  );
}
