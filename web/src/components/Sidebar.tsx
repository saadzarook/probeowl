// Author: @SaadZarook
import { NavLink } from "react-router-dom";
import { usePlugins } from "../api/usePlugins";
import { pluginScreens } from "../plugins";
import { navItems } from "../routes";
import { cn } from "../ui";
import { VERSION_LINE } from "../version";
import { OwlMark } from "./OwlMark";

const LINK = "rounded-md px-3 py-2 text-sm text-sidebar-foreground no-underline hover:bg-sidebar-active";
const linkClass = ({ isActive }: { isActive: boolean }) => cn(LINK, isActive && "bg-sidebar-active font-medium");

/** The ProbeOwl mark and wordmark, a link to Inspect. */
export function Brand({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <NavLink to="/inspect" onClick={onNavigate} className="flex items-center gap-2 px-2 text-xl font-normal text-sidebar-muted no-underline">
      <OwlMark className="size-8" />
      <span>
        Probe<strong className="font-semibold text-sidebar-foreground">Owl</strong>
      </span>
    </NavLink>
  );
}

/** The screens, then the plugins' tools: in the sidebar, and in the menu below 1024 px. */
export function Navigation({ onNavigate }: { onNavigate?: () => void }) {
  const plugins = usePlugins();
  const on = new Set((plugins.data ?? []).map((p) => p.name));
  const tools = pluginScreens.filter((screen) => on.has(screen.plugin));
  return (
    <>
      <nav aria-label="Main" className="flex flex-col gap-1">
        {navItems.map((item) => (
          <NavLink key={item.path} to={item.path} className={linkClass} onClick={onNavigate}>
            {item.label}
          </NavLink>
        ))}
      </nav>
      {tools.length > 0 && (
        <nav aria-label="Tools" className="flex flex-col gap-1">
          <p className="m-0 px-3 text-xs font-semibold text-sidebar-muted">Tools</p>
          {tools.map((tool) => (
            <NavLink key={tool.path} to={tool.path} className={linkClass} onClick={onNavigate}>
              {tool.label}
            </NavLink>
          ))}
        </nav>
      )}
      <p className="m-0 mt-auto px-3 pt-6 text-sm text-sidebar-muted">{VERSION_LINE}</p>
    </>
  );
}

/** The sidebar, from 1024 px; below that the top bar's Menu opens the same navigation (rule 5). */
export function Sidebar() {
  return (
    <aside className="hidden w-56 shrink-0 flex-col gap-6 bg-sidebar px-4 py-6 text-sidebar-foreground lg:flex">
      <Brand />
      <Navigation />
    </aside>
  );
}
