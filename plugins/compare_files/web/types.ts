// Author: @SaadZarook
import type { components } from "@probeowl/plugin-web";

export type CompareRequest = components["schemas"]["CompareRequest"];
export type CompareResult = components["schemas"]["CompareResult"];
export type CompareOptions = components["schemas"]["CompareOptions"];
export type FileDifference = components["schemas"]["FileDifference"];
export type Range = components["schemas"]["Range"];

/** A file the person supplied, held in this page only. */
export interface Supplied {
  name: string;
  text: string;
}

/** What was compared with what came back: Reveal reads these texts, never the ones edited since. */
export interface Compared {
  left: Supplied;
  right: Supplied;
  result: CompareResult;
}
