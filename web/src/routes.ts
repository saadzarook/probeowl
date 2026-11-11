// Author: @SaadZarook
export interface NavItem {
  path: string;
  label: string;
  subtitle: string;
}

export const navItems: NavItem[] = [
  { path: "/inspect", label: "Inspect", subtitle: "Current state, before you debug a flow" },
  { path: "/validate", label: "Validate", subtitle: "Did a flow execute correctly" },
  { path: "/health", label: "Health", subtitle: "Scheduled checks across the application" },
  { path: "/probes", label: "Probes", subtitle: "Probes and their expectations" },
  { path: "/knowledge-base", label: "Knowledge base", subtitle: "Runbooks of known problems" },
  { path: "/tables", label: "Tables", subtitle: "The table catalog" },
  { path: "/connectors", label: "Connectors", subtitle: "Read-only clients and guards" },
  { path: "/runs", label: "Runs", subtitle: "Every investigation and its record" },
];
