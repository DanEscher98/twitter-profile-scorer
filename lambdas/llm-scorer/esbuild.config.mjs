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
  external: [],
});

// Copy RDS CA certificate to dist folder
const certSrc = path.join(__dirname, "../../certs/aws-rds-global-bundle.pem");
const certDst = path.join(__dirname, "dist/aws-rds-global-bundle.pem");
if (fs.existsSync(certSrc)) {
  fs.copyFileSync(certSrc, certDst);
  console.log("Copied RDS CA certificate to dist/");
}

// Copy audience config files to dist folder
const audiencesSrc = path.join(__dirname, "src/audiences");
const audiencesDst = path.join(__dirname, "dist/audiences");
if (fs.existsSync(audiencesSrc)) {
  if (!fs.existsSync(audiencesDst)) {
    fs.mkdirSync(audiencesDst, { recursive: true });
  }
  const files = fs.readdirSync(audiencesSrc);
  for (const file of files) {
    if (file.endsWith(".json")) {
      fs.copyFileSync(path.join(audiencesSrc, file), path.join(audiencesDst, file));
    }
  }
  console.log(`Copied ${files.filter(f => f.endsWith('.json')).length} audience config(s) to dist/audiences/`);
}

console.log("Build complete: dist/handler.js");
