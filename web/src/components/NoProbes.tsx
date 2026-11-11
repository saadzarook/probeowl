// Author: @SaadZarook
import { Link } from "react-router-dom";
import { EmptyState, GuideLink } from "./EmptyState";

const WHAT = {
  validate: "A Validate probe checks that a flow ran correctly.",
  inspect: "An Inspect probe shows a subject's current state.",
};

/** An application with no probe of this kind for the entity: what one is, and where to write it. */
export function NoProbes({ kind, application, entity }: { kind: "validate" | "inspect"; application: string; entity: string | undefined }) {
  const title = kind === "validate" ? "Validate" : "Inspect";
  const start = `/probes?new=${kind}${entity ? `&entity=${encodeURIComponent(entity)}` : ""}`;
  return (
    <div role="status">
      <EmptyState title={`No ${title} probes in ${application}`} message={`${WHAT[kind]} Start one in the Probe editor.`}>
        <Link className="btn" to={start}>
          New probe
        </Link>
        <GuideLink page="writing-probes/">Writing probes</GuideLink>
      </EmptyState>
    </div>
  );
}
