// Author: @SaadZarook
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { reloadConfiguration } from "./client";

/** Reload owl serve's configuration; every screen then reads the new one. */
export function useReloadConfiguration() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: reloadConfiguration,
    // Applications, connectors, probes and the catalog may all have changed. A first read still
    // running started before the reload, and invalidating would join it rather than read again:
    // cancel it first.
    onSuccess: async () => {
      await client.cancelQueries();
      await client.invalidateQueries();
    },
  });
}
