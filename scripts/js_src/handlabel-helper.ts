#!/usr/bin/env tsx
/**
 * Hand-labeling TUI helper for profile scoring.
 *
 * Loads batch files from scripts/dataset/batches/ and their corresponding
 * haiku model responses, allowing manual review and labeling.
 *
 * Controls:
 *   1 - Label as TRUE
 *   2 - Label as FALSE
 *   3 - Label as NULL (uncertain)
 *   Enter - Save current label and move to next profile
 *   Tab - Focus on reason editor
 *   Ctrl+S - Save progress to CSV
 *   q / Ctrl+C - Quit (prompts to save)
 *
 * Output: scripts/dataset/hand_picked.csv
 *
 * Usage:
 *   yarn tsx js_src/handlabel-helper.ts
 */
import blessed from "blessed";
import * as fs from "fs";
import * as path from "path";
import { fileURLToPath } from "url";

import { decode } from "@toon-format/toon";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const DATASET_DIR = path.join(__dirname, "..", "dataset");
const BATCHES_DIR = path.join(DATASET_DIR, "batches");
const OUTPUT_CSV = path.join(DATASET_DIR, "hand_picked.csv");

// ============================================================================
// Types
// ============================================================================

interface ProfileData {
  handle: string;
  name: string;
  bio: string;
  category: string | null;
  followers: number;
}

interface HaikuLabel {
  handle: string;
  label: boolean | null;
  reason: string;
}

interface LabeledProfile extends ProfileData {
  modelLabel: boolean | null;
  modelReason: string;
  humanLabel?: boolean | null;
  humanReason?: string;
}

interface Stats {
  total: number;
  labeled: number;
  trueCount: number;
  falseCount: number;
  nullCount: number;
}

// ============================================================================
// State
// ============================================================================

let profiles: LabeledProfile[] = [];
let currentIndex = 0;
let stats: Stats = { total: 0, labeled: 0, trueCount: 0, falseCount: 0, nullCount: 0 };
let currentLabel: boolean | null = null;
let currentReason: string = "";
let unsavedChanges = false;
let isEditingReason = false;

// TUI elements
let screen: blessed.Widgets.Screen;
let statsBox: blessed.Widgets.BoxElement;
let progressBar: blessed.Widgets.ProgressBarElement;
let dataBox: blessed.Widgets.BoxElement;
let reasonEditor: blessed.Widgets.TextareaElement;
let labelButtons: blessed.Widgets.BoxElement;
let helpBox: blessed.Widgets.BoxElement;

// ============================================================================
// Data Loading
// ============================================================================

/**
 * Parse TOON file manually (simple format parsing).
 */
function parseToonFile(content: string): ProfileData[] {
  const lines = content.trim().split("\n");
  const profiles: ProfileData[] = [];

  // Skip header line
  for (let i = 1; i < lines.length; i++) {
    const line = lines[i].trim();
    if (!line) continue;

    // Parse CSV-like format with possible quoted fields
    const fields = parseCSVLine(line);
    if (fields.length >= 5) {
      profiles.push({
        handle: fields[0].trim(),
        name: fields[1].trim(),
        bio: fields[2].trim(),
        category: fields[3].trim() === "null" ? null : fields[3].trim(),
        followers: parseInt(fields[4].trim(), 10) || 0,
      });
    }
  }

  return profiles;
}

/**
 * Parse a CSV line handling quoted fields.
 */
function parseCSVLine(line: string): string[] {
  const fields: string[] = [];
  let current = "";
  let inQuotes = false;

  for (let i = 0; i < line.length; i++) {
    const char = line[i];

    if (char === '"') {
      if (inQuotes && line[i + 1] === '"') {
        current += '"';
        i++;
      } else {
        inQuotes = !inQuotes;
      }
    } else if (char === "," && !inQuotes) {
      fields.push(current);
      current = "";
    } else {
      current += char;
    }
  }
  fields.push(current);

  return fields;
}

/**
 * Load all batch files and their corresponding haiku labels.
 */
