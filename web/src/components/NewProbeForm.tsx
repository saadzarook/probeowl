// Author: @SaadZarook
import { useState } from "react";
import type { Entity } from "../api/client";
import { probeIdProblem, type ProbeKindChoice } from "../draftYaml";

/** What a new draft starts from: its id and title, entity and kind. */
export interface NewProbe {
  probe: string;
  title: string;
  entity: string;
  kind: ProbeKindChoice;
}

interface NewProbeFormProps {
  entities: Entity[];
  /** The ids of the application's probes: a new one may not reuse them. */
  taken: string[];
  initial: { entity: string; kind: ProbeKindChoice };
  onStart: (probe: NewProbe) => void;
  onCancel: () => void;
}

/** Name a new probe before its draft starts: its id names the file and every run, its title is what people read. */
export function NewProbeForm({ entities, taken, initial, onStart, onCancel }: NewProbeFormProps) {
  const [probe, setProbe] = useState("");
  const [title, setTitle] = useState("");
  const [entity, setEntity] = useState(initial.entity);
  const [kind, setKind] = useState<ProbeKindChoice>(initial.kind);
  const problem = probe === "" ? undefined : probeIdProblem(probe, taken);
  const ready = probe !== "" && problem === undefined && title.trim() !== "";

  return (
    <form
      className="card wizard-form"
      aria-labelledby="new-probe"
      onSubmit={(e) => {
        e.preventDefault();
        if (ready) onStart({ probe, title: title.trim(), entity, kind });
      }}
    >
      <h2 id="new-probe">New probe</h2>
      <p className="hint">Name it for what it checks, such as visitor_cards or topup_completed: the id names its file and every run of it.</p>
      <label>
        Probe id
        <input value={probe} placeholder="such as visitor_cards" aria-invalid={problem !== undefined} aria-describedby="probe-id-problem" onChange={(e) => setProbe(e.target.value.trim())} />
      </label>
      <p id="probe-id-problem" className={problem ? "form-error" : "sr-only"} role={problem ? "alert" : undefined}>
        {problem ?? ""}
      </p>
      <label>
        Title
        <input value={title} placeholder="such as A visitor's cards" onChange={(e) => setTitle(e.target.value)} />
      </label>
      <label>
        Entity
        <select value={entity} onChange={(e) => setEntity(e.target.value)}>
          {entities.map((e) => (
            <option key={e.entity} value={e.entity}>
              {e.entity}
            </option>
          ))}
        </select>
      </label>
      <label>
        Kind
        <select value={kind} onChange={(e) => setKind(e.target.value === "validate" ? "validate" : "inspect")}>
          <option value="inspect">Inspect · current state</option>
          <option value="validate">Validate · one flow</option>
        </select>
      </label>
      <div className="wizard-nav">
        <button className="btn btn-outline" type="button" onClick={onCancel}>
          Cancel
        </button>
        <button className="btn" type="submit" disabled={!ready}>
          Start the draft
        </button>
      </div>
    </form>
  );
}
