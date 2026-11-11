// Author: @SaadZarook
import type { KillSwitch, KillTarget } from "./api/client";

/** What a kill switch covers, in words. */
export function targetText(target: KillTarget): string {
  if (target.scope === "global") return "every connector, in every application";
  if (target.scope === "application") return `every connector of ${target.application ?? ""}`;
  return `connector ${target.connector ?? ""} of ${target.application ?? ""}, in every environment`;
}

function sameTarget(a: KillTarget, b: KillTarget): boolean {
  return a.scope === b.scope && (a.application ?? null) === (b.application ?? null) && (a.connector ?? null) === (b.connector ?? null);
}

/** The switch on exactly this target, if it is on. */
export function switchOn(switches: KillSwitch[], target: KillTarget): KillSwitch | undefined {
  return switches.find((s) => sameTarget(s, target));
}

/** A broader switch that also stops this connector: turning its own off would not resume it. */
export function broaderSwitch(switches: KillSwitch[], application: string): KillSwitch | undefined {
  return switches.find((s) => s.scope === "global" || (s.scope === "application" && s.application === application));
}
