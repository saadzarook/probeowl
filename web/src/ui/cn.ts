// Author: @SaadZarook
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** Joins class names, a later Tailwind class replacing an earlier one of the same kind. */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

/** The control's box, the same for every input and select: 40 px, one border, one radius (rule 2). */
export const CONTROL =
  "m-0 box-border h-10 w-full min-w-0 rounded-md border border-input bg-card px-3 py-0 text-sm text-foreground " +
  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring " +
  "disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-production-text";
