// Author: @SaadZarook

/** The header every API response carries: how many times owl serve reloaded its configuration. */
export const CONFIGURATION_HEADER = "X-ProbeOwl-Configuration";

let seen: string | undefined;
const listeners = new Set<() => void>();

/** Note the configuration a response was answered with; a new one tells every listener, once. */
export function noteConfiguration(value: string | null): void {
  if (value === null) return;
  const changed = seen !== undefined && value !== seen;
  seen = value;
  if (changed) for (const listener of listeners) listener();
}

/** Be told when owl serve answers with a configuration it loaded since: by owl reload, or Reload configuration. */
export function onConfigurationReloaded(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
