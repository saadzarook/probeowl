// Author: @SaadZarook
import { useQuery } from "@tanstack/react-query";
import { fetchPlugins } from "./client";

/** The plugins that are on. One that is off is not listed, and has no routes. */
export function usePlugins() {
  return useQuery({ queryKey: ["plugins"], queryFn: fetchPlugins, retry: false });
}
