import { drizzle } from "drizzle-orm/node-postgres";
import { Pool } from "pg";

import * as schema from "./schema";

let pool: Pool | null = null;

export function getDb() {
  if (!pool) {
    const connectionString = process.env.DATABASE_URL;
    if (!connectionString) {
      throw new Error("DATABASE_URL environment variable is required");
    }
    pool = new Pool({
      connectionString,
      max: 1, // Lambda best practice: single connection
      idleTimeoutMillis: 120000,
      connectionTimeoutMillis: 10000,
      ssl: {
        rejectUnauthorized: true, // Verify SSL certificate (AWS Lambda has RDS CA built-in)
      },
    });
  }
  return drizzle(pool, { schema });
}

export type Database = ReturnType<typeof getDb>;
