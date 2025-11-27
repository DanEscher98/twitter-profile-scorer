export function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export function normalizeString(input: string): string {
  return input
    .replace(/\\/g, '')           // remove backslashes
    .replace(/["']/g, '')         // remove quotes
    .replace(/,/g, '')            // remove commas
    .replace(/[\x00-\x1F\x7F]/g, '') // remove control/escape chars
    .trim();
}
