// Author: @SaadZarook
import { Navigate, Route, Routes } from "react-router-dom";
import { usePlugins } from "./api/usePlugins";
import { Shell } from "./components/Shell";
import { AddConnectionPage } from "./pages/AddConnectionPage";
import { ConnectorsPage } from "./pages/ConnectorsPage";
import { FirstRunPage } from "./pages/FirstRunPage";
import { InspectPage } from "./pages/InspectPage";
import { KnowledgeBasePage } from "./pages/KnowledgeBasePage";
import { NewEntityPage } from "./pages/NewEntityPage";
import { PlaceholderPage } from "./pages/PlaceholderPage";
import { ProbeEditorPage } from "./pages/ProbeEditorPage";
import { RunsPage } from "./pages/RunsPage";
import { TablesPage } from "./pages/TablesPage";
import { ValidatePage } from "./pages/ValidatePage";
import { pluginScreens, type PluginScreen } from "./plugins";
import { navItems, type NavItem } from "./routes";
import { SelectionProvider } from "./selection";

/** A plugin's screen, which does not exist while the plugin is off. */
function PluginPage({ screen }: { screen: PluginScreen }) {
  const plugins = usePlugins();
  if (plugins.isPending) return <p aria-busy="true">Loading…</p>;
  if (plugins.isError) {
    return (
      <div className="error-state" role="alert">
        <p>{plugins.error.message}</p>
        <button className="btn" type="button" onClick={() => void plugins.refetch()}>
          Retry
        </button>
      </div>
    );
  }
  if (!plugins.data.some((p) => p.name === screen.plugin)) return <Navigate to="/inspect" replace />;
  return <>{screen.element}</>;
}

function pageFor(item: NavItem) {
  if (item.path === "/inspect") return <InspectPage item={item} />;
  if (item.path === "/validate") return <ValidatePage item={item} />;
  if (item.path === "/probes") return <ProbeEditorPage item={item} />;
  if (item.path === "/tables") return <TablesPage item={item} />;
  if (item.path === "/knowledge-base") return <KnowledgeBasePage item={item} />;
  if (item.path === "/connectors") return <ConnectorsPage item={item} />;
  if (item.path === "/runs") return <RunsPage item={item} />;
  return <PlaceholderPage item={item} />;
}

/** Routes: Inspect, Validate, Probes, Tables and Connectors have their screens; the other entries are placeholder pages inside the shell. */
export function App() {
  return (
    <SelectionProvider>
      <Routes>
        <Route element={<Shell />}>
          <Route index element={<Navigate to="/inspect" replace />} />
          {navItems.map((item) => (
            <Route key={item.path} path={item.path} element={pageFor(item)} />
          ))}
          <Route path="/probes/new-entity" element={<NewEntityPage />} />
          <Route path="/setup" element={<FirstRunPage />} />
          <Route path="/connectors/new" element={<AddConnectionPage />} />
          {pluginScreens.map((screen) => (
            <Route key={screen.path} path={screen.path} element={<PluginPage screen={screen} />} />
          ))}
          <Route path="*" element={<Navigate to="/inspect" replace />} />
        </Route>
      </Routes>
    </SelectionProvider>
  );
}
