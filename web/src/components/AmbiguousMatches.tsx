// Author: @SaadZarook
import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { pickMatchesByNumber, type Match } from "../api/client";
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
  /** The run this is, for picking masked matches by number. */
  runId: string;
  /** The runs picked by number started: their ids. */
  onStarted: (ids: string[]) => void;
}

/** An identifier that matches several subjects: only the matches, until the person chooses. */
export function AmbiguousMatches(props: AmbiguousMatchesProps) {
  // The entity's key is PII, so its matches are stored masked: they are picked by number (#66).
  if (props.matches.length > 0 && props.matches.every((m) => m.subject === "[masked]")) return <ByNumber {...props} />;
  return <BySubject {...props} />;
}

function BySubject({ identifier, value, matches, onInspect }: AmbiguousMatchesProps) {
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

/** Masked matches, numbered: the value searched for is pasted again and the server finds them. */
function ByNumber({ identifier, value, matches, runId, onStarted }: AmbiguousMatchesProps) {
  // The pasted value is PII: in this form's state only, never a mutation's variables, and the
  // mutation leaves the cache with the form.
  const [pasted, setPasted] = useState("");
  const pick = useMutation({
    gcTime: 0,
    mutationFn: (numbers: number[]) => pickMatchesByNumber(runId, pasted, numbers),
    onSuccess: onStarted,
  });
  const together = matches.slice(0, MAX_TOGETHER).map((_, i) => i + 1);
  return (
    <section className="card ambiguous" aria-label="Ambiguous identifier">
      <header>
        <OutcomeChip outcome="ambiguous" />
        <h2>When an identifier matches more than one subject</h2>
        <p className="hint">
          {identifier} <code>{value}</code> matched {matches.length} subjects. Their key is PII, so they are shown by number with
          what isn't PII, and nothing runs until you choose.
        </p>
      </header>
      <label className="runbook-pasted">
        Paste the {identifier} you searched for again
        <input value={pasted} autoComplete="off" spellCheck={false} onChange={(e) => setPasted(e.target.value.trim())} />
      </label>
      <ul className="matches">
        {matches.map((match, i) => (
          <li key={i}>
            <div>
              <strong>Match {i + 1}</strong>
              <p className="hint">{Object.values(match.fields ?? {}).map(showValue).join(" · ")}</p>
            </div>
            <button className="btn-outline" type="button" disabled={pasted === "" || pick.isPending} onClick={() => pick.mutate([i + 1])}>
              Inspect this one<span className="sr-only"> match {i + 1}</span>
            </button>
          </li>
        ))}
      </ul>
      {pick.isError && (
        <p className="error-state" role="alert">
          {pick.error.message}
        </p>
      )}
      <footer>
        <p className="hint">Up to {MAX_TOGETHER} matches can run together.</p>
        <button className="btn-dark" type="button" disabled={pasted === "" || together.length < 2 || pick.isPending} onClick={() => pick.mutate(together)}>
          {matches.length === 2 ? "Inspect both" : `Inspect ${together.length === matches.length ? "all" : "first"} ${together.length}`}
        </button>
      </footer>
    </section>
  );
}
