// Author: @SaadZarook
import { useQuery } from "@tanstack/react-query";
import { fetchApplications } from "./client";

/** The applications and their environments, loaded through the generated client. */
export function useApplications() {
  return useQuery({ queryKey: ["applications"], queryFn: fetchApplications });
}
