// Author: @SaadZarook

/** A connector type the Add connection form offers. */
export type ConnectionType = "sqlserver" | "oracle" | "mariadb" | "rest";

/** What a pasted connection string says; every field may be missing. */
export interface ParsedConnection {
  type?: ConnectionType;
  host?: string;
  port?: number;
  database?: string; // SQL Server and MariaDB: the database; Oracle: the service name
  user?: string;
  password?: string; // moved to the password field, never kept with the string
  baseUrl?: string; // REST
  encrypt?: boolean;
  trustServerCertificate?: boolean;
}

export const DEFAULT_PORTS: Record<Exclude<ConnectionType, "rest">, number> = { sqlserver: 1433, oracle: 1521, mariadb: 3306 };

const SCHEMES: Record<string, ConnectionType> = {
  sqlserver: "sqlserver",
  mssql: "sqlserver",
  mariadb: "mariadb",
  mysql: "mariadb",
  oracle: "oracle",
};

/**
 * Read a JDBC, ODBC/ADO or URL connection string into its parts, in the browser: the string itself
 * is never sent or stored. Undefined when it isn't one of these forms.
 */
export function parseConnectionString(text: string): ParsedConnection | undefined {
  const s = text.trim();
  if (s === "") return undefined;
  if (/^https?:\/\//i.test(s)) return parseRest(s);
  if (/^jdbc:oracle:thin:/i.test(s)) return parseOracleThin(s.replace(/^jdbc:oracle:thin:/i, ""));
  if (/^jdbc:sqlserver:\/\//i.test(s)) return parseJdbcSqlServer(s.replace(/^jdbc:sqlserver:\/\//i, ""));
  const url = /^(?:jdbc:)?([a-z][a-z0-9+]*):\/\//i.exec(s);
  if (url) {
    const type = SCHEMES[(url[1] ?? "").toLowerCase().replace(/\+.*$/, "")];
    return type === undefined ? undefined : parseUrl(s.replace(/^jdbc:/i, ""), type);
  }
  if (s.includes("=") && s.includes(";")) return parseKeyValues(s);
  return undefined;
}

function parseRest(s: string): ParsedConnection | undefined {
  try {
    const url = new URL(s);
    const result: ParsedConnection = { type: "rest", baseUrl: `${url.protocol}//${url.host}${url.pathname.replace(/\/+$/, "")}` };
    // Credentials in a URL are a password like any other: moved out, never part of the base URL.
    if (url.password) result.password = decodeURIComponent(url.password);
    return result;
  } catch {
    return undefined;
  }
}

function parseUrl(s: string, type: ConnectionType): ParsedConnection | undefined {
  // URL can't parse an unknown scheme's host reliably everywhere: read it as http.
  let url: URL;
  try {
    url = new URL(s.replace(/^[a-z][a-z0-9+]*:\/\//i, "http://"));
  } catch {
    return undefined;
  }
  const result: ParsedConnection = { type };
  if (url.hostname) result.host = url.hostname.replace(/^\[|\]$/g, "");
  if (url.port) result.port = Number(url.port);
  const path = decodeURIComponent(url.pathname.replace(/^\/+/, "").split("/")[0] ?? "");
  if (path) result.database = path;
  if (url.username) result.user = decodeURIComponent(url.username);
  if (url.password) result.password = decodeURIComponent(url.password);
  const query = url.searchParams;
  const user = query.get("user") ?? query.get("username");
  if (user) result.user = user;
  const password = query.get("password");
  if (password) result.password = password;
  const ssl = query.get("ssl") ?? query.get("useSSL") ?? query.get("sslMode") ?? query.get("sslmode");
  if (ssl && /^(false|disable|disabled|0)$/i.test(ssl)) result.encrypt = false;
  return result;
}

/** jdbc:sqlserver://host:port;databaseName=x;user=y;password=z;encrypt=true */
function parseJdbcSqlServer(rest: string): ParsedConnection {
  const [server = "", ...pairs] = splitPairs(rest, true);
  const result: ParsedConnection = { type: "sqlserver", ...hostAndPort(server.replace(/\\[^:]*/, "")) };
  return { ...result, ...fromPairs(pairs) };
}

/** jdbc:oracle:thin:[user/password]@//host:port/service, or @host:port:SID, or @host:port/service */
function parseOracleThin(rest: string): ParsedConnection {
  const result: ParsedConnection = { type: "oracle" };
  const at = rest.lastIndexOf("@");
  const credentials = at >= 0 ? rest.slice(0, at) : "";
  const target = (at >= 0 ? rest.slice(at + 1) : rest).replace(/^\/\//, "");
  if (credentials) {
    const [user, password] = credentials.split("/");
    if (user) result.user = user;
    if (password) result.password = password;
  }
  const match = /^([^:/]+)(?::(\d+))?(?:[/:](.+))?$/.exec(target);
  if (match) {
    if (match[1]) result.host = match[1];
    if (match[2]) result.port = Number(match[2]);
    if (match[3]) result.database = match[3];
  }
  return result;
}

/** ODBC and ADO.NET: Server=tcp:host,1433;Database=x;Uid=y;Pwd=z, or Data Source=…;Initial Catalog=… */
function parseKeyValues(s: string): ParsedConnection | undefined {
  const pairs = splitPairs(s);
  const values = new Map(pairs.map((p) => {
    const i = p.indexOf("=");
    return [p.slice(0, i).trim().toLowerCase(), p.slice(i + 1).trim().replace(/^\{(.*)\}$/, "$1")] as const;
  }));
  const driver = values.get("driver") ?? values.get("provider") ?? "";
  const type: ConnectionType = /maria|mysql/i.test(driver) ? "mariadb" : /oracle/i.test(driver) ? "oracle" : "sqlserver";
  const server = values.get("server") ?? values.get("data source") ?? values.get("address") ?? values.get("host");
  if (server === undefined && values.get("database") === undefined && values.get("initial catalog") === undefined) return undefined;
  const result: ParsedConnection = { type, ...hostAndPort((server ?? "").replace(/^tcp:/i, "")) };
  const port = values.get("port");
  if (port && /^\d+$/.test(port)) result.port = Number(port);
  return { ...result, ...fromPairs(pairs) };
}

/** Split on ;, except inside {…}, where ODBC quotes a value that holds one: Pwd={p;w}. */
function splitPairs(s: string, keepFirst = false): string[] {
  const parts: string[] = [];
  let current = "";
  let braces = 0;
  for (const ch of s) {
    if (ch === "{") braces += 1;
    else if (ch === "}" && braces > 0) braces -= 1;
    if (ch === ";" && braces === 0) {
      parts.push(current);
      current = "";
    } else current += ch;
  }
  parts.push(current);
  return parts.filter((p, i) => (keepFirst && i === 0) || p.includes("="));
}

/** host, host:port or host,port (SQL Server's form) */
function hostAndPort(server: string): ParsedConnection {
  const match = /^([^,:]+)(?:[,:](\d+))?$/.exec(server.trim());
  if (!match || !match[1]) return {};
  return { host: match[1], ...(match[2] ? { port: Number(match[2]) } : {}) };
}

function fromPairs(pairs: string[]): ParsedConnection {
  const result: ParsedConnection = {};
  for (const pair of pairs) {
    const i = pair.indexOf("=");
    if (i < 0) continue;
    const key = pair.slice(0, i).trim().toLowerCase();
    const value = pair.slice(i + 1).trim().replace(/^\{(.*)\}$/, "$1");
    if (["database", "databasename", "initial catalog"].includes(key)) result.database = value;
    else if (["user", "user id", "uid", "username"].includes(key)) result.user = value;
    else if (["password", "pwd"].includes(key)) result.password = value;
    else if (key === "encrypt") result.encrypt = !/^(false|no|optional)$/i.test(value);
    else if (key === "trustservercertificate" || key === "trust server certificate") result.trustServerCertificate = /^(true|yes)$/i.test(value);
  }
  return result;
}
