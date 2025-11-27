import * as esbuild from "esbuild";

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

console.log("Build complete: dist/handler.js");
