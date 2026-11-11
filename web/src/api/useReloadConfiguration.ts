// Author: @SaadZarook
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { reloadConfiguration } from "./client";

/** Reload owl serve's configuration; every screen then reads the new one. */
export function useReloadConfiguration() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: reloadConfiguration,
    // Applications, connectors, probes and the catalog may all have changed.
    onSuccess: () => void client.invalidateQueries(),
  });
}