function loadBatches(): LabeledProfile[] {
  const allProfiles: LabeledProfile[] = [];
  const batchFiles = fs.readdirSync(BATCHES_DIR).filter((f) => f.startsWith("batch-") && f.endsWith(".toon"));

  for (const batchFile of batchFiles.sort()) {
    const batchNum = batchFile.match(/batch-(\d+)\.toon/)?.[1];
    if (!batchNum) continue;

    // Try both naming conventions for haiku files
    const haikuFile1 = `haiku-${batchNum}.json`;
    const haikuFile2 = `haiku.${batchNum}.json`;
    let haikuPath = path.join(BATCHES_DIR, haikuFile1);
    if (!fs.existsSync(haikuPath)) {
      haikuPath = path.join(BATCHES_DIR, haikuFile2);
    }

    if (!fs.existsSync(haikuPath)) {
      // Skip batches without haiku labels
      continue;
    }

    // Load haiku labels first to check if valid
    const haikuContent = fs.readFileSync(haikuPath, "utf-8").trim();
    if (!haikuContent || haikuContent.length === 0) {
      // Skip empty haiku files
      continue;
    }

    let haikuLabels: HaikuLabel[];
    try {
      haikuLabels = JSON.parse(haikuContent);
    } catch {
      // Skip invalid JSON
      continue;
    }

    if (!Array.isArray(haikuLabels) || haikuLabels.length === 0) {
      continue;
    }

    // Load batch profiles
    const batchPath = path.join(BATCHES_DIR, batchFile);
    const batchContent = fs.readFileSync(batchPath, "utf-8");
    const batchProfiles = parseToonFile(batchContent);

    // Create lookup map
    const labelMap = new Map<string, HaikuLabel>();
    for (const label of haikuLabels) {
      labelMap.set(label.handle.toLowerCase(), label);
    }

    // Match profiles with labels
    for (const profile of batchProfiles) {
      const haikuLabel = labelMap.get(profile.handle.toLowerCase());
      if (haikuLabel) {
        allProfiles.push({
          ...profile,
          modelLabel: haikuLabel.label,
          modelReason: haikuLabel.reason,
        });
      }
    }
  }

  return allProfiles;
}

/**
 * Load existing progress from CSV.
 */
function loadProgress(): Map<string, { label: boolean | null; reason: string }> {
  const progress = new Map<string, { label: boolean | null; reason: string }>();

  if (!fs.existsSync(OUTPUT_CSV)) {
    return progress;
  }

  const content = fs.readFileSync(OUTPUT_CSV, "utf-8");
  const lines = content.trim().split("\n");

  // Skip header
  for (let i = 1; i < lines.length; i++) {
    const fields = parseCSVLine(lines[i]);
    if (fields.length >= 3) {
      const handle = fields[0].trim();
      const labelStr = fields[1].trim().toLowerCase();
      const reason = fields[2].trim();

      let label: boolean | null = null;
      if (labelStr === "true") label = true;
      else if (labelStr === "false") label = false;

      progress.set(handle.toLowerCase(), { label, reason });
    }
  }

  return progress;
}

/**
 * Save progress to CSV.
 */
