// Author: @SaadZarook
import { CompareFilesPage } from "@plugins/compare_files/web";
import type { ReactNode } from "react";

/** A plugin's screen under Tools. Built into the web app, and shown only while the plugin is on. */
export interface PluginScreen {
  /** The name in its plugin.yaml. */
  plugin: string;
  path: string;
  label: string;
  element: ReactNode;
}

/** Every first-party plugin's screen. */
export const pluginScreens: PluginScreen[] = [
  { plugin: "compare_files", path: "/tools/compare-files", label: "Compare files", element: <CompareFilesPage /> },
];

/** Whether this path is a tool's: it uses no application or environment. */
export function isToolPath(path: string): boolean {
  return pluginScreens.some((screen) => screen.path === path);
}
