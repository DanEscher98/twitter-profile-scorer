#!/usr/bin/env tsx
/**
 * Generate the system prompt from an audience config JSON file.
 *
 * Usage:
 *   just get-systemprompt scripts/data/thelai_customers.json
 *
 * Output:
 *   Writes to scripts/output/<config_name>_system_prompt.txt
 */
import * as fs from "fs";
import * as path from "path";
import { fileURLToPath } from "url";

import { AudienceConfig, generateSystemPrompt } from "@profile-scorer/llm-scoring";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Project root (scripts workspace is at <root>/scripts)
const projectRoot = path.resolve(__dirname, "../..");

function main() {
  const args = process.argv.slice(2);

  if (args.length === 0 || args.includes("--help") || args.includes("-h")) {
    console.log(`
Generate system prompt from audience config JSON.

Usage:
  just get-systemprompt <config-path>

Arguments:
  config-path    Path to audience config JSON file (relative to project root)

Example:
  just get-systemprompt scripts/data/thelai_customers.json

Output:
  Writes to scripts/output/<config_name>_system_prompt.txt
`);
    process.exit(args.includes("--help") || args.includes("-h") ? 0 : 1);
  }

  let configPath = args[0]!;

  // Resolve path relative to project root if not absolute
  if (!path.isAbsolute(configPath)) {
    configPath = path.resolve(projectRoot, configPath);
  }

  if (!fs.existsSync(configPath)) {
    console.error(`Error: Config file not found: ${configPath}`);
    process.exit(1);
  }

  // Load config
  const configContent = fs.readFileSync(configPath, "utf-8");
  let config: AudienceConfig;
  try {
    config = JSON.parse(configContent) as AudienceConfig;
  } catch (e) {
    console.error(`Error: Invalid JSON in config file: ${configPath}`);
    process.exit(1);
  }

  // Generate system prompt
  const systemPrompt = generateSystemPrompt(config);

  // Create output directory
  const outputDir = path.join(__dirname, "..", "output");
  if (!fs.existsSync(outputDir)) {
    fs.mkdirSync(outputDir, { recursive: true });
  }

  // Get config name from filename
  const configName = path.basename(configPath, ".json");
  const outputPath = path.join(outputDir, `${configName}_system_prompt.md`);

  // Write to file
  fs.writeFileSync(outputPath, systemPrompt, "utf-8");

  console.log(`Generated system prompt for: ${configName}`);
  console.log(`Output: ${outputPath}`);
  console.log(`\n--- System Prompt Preview (first 500 chars) ---\n`);
  console.log(systemPrompt.slice(0, 500) + (systemPrompt.length > 500 ? "..." : ""));
  console.log(`\n--- Total length: ${systemPrompt.length} characters ---`);
}

main();
