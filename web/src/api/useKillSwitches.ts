// Author: @SaadZarook
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { engageKillSwitch, fetchKillSwitches, releaseKillSwitch, type SetKillSwitch } from "./client";

/** Often enough that a switch someone else turned on in a shared store is seen soon. */
const REFRESH_MS = 15_000;

/** The kill switches that are on. */
export function useKillSwitches() {
  return useQuery({
    queryKey: ["kill-switches"],
    queryFn: fetchKillSwitches,
    refetchInterval: REFRESH_MS,
    retry: false,
  });
}

/** Turn a kill switch on or off; what the connectors show changes with it. */
export function useSetKillSwitch(on: boolean) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: SetKillSwitch) => (on ? engageKillSwitch(body) : releaseKillSwitch(body)),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["kill-switches"] });
      void client.invalidateQueries({ queryKey: ["connectors"] });
    },
  });
}
