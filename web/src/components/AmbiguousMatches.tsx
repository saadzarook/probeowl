// Author: @SaadZarook
import type { Match } from "../api/client";
import { OutcomeChip } from "./OutcomeChip";

/** The most matches that can run together. */
export const MAX_TOGETHER = 5;

export function showValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  return typeof value === "string" ? value : JSON.stringify(value);
}

interface AmbiguousMatchesProps {
  identifier: string;
  /** As returned by the API: masked when the identifier is PII. */
  value: string;
  matches: Match[];
  onInspect: (subjects: string[]) => void;
}

/** An identifier that matches several subjects: only the matches, until the person chooses. */
export function AmbiguousMatches({ identifier, value, matches, onInspect }: AmbiguousMatchesProps) {
  const rows = matches.map((match) => ({
    id: match.subject,
    detail: Object.values(match.fields ?? {}).map(showValue).join(" · "),
  }));
  const together = rows.slice(0, MAX_TOGETHER);

  return (
    <section className="card ambiguous" aria-label="Ambiguous identifier">
      <header>
        <OutcomeChip outcome="ambiguous" />
        <h2>When an identifier matches more than one subject</h2>
        <p className="hint">
          {identifier} <code>{value}</code> matched {rows.length} subjects. Nothing runs until you choose.
        </p>
      </header>
      <ul className="matches">
        {rows.map((match) => (
          <li key={match.id}>
            <div>
              <code>{match.id}</code>
              <p className="hint">{match.detail}</p>
            </div>
            <button className="btn-outline" type="button" onClick={() => onInspect([match.id])}>
              Inspect this one<span className="sr-only"> {match.id}</span>
            </button>
          </li>
        ))}
      </ul>
      <footer>
        <p className="hint">
          Up to {MAX_TOGETHER} matches can run together. A duplicate profile is often the finding itself.
          {rows.length > MAX_TOGETHER && ` Only the first ${MAX_TOGETHER} will run.`}
        </p>
        <button className="btn-dark" type="button" disabled={together.length < 2} onClick={() => onInspect(together.map((m) => m.id))}>
          {rows.length === 2 ? "Inspect both" : `Inspect ${together.length === rows.length ? "all" : "first"} ${together.length}`}
        </button>
      </footer>
    </section>
  );
}
