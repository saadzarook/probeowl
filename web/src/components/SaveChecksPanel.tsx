// Author: @SaadZarook
import type { SaveCheckReport } from "../api/client";

interface SaveChecksPanelProps {
  report: SaveCheckReport | undefined;
  loading: boolean;
  error: string | undefined;
  onCheck: () => void;
}

const STATUS_LABELS = { pass: "Pass", warn: "Warn", fail: "Fail" } as const;

/** The save checks of the draft: a summary, then one row per check. */
export function SaveChecksPanel({ report, loading, error, onCheck }: SaveChecksPanelProps) {
  return (
    <section className="card save-checks" aria-label="Save checks" aria-busy={loading}>
      <header className="checks-header">
        <h2>Save checks</h2>
        {report && (
          <span className="hint">
            {report.passed} passed · {report.warnings} {report.warnings === 1 ? "warning" : "warnings"} · {report.failed} failed
          </span>
        )}
        <button className="btn secondary" type="button" onClick={onCheck} disabled={loading}>
          {loading ? "Checking…" : "Check"}
        </button>
      </header>
      {error && (
        <div className="error-state" role="alert">
          <p>{error}</p>
          <button className="btn" type="button" onClick={onCheck}>
            Retry
          </button>
        </div>
      )}
      {report && !report.can_save && <p className="form-error">Fix the failed checks to save</p>}
      {!report && !error && <p className="hint">Checking the draft…</p>}
      {report && (
        <ul className="checks">
          {report.checks.map((c, i) => (
            <li key={`${c.check}-${c.location ?? ""}-${c.environment ?? ""}-${i}`}>
              <span className="check-chip" data-status={c.status}>
                {STATUS_LABELS[c.status]}
              </span>
              <div>
                <strong>{c.check}</strong>
                {c.location && <span className="hint"> · {c.location}</span>}
                {c.environment && <span className="hint"> · {c.environment}</span>}
                <p className="hint">{c.message}</p>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
