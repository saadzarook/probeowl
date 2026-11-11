// Author: @SaadZarook
// owl serve serves the guide at /guide/, on the web app's own origin, so its links work offline.
// The Vite servers pass /guide to owl serve, as they pass /api (vite.config.ts).
const GUIDE = "/guide/";

/** The address of one page of the guide, such as guidePage("connectors/"). */
export function guidePage(path: string): string {
  return GUIDE + path;
}
