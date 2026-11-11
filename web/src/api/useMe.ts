// Author: @SaadZarook
import { useQuery } from "@tanstack/react-query";
import { fetchMe } from "./client";

/** The person running owl serve; it does not change while the page is open. */
export function useMe() {
  return useQuery({ queryKey: ["me"], queryFn: fetchMe, staleTime: Infinity });
}

/** "saadz" -> "SA", "s.khan" -> "SK", "maria-lopez" -> "ML": two letters for the badge. */
export function initials(name: string): string {
  const parts = name.split(/[._\s-]+/).filter(Boolean);
  const letters = parts.length > 1 ? (parts[0]?.[0] ?? "") + (parts[1]?.[0] ?? "") : name.slice(0, 2);
  return letters.toUpperCase();
}
