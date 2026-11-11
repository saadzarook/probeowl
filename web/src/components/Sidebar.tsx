// Author: @SaadZarook
import { NavLink } from "react-router-dom";
import { usePlugins } from "../api/usePlugins";
import { pluginScreens } from "../plugins";
import { navItems } from "../routes";
import { VERSION_LINE } from "../version";
import { OwlMark } from "./OwlMark";

/** Left sidebar: ProbeOwl mark and wordmark, then navigation. */
export function Sidebar() {
  const plugins = usePlugins();
  const on = new Set((plugins.data ?? []).map((p) => p.name));
  const tools = pluginScreens.filter((screen) => on.has(screen.plugin));
  return (
    <aside className="sidebar">
      <NavLink to="/inspect" className="brand">
        <OwlMark />
        <span>
          Probe<strong>Owl</strong>
        </span>
      </NavLink>
      <nav className="nav" aria-label="Main">
        {navItems.map((item) => (
          <NavLink key={item.path} to={item.path}>
            {item.label}
          </NavLink>
        ))}
      </nav>
      {tools.length > 0 && (
        <nav className="nav" aria-label="Tools">
          <p className="nav-heading">Tools</p>
          {tools.map((tool) => (
            <NavLink key={tool.path} to={tool.path}>
              {tool.label}
            </NavLink>
          ))}
        </nav>
      )}
      <p className="sidebar-footer">{VERSION_LINE}</p>
    </aside>
  );
}
