// Author: @SaadZarook
import { expect, test } from "@playwright/test";
import { parseConnectionString } from "../src/connectionString";

// Pure parsing, no page: the pasted string is read in the browser and never sent.
const cases: [string, ReturnType<typeof parseConnectionString>][] = [
  [
    "jdbc:sqlserver://db.example.test:1433;databaseName=wallet_uat;user=probeowl_ro;password=s3cret;encrypt=true;trustServerCertificate=true",
    { type: "sqlserver", host: "db.example.test", port: 1433, database: "wallet_uat", user: "probeowl_ro", password: "s3cret", encrypt: true, trustServerCertificate: true },
  ],
  [
    "Server=tcp:db.example.test,1433;Initial Catalog=wallet_uat;User ID=probeowl_ro;Password={p;w};Encrypt=True",
    { type: "sqlserver", host: "db.example.test", port: 1433, database: "wallet_uat", user: "probeowl_ro", password: "p;w", encrypt: true },
  ],
  [
    "Driver={MariaDB ODBC 3.1 Driver};Server=127.0.0.1;Port=3306;Database=access_uat;Uid=probeowl_ro;Pwd=pw",
    { type: "mariadb", host: "127.0.0.1", port: 3306, database: "access_uat", user: "probeowl_ro", password: "pw" },
  ],
  ["mariadb://probeowl_ro:pa%40ss@127.0.0.1:3306/access_uat", { type: "mariadb", host: "127.0.0.1", port: 3306, database: "access_uat", user: "probeowl_ro", password: "pa@ss" }],
  ["jdbc:mysql://db.example.test/access?user=ro&password=x&useSSL=false", { type: "mariadb", host: "db.example.test", database: "access", user: "ro", password: "x", encrypt: false }],
  ["jdbc:oracle:thin:@//ledger.example.test:1521/FREEPDB1", { type: "oracle", host: "ledger.example.test", port: 1521, database: "FREEPDB1" }],
  ["jdbc:oracle:thin:probeowl_ro/pw@ledger.example.test:1521:XE", { type: "oracle", host: "ledger.example.test", port: 1521, database: "XE", user: "probeowl_ro", password: "pw" }],
  ["https://api.example.test/v1/", { type: "rest", baseUrl: "https://api.example.test/v1" }],
  ["https://user:token@api.example.test/v1", { type: "rest", baseUrl: "https://api.example.test/v1", password: "token" }],
];

for (const [text, expected] of cases) {
  test(`parses ${text.slice(0, 40)}`, () => {
    expect(parseConnectionString(text)).toEqual(expected);
  });
}

test("anything else is not a connection string", () => {
  for (const text of ["", "hello", "postgres://x/y", "ftp://host/"]) expect(parseConnectionString(text)).toBeUndefined();
});
