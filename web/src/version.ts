// Author: @SaadZarook
// Set at build time from backend/pyproject.toml: the version owl --version prints.
declare const __PROBEOWL_VERSION__: string;

/** ProbeOwl runs on the person's own machine; the only mode in this release. */
export const MODE = "local";

/** "v0.2.0 · local": the version and mode, as the sidebar shows them. */
export const VERSION_LINE = `v${__PROBEOWL_VERSION__} · ${MODE}`;
