// Author: @SaadZarook
import { Outlet, useLocation } from "react-router-dom";
import { isToolPath } from "../plugins";
import { useSelection } from "../selection";
import { EmptyState, GuideLink } from "./EmptyState";
import { ErrorBoundary } from "./ErrorBoundary";
import { KillSwitchBanner } from "./KillSwitchBanner";
import { ProductionBanner } from "./ProductionBanner";
import { ReloadedBanner } from "./ReloadedBanner";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";

/** The shared frame around every screen. */
export function Shell() {
  const { applications, application, environment, loading, error, retry } = useSelection();
  const location = useLocation();
  // A tool such as Compare files needs no application, so it still opens.
  const noApplications = applications.length === 0 && !isToolPath(location.pathname);

  return (
    <div className="shell">
      <Sidebar />
      <div className="main">
        <TopBar />
        {application && environment?.production && !isToolPath(location.pathname) && <ProductionBanner applicationName={application.title} />}
        <KillSwitchBanner />
        <ReloadedBanner />
        <main>
          {error ? (
            <div className="error-state" role="alert">
              <p>{error}</p>
              <button className="btn" type="button" onClick={retry}>
                Retry
              </button>
            </div>
          ) : loading ? (
            <p aria-busy="true">Loading applications…</p>
          ) : noApplications ? (
            <EmptyState
              level={1}
              title="No applications yet"
              message={
                <>
                  owl serve found no applications in its config folder. Run <code>owl init</code> to create one, or restart{" "}
                  <code>owl serve --config &lt;folder&gt;</code> on an existing folder.
                </>
              }
            >
              <GuideLink page="getting-started/">Getting started</GuideLink>
              <GuideLink page="getting-started/#try-the-examples">Try the examples</GuideLink>
            </EmptyState>
          ) : (
            <ErrorBoundary key={location.pathname}>
              <Outlet />
            </ErrorBoundary>
          )}
        </main>
      </div>
    </div>
  );
}
