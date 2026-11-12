// Author: @SaadZarook

// Words that name one environment, as owl init strips them (cli/init.py): "Card access UAT" is card_access.
const ENVIRONMENT_WORDS = new Set([
  "prod", "production", "prd", "live", "uat", "test", "tst", "qa", "sit", "stg", "staging",
  "preprod", "dev", "development", "local",
]);

/** The id an application's name suggests: lower case, words joined by _, environment words left out. */
export function applicationId(name: string): string {
  const words = name
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .filter((w) => w !== "");
  const kept = words.filter((w) => !ENVIRONMENT_WORDS.has(w));
  const id = (kept.length > 0 ? kept : words).join("_").replace(/^[^a-z]+/, "");
  return id.slice(0, 64);
}

/** Why an id can't name an application, or undefined: a-z, 0-9 and _, starting with a letter. */
export function applicationIdProblem(id: string, taken: string[]): string | undefined {
  if (id === "") return "give it a name: the id names its folder and appears in every URL";
  if (!/^[a-z][a-z0-9_]*$/.test(id)) return "use a-z, 0-9 and _, starting with a letter";
  return taken.includes(id) ? `an application named ${id} already exists` : undefined;
}
