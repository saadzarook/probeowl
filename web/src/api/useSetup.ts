// Author: @SaadZarook
import { useQuery } from "@tanstack/react-query";
import { fetchSetup } from "./client";

/** First run's facts: applications loaded, the config folder, the store and the keychain. */
export function useSetup() {
  return useQuery({ queryKey: ["setup"], queryFn: fetchSetup });
}
