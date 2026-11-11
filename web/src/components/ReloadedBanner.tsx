// Author: @SaadZarook
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { onConfigurationReloaded } from "../api/configuration";

/**
 * When owl serve answers with a configuration it loaded since this page did (owl reload in a terminal,
 * or Reload configuration in another tab), every screen reads again and this says so. Drafts are kept:
 * the Probe editor checks them again against the new entities.
 */
export function ReloadedBanner() {
  const client = useQueryClient();
  const [reloaded, setReloaded] = useState(false);

  useEffect(
    () =>
      onConfigurationReloaded(() => {
        void client.invalidateQueries();
        setReloaded(true);
      }),
    [client],
  );

  if (!reloaded) return null;
  return (
    <div className="reload-banner" role="status">
      <span>owl serve reloaded its configuration. This screen now shows the new one; drafts are kept and checked again.</span>
      <button className="btn btn-outline" type="button" onClick={() => setReloaded(false)}>
        Dismiss
      </button>
    </div>
  );
}
