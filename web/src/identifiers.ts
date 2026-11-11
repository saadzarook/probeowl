// Author: @SaadZarook
import type { Entity } from "./api/client";

/** The identifier whose pattern matches the value, or undefined when none does. Identifiers without a pattern are never detected. */
export function detectIdentifier(entity: Entity, value: string): string | undefined {
  const text = value.trim();
  if (text === "") return undefined;
  return Object.entries(entity.identifiers ?? {}).find(([, identifier]) => {
    return identifier.pattern !== undefined && identifier.pattern !== null && new RegExp(identifier.pattern).test(text);
  })?.[0];
}
