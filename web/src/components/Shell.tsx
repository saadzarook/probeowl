// Author: @SaadZarook
import { useEffect, useRef, useState } from "react";
import { Navigate, Outlet, useLocation } from "react-router-dom";
import { isToolPath } from "../plugins";
import { useSelection } from "../selection";
import { ErrorBoundary } from "./ErrorBoundary";
import { KillSwitchBanner } from "./KillSwitchBanner";
import { ProductionBanner } from "./ProductionBanner";
import { ReloadedBanner } from "./ReloadedBanner";
import { SearchPalette } from "./SearchPalette";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";

/** The shared frame around every screen. */
export function Shell() {
  const { applications, application, environment, loading, error, retry } = useSelection();
  const location = useLocation();
  // A tool such as Compare files needs no application, so it still opens.
  const noApplications = applications.length === 0 && !isToolPath(location.pathname) && location.pathname !== "/setup";
  const [searching, setSearching] = useState(false);
  // Where focus was when search opened, to go back to when it closes without opening anything.
  const opener = useRef<HTMLElement | null>(null);
  const openSearch = () => {
    opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setSearching(true);
  };
  // What opening a result changed, such as the environment shown: said, never silent.
  const [notice, setNotice] = useState<string>();

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        openSearch();
      } else if (event.key === "/" && !event.ctrlKey && !event.metaKey && !event.altKey && !typingIn(event.target)) {
        event.preventDefault();
        openSearch();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div className="shell">
      <Sidebar />
      <div className="main">
        <TopBar onSearch={openSearch} />
        <SearchPalette open={searching} onOpenChange={setSearching} onNotice={setNotice} returnFocus={() => opener.current?.focus()} />
        {notice && (
          <div className="switch-notice" role="status">
            <p>{notice}</p>
            <button className="btn-outline" type="button" onClick={() => setNotice(undefined)}>
              Dismiss
            </button>
          </div>
        )}
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
            // First run: no applications yet, so set one up in the browser.
            <Navigate to="/setup" replace />
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

/** / opens search only outside a text field, as GitHub's does: in one it is a character. */
function typingIn(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable) return true;
  const tag = target.tagName;
  if (tag === "TEXTAREA" || tag === "SELECT") return true;
  if (tag !== "INPUT") return false;
  const type = (target as HTMLInputElement).type;
  return !["checkbox", "radio", "button", "submit", "reset", "range", "color", "file"].includes(type);
}
