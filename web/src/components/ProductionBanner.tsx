// Author: @SaadZarook

/** What Production means for every read, shown beside the application's name. */
const PRODUCTION_NOTICE = "read replicas only · full guards · Reason and Reference required";

/** Red banner shown on every screen while the environment is Production. */
export function ProductionBanner({ applicationName }: { applicationName: string }) {
  return (
    <div className="prod-banner" role="status">
      <span className="prod-pill">Production</span>
      <span>
        {applicationName} · {PRODUCTION_NOTICE}
      </span>
    </div>
  );
}
