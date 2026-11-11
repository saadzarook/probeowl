// Author: @SaadZarook
import type { CatalogChange } from "../api/client";

/** The change to catalog.yaml as a unified diff: added lines green, removed lines red. */
export function ChangePreview({ change, stale }: { change: CatalogChange; stale: boolean }) {
  return (
    <div className="change-preview">
      <p>
        Preview of <code>{change.path}</code>
      </p>
      <pre className="diff" tabIndex={0} aria-label={`Diff of ${change.path}`}>
        {change.diff.split("\n").map((line, i) => (
          <span key={i} className="diff-line" data-kind={line.startsWith("+") && !line.startsWith("+++") ? "add" : line.startsWith("-") && !line.startsWith("---") ? "remove" : "context"}>
            {line || " "}
          </span>
        ))}
      </pre>
      {stale && <p role="status" className="hint">Edited after this preview. Preview again before confirming.</p>}
    </div>
  );
}
