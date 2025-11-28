import { drizzle } from "drizzle-orm/node-postgres";
import { Pool } from "pg";
import fs from "fs";
import path from "path";
import { createLogger } from "@profile-scorer/logger";

import * as schema from "./schema";

const log = createLogger("db-client");

let pool: Pool | null = null;

/**
 * NOTE: AWS RDS SSL Certificate Configuration
 *
 * AWS Lambda does NOT include RDS CA certificates in the Node.js runtime.
 * The `pg` driver requires explicit CA configuration for SSL verification.
 *
 * The certificate bundle (aws-rds-global-bundle.pem) is downloaded from:
 * https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem
 *
 * Each Lambda must bundle this certificate and set RDS_CA_PATH env var.
 * The esbuild config should copy the cert to the dist folder.
 */
function loadRdsCaCert(): string | undefined {
  // Check for explicit path via env var
  const envPath = process.env.RDS_CA_PATH;
  if (envPath && fs.existsSync(envPath)) {
    log.info("Loading RDS CA cert from env path", { path: envPath });
    return fs.readFileSync(envPath, "utf-8");
  }

  // Check common locations - Lambda extracts to /var/task/
  const possiblePaths = [
    "/var/task/aws-rds-global-bundle.pem", // Lambda runtime location
    path.join(__dirname, "aws-rds-global-bundle.pem"),
    path.join(__dirname, "..", "aws-rds-global-bundle.pem"),
    path.join(process.cwd(), "aws-rds-global-bundle.pem"),
  ];

  log.info("Searching for RDS CA cert", { searchPaths: possiblePaths, cwd: process.cwd(), dirname: __dirname });

  for (const p of possiblePaths) {
    if (fs.existsSync(p)) {
      log.info("Found RDS CA cert", { path: p });
      return fs.readFileSync(p, "utf-8");
    }
  }

  log.warn("RDS CA cert not found, falling back to insecure connection");
  return undefined;
}

export function getDb() {
  if (!pool) {
    let connectionString = process.env.DATABASE_URL;
    if (!connectionString) {
      throw new Error("DATABASE_URL environment variable is required");
    }

    // Remove sslmode from URL to avoid conflict with explicit ssl config
    const urlObj = new URL(connectionString);
    urlObj.searchParams.delete("sslmode");
    connectionString = urlObj.toString();

    const ca = loadRdsCaCert();

    const sslConfig = ca
      ? { rejectUnauthorized: true, ca }
      : { rejectUnauthorized: false };

    log.info("Creating DB connection pool", {
      hasCA: !!ca,
      caLength: ca?.length,
      rejectUnauthorized: sslConfig.rejectUnauthorized,
      host: urlObj.hostname
    });

    pool = new Pool({
      connectionString,
      max: 1, // Lambda best practice: single connection
      idleTimeoutMillis: 120000,
      connectionTimeoutMillis: 10000,
      ssl: sslConfig,
    });
  }
  return drizzle(pool, { schema });
}

export type Database = ReturnType<typeof getDb>;
