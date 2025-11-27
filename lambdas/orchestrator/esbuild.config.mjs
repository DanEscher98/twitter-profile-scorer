import * as esbuild from "esbuild";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

await esbuild.build({
  entryPoints: ["src/handler.ts"],
  bundle: true,
  platform: "node",
  target: "node20",
  outfile: "dist/handler.js",
  format: "cjs",
  sourcemap: true,
  external: ["@aws-sdk/client-lambda", "@aws-sdk/client-sqs"],
});

// Copy RDS CA certificate to dist folder
const certSrc = path.join(__dirname, "../../certs/aws-rds-global-bundle.pem");
const certDst = path.join(__dirname, "dist/aws-rds-global-bundle.pem");
if (fs.existsSync(certSrc)) {
  fs.copyFileSync(certSrc, certDst);
  console.log("Copied RDS CA certificate to dist/");
}

console.log("Build complete: dist/handler.js");
