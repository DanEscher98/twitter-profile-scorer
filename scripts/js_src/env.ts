/**
 * Load environment variables from root .env file.
 * Import this at the top of any script that needs env vars.
 */

import * as fs from "fs";
import * as path from "path";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Root directory (scripts/js_src -> scripts -> root)
const rootDir = path.resolve(__dirname, "..", "..");
const envPath = path.join(rootDir, ".env");

if (fs.existsSync(envPath)) {
  const content = fs.readFileSync(envPath, "utf-8");

  for (const line of content.split("\n")) {
    // Skip empty lines and comments
    if (!line.trim() || line.startsWith("#")) continue;

    const match = line.match(/^([^=]+)=(.*)$/);
    if (match && match[1] && match[2]) {
      const key = match[1].trim();
      let value = match[2].trim();

      // Remove surrounding quotes if present
      if ((value.startsWith('"') && value.endsWith('"')) ||
          (value.startsWith("'") && value.endsWith("'"))) {
        value = value.slice(1, -1);
      }

      // Only set if not already defined (don't override explicit env vars)
      if (!process.env[key]) {
        process.env[key] = value;
      }
    }
  }
}

// Resolve RDS_CA_CERT to full path and set RDS_CA_PATH for the db package
if (process.env.RDS_CA_CERT && !process.env.RDS_CA_PATH) {
  const certPath = path.isAbsolute(process.env.RDS_CA_CERT)
    ? process.env.RDS_CA_CERT
    : path.join(rootDir, "certs", process.env.RDS_CA_CERT);
  if (fs.existsSync(certPath)) {
    process.env.RDS_CA_PATH = certPath;
  }
}
