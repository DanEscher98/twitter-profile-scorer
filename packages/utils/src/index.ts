/**
 * Shared logger for AWS Lambda CloudWatch
 *
 * Outputs structured JSON logs optimized for CloudWatch:
 * - No timestamps (CloudWatch adds them automatically)
 * - No ANSI color codes (not rendered in CloudWatch console)
 * - Flat JSON structure for CloudWatch Insights queries
 *
 * Usage:
 *   import { createLogger } from "@profile-scorer/utils";
 *   const log = createLogger("my-service");
 *   log.info("message", { key: "value" });
 *
 * Output in CloudWatch:
 *   {"level":"info","service":"my-service","message":"message","key":"value"}
 */

export type LogLevel = "debug" | "info" | "warn" | "error";

export interface LogMeta {
  [key: string]: unknown;
}

export interface Logger {
  debug: (message: string, meta?: LogMeta) => void;
  info: (message: string, meta?: LogMeta) => void;
  warn: (message: string, meta?: LogMeta) => void;
  error: (message: string, meta?: LogMeta) => void;
}

const LOG_LEVELS: Record<LogLevel, number> = {
  debug: 0,
  info: 1,
  warn: 2,
  error: 3,
};

function getConfiguredLevel(): LogLevel {
  const env = process.env.LOG_LEVEL?.toLowerCase();
  if (env && env in LOG_LEVELS) {
    return env as LogLevel;
  }
  return "info";
}

function shouldLog(level: LogLevel): boolean {
  const configuredLevel = getConfiguredLevel();
  return LOG_LEVELS[level] >= LOG_LEVELS[configuredLevel];
}

function formatLog(level: LogLevel, service: string, message: string, meta?: LogMeta): string {
  const entry: Record<string, unknown> = {
    level,
    service,
    message,
  };

  if (meta) {
    // Flatten meta into the log entry
    for (const [key, value] of Object.entries(meta)) {
      // Handle Error objects specially
      if (value instanceof Error) {
        entry[key] = {
          name: value.name,
          message: value.message,
          stack: value.stack,
        };
      } else {
        entry[key] = value;
      }
    }
  }

  return JSON.stringify(entry);
}

/**
 * Create a logger instance for a specific service
 *
 * @param service - Service name to include in all log entries
 * @returns Logger instance with debug, info, warn, error methods
 */
export function createLogger(service: string): Logger {
  return {
    debug: (message: string, meta?: LogMeta) => {
      if (shouldLog("debug")) {
        console.log(formatLog("debug", service, message, meta));
      }
    },
    info: (message: string, meta?: LogMeta) => {
      if (shouldLog("info")) {
        console.log(formatLog("info", service, message, meta));
      }
    },
    warn: (message: string, meta?: LogMeta) => {
      if (shouldLog("warn")) {
        console.warn(formatLog("warn", service, message, meta));
      }
    },
    error: (message: string, meta?: LogMeta) => {
      if (shouldLog("error")) {
        console.error(formatLog("error", service, message, meta));
      }
    },
  };
}

// Default export for convenience
export default createLogger;