function saveProgress() {
  const lines = ["HANDLE,LABEL,REASON"];

  for (const profile of profiles) {
    if (profile.humanLabel !== undefined) {
      const labelStr = profile.humanLabel === null ? "null" : String(profile.humanLabel);
      const reason = (profile.humanReason ?? "").replace(/"/g, '""');
      lines.push(`${profile.handle},${labelStr},"${reason}"`);
    }
  }

  fs.writeFileSync(OUTPUT_CSV, lines.join("\n") + "\n", "utf-8");
  unsavedChanges = false;
}

// ============================================================================
// TUI Setup
// ============================================================================

function initTUI() {
  screen = blessed.screen({
    smartCSR: true,
    title: "Hand Label Helper",
    fullUnicode: true,
    grabKeys: false,
    warnings: false,
  });

  // Stats box (top left)
  statsBox = blessed.box({
    parent: screen,
    top: 0,
    left: 0,
    width: "30%",
    height: 10,
    border: { type: "line" },
    label: " Stats ",
    tags: true,
    style: {
      border: { fg: "cyan" },
      fg: "white",
    },
  });

  // Progress bar (top right)
  progressBar = blessed.progressbar({
    parent: screen,
    top: 0,
    left: "30%",
    width: "70%",
    height: 3,
    border: { type: "line" },
    style: {
      fg: "white",
      bg: "default",
      bar: { bg: "green" },
      border: { fg: "cyan" },
    },
    ch: "█",
    filled: 0,
    label: " Progress ",
  });

  // Help box (below progress)
  helpBox = blessed.box({
    parent: screen,
    top: 3,
    left: "30%",
    width: "70%",
    height: 7,
    border: { type: "line" },
    label: " Controls ",
    tags: true,
    style: {
      border: { fg: "yellow" },
      fg: "gray",
    },
    content: "{bold}1{/bold}=TRUE  {bold}2{/bold}=FALSE  {bold}3{/bold}=NULL  |  {bold}Enter{/bold}=Save & Next  |  {bold}Up/Down{/bold}=Navigate  |  {bold}Tab{/bold}=Edit  |  {bold}Ctrl+S{/bold}=Save  |  {bold}q{/bold}=Quit",
  });

  // Data box (profile info)
  dataBox = blessed.box({
    parent: screen,
    top: 10,
    left: 0,
    width: "100%",
    height: 12,
    border: { type: "line" },
    label: " Profile Data ",
    tags: true,
    scrollable: true,
    style: {
      border: { fg: "blue" },
      fg: "white",
    },
  });

  // Reason editor - use textarea for editing
  reasonEditor = blessed.textarea({
    parent: screen,
    top: 22,
    left: 0,
    width: "100%",
    height: 6,
    border: { type: "line" },
    label: " Reason (Tab to edit, Esc to exit) - Note: Use Backspace to edit ",
    tags: true,
    inputOnFocus: false,
    keys: true,
    mouse: true,
    scrollable: true,
    style: {
      border: { fg: "white" },
      fg: "white",
    },
  });

  // Label buttons
  labelButtons = blessed.box({
    parent: screen,
    top: 28,
    left: 0,
    width: "100%",
    height: 5,
    border: { type: "line" },
    label: " Label Selection ",
    tags: true,
    style: {
      border: { fg: "blue" },
    },
  });

  // Setup key bindings
  setupKeyBindings();

  screen.render();
}

function setupKeyBindings() {
  // Global keys (work when not editing)
  screen.key(["1"], () => {
    if (!isEditingReason) {
      setLabel(true);
    }
  });

  screen.key(["2"], () => {
    if (!isEditingReason) {
      setLabel(false);
    }
  });

  screen.key(["3"], () => {
    if (!isEditingReason) {
      setLabel(null);
    }
  });

  screen.key(["enter", "return"], () => {
    if (!isEditingReason) {
      saveCurrentAndNext();
    }
  });

  screen.key(["tab"], () => {
    if (isEditingReason) {
      // Exit edit mode
      currentReason = reasonEditor.getValue();
      reasonEditor.cancel();
      isEditingReason = false;
      updateEditorBorder();
    } else {
      // Enter edit mode
      isEditingReason = true;
      updateEditorBorder();
      reasonEditor.focus();
      reasonEditor.readInput();
    }
  });

  screen.key(["escape"], () => {
    if (isEditingReason) {
      currentReason = reasonEditor.getValue();
      reasonEditor.cancel();
      isEditingReason = false;
      updateEditorBorder();
    }
  });

  screen.key(["C-s"], () => {
    if (!isEditingReason) {
      saveProgress();
      showMessage("Progress saved!");
    }
  });

  screen.key(["q", "C-c"], () => {
    if (isEditingReason) {
      return; // Don't quit while editing
    }
    if (unsavedChanges) {
      showConfirmQuit();
    } else {
      screen.destroy();
      process.exit(0);
    }
  });

  // Up/Down keys for profile navigation (only when not editing)
  screen.key(["up"], () => {
    if (!isEditingReason && currentIndex > 0) {
      currentIndex--;
      loadCurrentProfile();
    }
  });

  screen.key(["down"], () => {
    if (!isEditingReason && currentIndex < profiles.length - 1) {
      currentIndex++;
      loadCurrentProfile();
    }
  });

  // Reason editor events
  reasonEditor.on("submit", () => {
    currentReason = reasonEditor.getValue();
    isEditingReason = false;
    updateEditorBorder();
  });

  reasonEditor.on("cancel", () => {
    currentReason = reasonEditor.getValue();
    isEditingReason = false;
    updateEditorBorder();
  });

}

// ============================================================================
// UI Updates
// ============================================================================

function updateEditorBorder() {
  const color = isEditingReason ? "green" : "white";
  // Force border color update by setting both style properties
  (reasonEditor as any).style.border.fg = color;
  (reasonEditor as any).border.fg = color;
  reasonEditor.render();
  screen.render();
}

function updateStats() {
  const percent = stats.total > 0 ? Math.round((stats.labeled / stats.total) * 100) : 0;
  const remaining = stats.total - stats.labeled;

  statsBox.setContent(
    `{bold}Total Profiles:{/bold} ${stats.total}\n` +
      `{bold}Labeled:{/bold} ${stats.labeled}\n` +
      `{bold}Remaining:{/bold} ${remaining}\n` +
      `\n` +
      `{green-fg}{bold}TRUE:{/bold}  ${stats.trueCount}{/green-fg}\n` +
      `{red-fg}{bold}FALSE:{/bold} ${stats.falseCount}{/red-fg}\n` +
      `{yellow-fg}{bold}NULL:{/bold}  ${stats.nullCount}{/yellow-fg}`
  );

  progressBar.setProgress(percent);
  progressBar.setLabel(` Progress: ${percent}% (${stats.labeled}/${stats.total}) `);

  screen.render();
}

function updateDataBox() {
  // Clear the box first to prevent trailing characters
  dataBox.setContent("");

  if (profiles.length === 0) {
    dataBox.setContent("No profiles loaded.");
    return;
  }

  const profile = profiles[currentIndex];
  const indexDisplay = `[${currentIndex + 1}/${profiles.length}]`;
  const handPickedLabel = profile.humanLabel !== undefined ? " {green-fg}[HAND PICKED]{/green-fg}" : "";

  dataBox.setLabel(` Profile Data ${indexDisplay}${handPickedLabel} `);
  dataBox.setContent(
    `{bold}Handle:{/bold}    @${profile.handle}\n` +
      `{bold}Name:{/bold}      ${profile.name}\n` +
      `{bold}Category:{/bold}  ${profile.category ?? "null"}\n` +
      `{bold}Followers:{/bold} ${profile.followers.toLocaleString()}\n` +
      `\n` +
      `{bold}Bio:{/bold}\n${profile.bio || "(no bio)"}`
  );

  screen.render();
}

function updateReasonEditor() {
  const profile = profiles[currentIndex];
  if (!profile) return;

  // Use human reason if exists, otherwise model reason (lowercase as placeholder)
  currentReason = profile.humanReason ?? profile.modelReason.toLowerCase();
  reasonEditor.setValue(currentReason);
  screen.render();
}

function updateLabelButtons() {
  const trueStyle = currentLabel === true ? "{white-bg}{black-fg}" : "{green-fg}";
  const falseStyle = currentLabel === false ? "{white-bg}{black-fg}" : "{red-fg}";
  const nullStyle = currentLabel === null ? "{white-bg}{black-fg}" : "{yellow-fg}";

  const trueEnd = currentLabel === true ? "{/black-fg}{/white-bg}" : "{/green-fg}";
  const falseEnd = currentLabel === false ? "{/black-fg}{/white-bg}" : "{/red-fg}";
  const nullEnd = currentLabel === null ? "{/black-fg}{/white-bg}" : "{/yellow-fg}";

  const profile = profiles[currentIndex];
  const modelIndicator = (label: boolean | null) => {
    if (profile?.modelLabel === label) return " (model)";
    return "";
  };

  labelButtons.setContent(
    `\n` +
      `    ${trueStyle}[1] TRUE${modelIndicator(true)}${trueEnd}` +
      `        ${falseStyle}[2] FALSE${modelIndicator(false)}${falseEnd}` +
      `        ${nullStyle}[3] NULL${modelIndicator(null)}${nullEnd}`
  );

  screen.render();
}

function loadCurrentProfile() {
  if (profiles.length === 0) return;

  const profile = profiles[currentIndex];

  // Set label to human label if exists, otherwise model label
  currentLabel = profile.humanLabel !== undefined ? profile.humanLabel : profile.modelLabel;

  updateDataBox();
  updateReasonEditor();
  updateLabelButtons();
  updateStats();
}

function setLabel(label: boolean | null) {
  currentLabel = label;
  updateLabelButtons();
}

function saveCurrentAndNext() {
  if (profiles.length === 0) return;

  const profile = profiles[currentIndex];
  const wasLabeled = profile.humanLabel !== undefined;

  // Get current reason from editor and trim
  currentReason = reasonEditor.getValue().trim();

  // Update profile
  const oldLabel = profile.humanLabel;
  profile.humanLabel = currentLabel;
  profile.humanReason = currentReason;
  unsavedChanges = true;

  // Update stats
  if (!wasLabeled) {
    stats.labeled++;
  } else {
    // Remove old label from count
    if (oldLabel === true) stats.trueCount--;
    else if (oldLabel === false) stats.falseCount--;
    else stats.nullCount--;
  }

  // Add new label to count
  if (currentLabel === true) stats.trueCount++;
  else if (currentLabel === false) stats.falseCount++;
  else stats.nullCount++;

  // Move to next unlabeled profile
  moveToNextUnlabeled();

  updateStats();
}

function moveToNextUnlabeled() {
  // First, try to find next unlabeled after current
  for (let i = currentIndex + 1; i < profiles.length; i++) {
    if (profiles[i].humanLabel === undefined) {
      currentIndex = i;
      loadCurrentProfile();
      return;
    }
  }

  // If none found after, try from beginning
  for (let i = 0; i < currentIndex; i++) {
    if (profiles[i].humanLabel === undefined) {
      currentIndex = i;
      loadCurrentProfile();
      return;
    }
  }

  // All labeled, stay on current or go to next
  if (currentIndex < profiles.length - 1) {
    currentIndex++;
  }
  loadCurrentProfile();

  if (stats.labeled === stats.total) {
    showMessage("All profiles labeled! Press Ctrl+S to save.");
  }
}

function showMessage(msg: string) {
  const msgBox = blessed.message({
    parent: screen,
    top: "center",
    left: "center",
    width: "50%",
    height: 5,
    border: { type: "line" },
    style: {
      border: { fg: "green" },
      fg: "white",
      bg: "blue",
    },
  });

  msgBox.display(msg, 2, () => {
    msgBox.destroy();
    screen.render();
  });
}

function showConfirmQuit() {
  const confirmBox = blessed.question({
    parent: screen,
    top: "center",
    left: "center",
    width: "60%",
    height: 7,
    border: { type: "line" },
    style: {
      border: { fg: "red" },
      fg: "white",
      bg: "black",
    },
  });

  confirmBox.ask("You have unsaved changes. Save before quitting? (y/n/c)", (err, value) => {
    confirmBox.destroy();
    if (value === true || value === "y" || value === "Y") {
      saveProgress();
      screen.destroy();
      process.exit(0);
    } else if (value === false || value === "n" || value === "N") {
      screen.destroy();
      process.exit(0);
    }
    // Cancel - just close dialog
    screen.render();
  });
}

// ============================================================================
// Main
// ============================================================================

async function main() {
  console.log("Loading batch files...");

  // Load all profiles with haiku labels
  profiles = loadBatches();

  if (profiles.length === 0) {
    console.error("No profiles found with haiku labels in", BATCHES_DIR);
    process.exit(1);
  }

  console.log(`Loaded ${profiles.length} profiles with haiku labels`);

  // Load existing progress
  const progress = loadProgress();
  let startIndex = 0;

  for (let i = 0; i < profiles.length; i++) {
    const saved = progress.get(profiles[i].handle.toLowerCase());
    if (saved) {
      profiles[i].humanLabel = saved.label;
      profiles[i].humanReason = saved.reason;

      // Update stats
      stats.labeled++;
      if (saved.label === true) stats.trueCount++;
      else if (saved.label === false) stats.falseCount++;
      else stats.nullCount++;
    } else if (startIndex === 0 || profiles[i].humanLabel === undefined) {
      // Find first unlabeled
      if (startIndex === 0 && i > 0) startIndex = i;
    }
  }

  // Find actual first unlabeled
  for (let i = 0; i < profiles.length; i++) {
    if (profiles[i].humanLabel === undefined) {
      startIndex = i;
      break;
    }
  }

  stats.total = profiles.length;
  currentIndex = startIndex;

  console.log(`Progress: ${stats.labeled}/${stats.total} labeled`);
  console.log("Starting TUI...");

  // Initialize TUI
  initTUI();

  // Load first/current profile
  loadCurrentProfile();
}

main().catch((err) => {
  console.error("Error:", err);
  process.exit(1);
});
